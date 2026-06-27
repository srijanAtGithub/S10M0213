"""
cowork_rag.py
-------------
Local RAG engine for Sicily Cowork.

Hybrid search architecture
--------------------------
  Stage 1 — TF-IDF (sklearn)
      Keyword pre-filter. Fully local, zero cost, zero latency.
      Scans all indexed chunks and returns top-N keyword matches.

  Stage 2 — ChromaDB (text-embedding-3-small)
      Semantic vector search. Understands meaning, not just words.
      Runs against the same corpus and returns top-N semantic matches.

  Stage 3 — Reciprocal Rank Fusion (RRF)
      Merges both ranked lists into a single, de-duplicated ranking.
      Neither layer alone wins — both inform the final order.

Storage layout (one directory per sandbox session)
--------------------------------------------------
  ~/.sicily/<encoded-sandbox-path>/rag-details/
      chroma/           ChromaDB persistent collection
      tfidf.pkl         Fitted TfidfVectorizer + sparse matrix + ID list
      registry.json     { "relative/path.pdf": "2024-01-15T10:30:00" }

Incremental updates
-------------------
  On every session start, Sicily walks the sandbox and compares each
  file's last-modified timestamp against the registry. Only new or
  changed files are re-indexed. Deleted files are removed from the store.
  Unchanged files are skipped entirely — startup is fast after the first run.
"""

import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from typing import Optional

import structlog
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

log = structlog.get_logger()


# Constants
CHUNK_SIZE        = 500   # characters per chunk
CHUNK_OVERLAP     = 50    # overlap between adjacent chunks
TOP_K             = 5     # final results returned to the LLM
TFIDF_CANDIDATES  = 50    # how many TF-IDF hits to feed into RRF
SEMANTIC_CANDIDATES = 50  # how many ChromaDB hits to feed into RRF
RRF_K             = 60    # RRF constant (standard value, do not change lightly)

# All file types that can be meaningfully indexed as text
INDEXABLE_EXTENSIONS: frozenset[str] = frozenset({
    # Plain documents / notes
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex",
    # Config / data interchange
    ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".env",
    # Web / markup
    ".html", ".htm", ".xml", ".css", ".scss",
    # Source code (treated as plain text — no special parsing)
    ".py", ".pyi",
    ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".sh", ".bash", ".zsh",
    ".rb", ".go", ".rs",
    ".java", ".kt", ".scala",
    ".c", ".cpp", ".h", ".hpp",
    ".cs", ".php", ".lua", ".r", ".sql",
    # Data / logs
    ".csv", ".tsv", ".log",
    # Binary formats with text extractors
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
})

# Directories to skip when walking the sandbox
SKIP_DIRS: set[str] = {
    ".venv", "venv", "env", ".env",
    "node_modules", "__pycache__", ".git",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
    ".tox", ".nox", ".idea", ".vscode",
}


# Module-level singleton
# Set once in cowork_session.py, read by the search_index tool in cowork_tools.py
_RAG_INSTANCE: Optional["SicilyRAG"] = None


def set_rag(instance: "SicilyRAG") -> None:
    global _RAG_INSTANCE
    _RAG_INSTANCE = instance


def get_rag() -> Optional["SicilyRAG"]:
    return _RAG_INSTANCE


# Path helpers
def _encode_sandbox_path(sandbox: Path) -> str:
    """
    Convert an absolute sandbox path to a safe, human-readable directory name.
    e.g.  /home/alice/projects/myapp  →  home-alice-projects-myapp

    Capped at 180 chars so the full ~/.sicily/<encoded>/rag-details path
    stays well within filesystem limits on every OS.
    """
    parts = [p for p in sandbox.parts if p not in ("", "/", "\\")]
    encoded = "-".join(parts).replace(" ", "_")
    return encoded[:180]


def _rag_dir(sandbox: Path) -> Path:
    """Return (and create) the RAG storage directory for this sandbox session."""
    encoded = _encode_sandbox_path(sandbox)
    path = Path.home() / ".sicily" / encoded / "rag-details"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Text extraction
def _extract_text(file_path: Path) -> Optional[str]:
    """
    Extract plain text from any supported file type.
    Mirrors the extraction logic already in cowork_tools._read_binary,
    kept here to avoid a circular import.
    Returns None on failure or unsupported type.
    """
    ext = file_path.suffix.lower()
    if ext not in INDEXABLE_EXTENSIONS:
        return None

    try:
        # Binary formats
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n\n".join(
                f"[Page {i + 1}]\n{t}" for i, t in enumerate(pages) if t.strip()
            )

        if ext in {".xlsx", ".xls"}:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheets = []
            for name in wb.sheetnames:
                ws = wb[name]
                rows = [
                    "\t".join(
                        "" if cell.value is None else str(cell.value)
                        for cell in row
                    )
                    for row in ws.iter_rows()
                ]
                sheets.append(f"[Sheet: {name}]\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(sheets)

        if ext in {".docx", ".doc"}:
            from docx import Document
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        # Plain text (all remaining supported extensions)
        return file_path.read_text(encoding="utf-8", errors="replace")

    except Exception as exc:
        log.warning("rag.extract_failed", path=str(file_path), error=str(exc))
        return None


# Core RAG class
class SicilyRAG:
    """
    Local hybrid RAG engine for a single Sicily session.

    Lifecycle
    ---------
    1. Instantiate with the sandbox root.
    2. Call index_session() once at startup (blocks until done).
    3. Call search(query) for every user question that needs file content.
    """

    def __init__(self, sandbox_root: Path) -> None:
        self.sandbox_root = sandbox_root.resolve()
        self.rag_dir      = _rag_dir(self.sandbox_root)

        # Paths
        self._chroma_dir    = self.rag_dir / "chroma"
        self._tfidf_path    = self.rag_dir / "tfidf.pkl"
        self._registry_path = self.rag_dir / "registry.json"

        # LangChain / ChromaDB
        self._embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        self._vectorstore = Chroma(
            collection_name="sicily_rag",
            embedding_function=self._embeddings,
            persist_directory=str(self._chroma_dir),
        )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            add_start_index=True,   # stored in metadata as "start_index"
        )

        # TF-IDF state
        self._tfidf_vectorizer: Optional[TfidfVectorizer] = None
        self._tfidf_matrix   = None
        self._tfidf_ids: list[str] = []  # row i  →  ChromaDB chunk id

        # File registry
        # { "relative/path/to/file.pdf": "2024-01-15T10:30:00" }
        self._registry: dict[str, str] = self._load_registry()


    # Registry
    def _load_registry(self) -> dict[str, str]:
        if self._registry_path.exists():
            try:
                return json.loads(self._registry_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}


    def _save_registry(self) -> None:
        self._registry_path.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


    # TF-IDF persistence
    def _save_tfidf(self) -> None:
        with open(self._tfidf_path, "wb") as fh:
            pickle.dump(
                {
                    "vectorizer": self._tfidf_vectorizer,
                    "matrix":     self._tfidf_matrix,
                    "ids":        self._tfidf_ids,
                },
                fh,
            )


    def _load_tfidf(self) -> bool:
        if not self._tfidf_path.exists():
            return False
        try:
            with open(self._tfidf_path, "rb") as fh:
                data = pickle.load(fh)
            self._tfidf_vectorizer = data["vectorizer"]
            self._tfidf_matrix     = data["matrix"]
            self._tfidf_ids        = data["ids"]
            return True
        except Exception:
            return False


    def _rebuild_tfidf(self) -> None:
        """
        Rebuild the TF-IDF index from scratch using whatever is
        currently stored in ChromaDB.  Called after any indexing change.
        """
        result = self._vectorstore.get(include=["documents"])
        docs = result.get("documents") or []
        ids  = result.get("ids") or []

        if not docs:
            # Nothing indexed yet — clear TF-IDF state
            self._tfidf_vectorizer = None
            self._tfidf_matrix     = None
            self._tfidf_ids        = []
            return

        vectorizer = TfidfVectorizer(
            strip_accents="unicode",
            analyzer="word",
            ngram_range=(1, 2),     # unigrams + bigrams (e.g. "Q3 budget")
            min_df=1,
            sublinear_tf=True,      # log(1+tf) dampens very frequent terms
        )
        matrix = vectorizer.fit_transform(docs)

        self._tfidf_vectorizer = vectorizer
        self._tfidf_matrix     = matrix
        self._tfidf_ids        = list(ids)
        self._save_tfidf()

    # Chunk ID
    @staticmethod
    def _chunk_id(rel_path: str, chunk_index: int) -> str:
        """
        Stable, deterministic ID for a chunk.
        Used as the ChromaDB document ID so re-indexing is idempotent.
        """
        raw = f"{rel_path}::{chunk_index}"
        return hashlib.md5(raw.encode()).hexdigest()


    # File-level indexing
    def _mtime_iso(self, file_path: Path) -> str:
        ts = file_path.stat().st_mtime
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


    def _should_reindex(self, file_path: Path) -> bool:
        rel = str(file_path.relative_to(self.sandbox_root))
        if rel not in self._registry:
            return True
        return self._mtime_iso(file_path) != self._registry[rel]


    def _delete_file_chunks(self, rel_path: str) -> None:
        """Remove all ChromaDB documents that belong to one file."""
        try:
            existing = self._vectorstore.get(
                where={"file_path": rel_path},
                include=[],
            )
            if existing["ids"]:
                self._vectorstore.delete(ids=existing["ids"])
        except Exception as exc:
            log.warning("rag.delete_chunks_failed", path=rel_path, error=str(exc))


    def _index_file(self, file_path: Path) -> int:
        """
        Index one file:
          1. Extract text.
          2. Delete any previously indexed chunks for this file.
          3. Split into chunks with character offsets.
          4. Convert character offsets → line numbers (1-based).
          5. Store in ChromaDB with full metadata.
          6. Update registry.

        Returns number of chunks stored (0 if skipped).
        """
        rel_path = str(file_path.relative_to(self.sandbox_root))

        text = _extract_text(file_path)
        if not text or not text.strip():
            return 0

        # Remove stale chunks
        self._delete_file_chunks(rel_path)

        # Use create_documents to get metadata with start_index
        raw_docs = self._splitter.create_documents([text])
        if not raw_docs:
            return 0

        # ── Build line offset map for fast char → line conversion ──
        # line_starts[i] = character index where line (i+1) begins
        line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                line_starts.append(i + 1)

        def char_to_line(char_index: int) -> int:
            """Convert character offset to 1-based line number."""
            if not line_starts:
                return 1
            lo, hi = 0, len(line_starts) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if line_starts[mid] <= char_index:
                    lo = mid
                else:
                    hi = mid - 1
            return lo + 1  # 1-indexed

        # ── Prepare chunks with line numbers ──
        mtime_iso = self._mtime_iso(file_path)
        n = len(raw_docs)

        texts = []
        ids = []
        metadatas = []

        for i, doc in enumerate(raw_docs):
            start_char = doc.metadata.get("start_index", 0)
            end_char = start_char + len(doc.page_content)

            start_line = char_to_line(start_char)
            end_line = char_to_line(end_char)

            texts.append(doc.page_content)
            ids.append(self._chunk_id(rel_path, i))
            metadatas.append({
                "file_path":    rel_path,
                "file_name":    file_path.name,
                "extension":    file_path.suffix.lower(),
                "chunk_index":  i,
                "total_chunks": n,
                "last_modified": mtime_iso,
                "start_line":   start_line,
                "end_line":     end_line,
            })

        # Embed + store
        self._vectorstore.add_texts(
            texts=texts,
            metadatas=metadatas,
            ids=ids,
        )

        self._registry[rel_path] = mtime_iso
        return n


    # Session-level indexing
    def _walk_sandbox(self) -> list[Path]:
        """Return every indexable file inside the sandbox, skipping noise dirs."""
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.sandbox_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                fp = Path(dirpath) / fname
                if fp.suffix.lower() in INDEXABLE_EXTENSIONS:
                    files.append(fp)
        return files

    def index_session(self) -> dict:
        """
        Run at Sicily startup.  Walks the sandbox and brings the index
        in sync with the current state of the filesystem.

        Returns
        -------
        dict with keys: total_files, indexed, skipped, deleted, failed
        """
        all_files = self._walk_sandbox()
        current_rel = {
            str(f.relative_to(self.sandbox_root)) for f in all_files
        }

        # Remove deleted files
        deleted_paths = [p for p in list(self._registry) if p not in current_rel]
        for rel_path in deleted_paths:
            self._delete_file_chunks(rel_path)
            del self._registry[rel_path]

        # Index new / modified files
        indexed = skipped = failed = 0

        for fp in all_files:
            if not self._should_reindex(fp):
                skipped += 1
                continue
            try:
                n = self._index_file(fp)
                if n > 0:
                    indexed += 1
                    log.info(
                        "rag.indexed",
                        path=str(fp.relative_to(self.sandbox_root)),
                        chunks=n,
                    )
            except Exception as exc:
                failed += 1
                log.warning("rag.index_failed", path=str(fp), error=str(exc))

        self._save_registry()

        # Sync TF-IDF
        if indexed > 0 or deleted_paths:
            # Something changed — rebuild from scratch for consistency
            self._rebuild_tfidf()
        else:
            # Nothing changed — load from disk (fast)
            self._load_tfidf()

        return {
            "total_files": len(all_files),
            "indexed":     indexed,
            "skipped":     skipped,
            "deleted":     len(deleted_paths),
            "failed":      failed,
        }


    # Search
    def _tfidf_search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Keyword search. Returns [(chunk_id, score), ...]."""
        if self._tfidf_vectorizer is None or self._tfidf_matrix is None:
            return []
        try:
            q_vec  = self._tfidf_vectorizer.transform([query])
            scores = cosine_similarity(q_vec, self._tfidf_matrix).flatten()
            top_i  = scores.argsort()[::-1][:k]
            return [
                (self._tfidf_ids[i], float(scores[i]))
                for i in top_i
                if scores[i] > 0.0
            ]
        except Exception as exc:
            log.warning("rag.tfidf_search_failed", error=str(exc))
            return []


    def _semantic_search(self, query: str, k: int) -> list[tuple[str, float]]:
        """
        Semantic search via ChromaDB.
        Returns [(chunk_id, relevance_score), ...].
        Relevance score is normalised to [0, 1] by LangChain.
        """
        try:
            hits = self._vectorstore.similarity_search_with_relevance_scores(
                query, k=k
            )
            results = []
            for doc, score in hits:
                meta     = doc.metadata
                chunk_id = self._chunk_id(meta["file_path"], meta["chunk_index"])
                results.append((chunk_id, float(score)))
            return results
        except Exception as exc:
            log.warning("rag.semantic_search_failed", error=str(exc))
            return []


    @staticmethod
    def _rrf_merge(
        tfidf_hits:    list[tuple[str, float]],
        semantic_hits: list[tuple[str, float]],
        k: int = RRF_K,
    ) -> list[str]:
        """
        Reciprocal Rank Fusion.

        Each list contributes  1 / (k + rank)  to the shared score.
        A chunk appearing in both lists is rewarded twice.
        Returns chunk IDs sorted by descending RRF score.
        """
        rrf_scores: dict[str, float] = {}

        for rank, (chunk_id, _) in enumerate(tfidf_hits):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

        for rank, (chunk_id, _) in enumerate(semantic_hits):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

        return sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)


    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """
        Hybrid search: TF-IDF keyword pass  +  semantic pass  →  RRF merge.

        Parameters
        ----------
        query   Natural-language user query.
        top_k   Max number of chunks to return.

        Returns
        -------
        List of result dicts, best match first:
        {
            "file_path":    "reports/Q3.pdf",
            "file_name":    "Q3.pdf",
            "chunk_index":  3,
            "total_chunks": 12,
            "last_modified":"2024-01-15T10:30:00",
            "text":         "...the actual chunk content...",
        }
        """
        # Stage 1 — run both searches
        tfidf_hits    = self._tfidf_search(query,    k=TFIDF_CANDIDATES)
        semantic_hits = self._semantic_search(query, k=SEMANTIC_CANDIDATES)

        # Stage 2 — merge
        merged_ids = self._rrf_merge(tfidf_hits, semantic_hits)
        top_ids    = merged_ids[:top_k]

        if not top_ids:
            return []

        # Stage 3 — fetch full chunk data from ChromaDB
        raw = self._vectorstore.get(
            ids=top_ids,
            include=["documents", "metadatas"],
        )

        id_to_data = {
            cid: {"text": doc, "meta": meta}
            for cid, doc, meta in zip(
                raw["ids"], raw["documents"], raw["metadatas"]
            )
        }

        # Preserve RRF order
        results = []
        for cid in top_ids:
            if cid not in id_to_data:
                continue
            entry = id_to_data[cid]
            m = entry["meta"]
            results.append(
                {
                    "file_path":    m["file_path"],
                    "file_name":    m["file_name"],
                    "chunk_index":  m["chunk_index"],
                    "total_chunks": m["total_chunks"],
                    "last_modified": m["last_modified"],
                    "start_line":   m.get("start_line"),
                    "end_line":     m.get("end_line"),
                    "text":         entry["text"],
                }
            )

        return results


    def format_results(self, results: list[dict]) -> str:
        """
        Format search results as a structured string for the LLM.
        Now includes precise line numbers so the LLM can call read_file_lines
        with exact ranges.
        """
        if not results:
            return "No relevant content found in the indexed files."

        lines = [f"Found {len(results)} relevant snippet(s):\n"]
        for i, r in enumerate(results, 1):
            line_info = ""
            if r.get("start_line") and r.get("end_line"):
                line_info = f"lines {r['start_line']}–{r['end_line']}  "

            lines.append(
                f"[{i}] {r['file_path']}  "
                f"{line_info}"
                f"(chunk {r['chunk_index'] + 1}/{r['total_chunks']}, "
                f"modified {r['last_modified']})\n"
                f"{r['text']}\n"
            )
        return "\n".join(lines)