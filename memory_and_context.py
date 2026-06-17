import hashlib
import numpy as np
from openai import AsyncOpenAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from pathlib import Path
import asyncio
import configuration

import structlog
log = structlog.get_logger()

BASE_DIR = Path(__file__).resolve().parent
SOUL_DIR = BASE_DIR / "Souls"
CONTEXT_DIR = BASE_DIR / "Context"
PREFERENCES_FILE = CONTEXT_DIR / "preferences.md"

eval_llm = configuration.get_eval_llm()
_openai_client = AsyncOpenAI()

_cache_lock = asyncio.Lock()
_file_lock = asyncio.Lock()


# Preferences: file I/O
def load_preferences_file() -> str:
    if not PREFERENCES_FILE.exists():
        return ""
    return PREFERENCES_FILE.read_text(encoding="utf-8").strip()


def save_to_preferences_file(content: str) -> None:
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Create a temporary file path
    temp_file = PREFERENCES_FILE.with_suffix('.tmp')
    
    # 2. Write the data to the temporary file
    temp_file.write_text(content.strip() + "\n", encoding="utf-8")
    
    # 3. Atomically swap it. This replaces the old file instantly.
    # If the server crashes during step 2, the original preferences.md is safe.
    temp_file.replace(PREFERENCES_FILE)


# Preferences: line parsing
def _parse_preference_lines(text: str) -> list[str]:
    """
    Takes the raw preferences file content and returns a flat list of
    individual preference strings — one per line.

    Handles:
    - Lines starting with `-`, `*`, `•`, or `–` (stripped)
    - Plain lines
    - Skips blank lines, markdown headers, and separator lines
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()

        # Skip blanks, headers, horizontal rules
        if not line:
            continue
        if line.startswith("#"):
            continue
        if set(line) <= set("-_* "):
            continue

        # Strip leading bullet markers
        for marker in ("-", "*", "•", "–"):
            if line.startswith(marker):
                line = line[len(marker):].strip()
                break

        if line:
            lines.append(line)

    return lines


# Preferences: embedding + cache
_pref_cache: dict = {
    "hash":       None,
    "lines":      [],        # list[str] — individual preference statements
    "embeddings": None,      # np.ndarray shape (n_lines, dim)
}


async def _embed(texts: list[str]) -> np.ndarray:
    response = await _openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    vectors = [item.embedding for item in response.data]
    return np.array(vectors, dtype=np.float32)


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q     = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return (matrix / norms) @ q


async def _refresh_cache_if_needed(content: str) -> None:
    file_hash = hashlib.md5(content.encode()).hexdigest()

    if _pref_cache["hash"] == file_hash:
        return

    async with _cache_lock:
        if _pref_cache["hash"] == file_hash:  # re-check inside lock
            return

    lines = _parse_preference_lines(content)

    if not lines:
        _pref_cache.update({"hash": file_hash, "lines": [], "embeddings": None})
        return

    embeddings = await _embed(lines)
    _pref_cache.update({
        "hash":       file_hash,
        "lines":      lines,
        "embeddings": embeddings,
    })
    log.info("preferences_cache_refreshed", lines_embedded=len(lines))


async def get_relevant_preferences(query: str, top_k: int = 5, threshold: float = 0.35) -> str:
    """
    Returns a flat markdown bullet list of the preference lines most
    semantically relevant to `query`.

    Returns "" if the file is empty or nothing clears the threshold.
    """
    content = load_preferences_file()
    if not content.strip():
        return ""

    await _refresh_cache_if_needed(content)

    lines      = _pref_cache["lines"]
    embeddings = _pref_cache["embeddings"]

    if not lines or embeddings is None:
        return ""

    # Only one preference stored — no need to rank
    if len(lines) == 1:
        return f"- {lines[0]}"

    query_vec = (await _embed([query]))[0]
    scores    = _cosine_similarity(query_vec, embeddings)
    ranked    = np.argsort(scores)[::-1]

    picked = [
        f"- {lines[idx]}"
        for idx in ranked[:top_k]
        if scores[idx] >= threshold
    ]

    return "\n".join(picked)


# Shared format contract — used verbatim in both extraction and merge prompts
_FORMAT_RULES = """
OUTPUT FORMAT — follow exactly, no exceptions:
- Return ONLY a flat list of bullet points
- One preference per line, starting with "- "
- No headers, no categories, no markdown other than the leading "- "
- No multi-line bullets — if a thought needs two lines, split it into two bullets
- No meta-commentary, no preamble, no trailing summary
- Each bullet must be a self-contained, specific, reusable statement about the user

Good examples:
- Prefers concise responses without extra explanation
- Usually active in the evenings
- Prefers to confirm before any action that modifies data
- Likes to see options before making a decision
- Tends to give partial instructions and refine iteratively

Bad examples (do NOT do these):
## Behaviour Patterns        ← no headers
- Style: likes concise       ← no category prefixes
- Prefers concise responses. ← no trailing periods
- Prefers concise responses and also likes options  ← split into two bullets
""".strip()


async def run_evaluator(thread_id: str, messages: list[BaseMessage]) -> None:
    if not messages:
        log.info("evaluator_skipped", reason="no messages in session")
        return

    allowed_types = {"HumanMessage", "AIMessage"}
    conversation_text = "\n".join(
        f"[{type(msg).__name__}] {msg.content}"
        for msg in messages
        if type(msg).__name__ in allowed_types
        and getattr(msg, "content", "")
    )

    if not conversation_text.strip():
        log.info("evaluator_skipped", reason="all messages were empty")
        return

    EVAL_LLM_SOUL = get_system_message("eval_llm")

    # ── Step 1: Extract ───────────────────────────────────────────────────────
    try:
        extraction_result = await eval_llm.ainvoke([
            SystemMessage(content=EVAL_LLM_SOUL),
            HumanMessage(content=(
                f"Full session conversation:\n\n{conversation_text}\n\n"
                "Extract every stable, reusable preference or behavioural pattern "
                "you can observe about this user.\n\n"
                f"{_FORMAT_RULES}"
            )),
        ])
    except Exception as e:
        log.error("preference_extraction_failed", error=str(e))
        return

    session_preferences = extraction_result.content.strip()
    log.info("session_preferences_extracted", content=session_preferences)

    async with _file_lock:
        # ── Step 2: Load existing ─────────────────────────────────────────────────
        existing_preferences = load_preferences_file()

        if not existing_preferences.strip():
            save_to_preferences_file(session_preferences)
            log.info("preferences_file_created")
            return

        # ── Step 3: Merge ─────────────────────────────────────────────────────────
        try:
            merged_preferences = await merge_preferences(
                existing_preferences=existing_preferences,
                new_preferences=session_preferences,
            )
        except Exception as e:
            log.error("preference_merge_failed", error=str(e))
            return

        # ── Step 4: Save ──────────────────────────────────────────────────────────
        save_to_preferences_file(merged_preferences)
        log.info("preferences_file_updated")


async def merge_preferences(existing_preferences: str, new_preferences: str) -> str:
    merge_prompt = f"""
You are maintaining a long-term user preference memory file.

You are given existing stored preferences and newly extracted session preferences.
Both may be in messy formats. Your job is to produce a single clean merged list.

Merge rules:
- Remove exact and near-duplicate preferences
- If new info contradicts old info, keep the newer version only
- Drop one-time requests or temporary context (e.g. "wanted X today")
- Keep only stable behavioural patterns that are likely to matter in future sessions
- Do not invent or infer anything not stated

{_FORMAT_RULES}

# Existing Preferences

{existing_preferences}

# New Session Preferences

{new_preferences}
""".strip()

    result = await eval_llm.ainvoke([
        SystemMessage(content="You are a memory consolidation system."),
        HumanMessage(content=merge_prompt),
    ])

    return result.content.strip()


# Soul loader
def get_system_message(name: str) -> str:
    file_path = SOUL_DIR / f"{name}.md"
    if not file_path.exists():
        return "You are a helpful AI assistant."
    return file_path.read_text(encoding="utf-8")