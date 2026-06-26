"""
cowork_tools.py
--------------
Sandboxed filesystem tools for `sicily start`.

Mirrors the @modelcontextprotocol/server-filesystem interface,
re-implemented in pure Python with zero extra dependencies.

ALL tools are locked to a single root directory (the cwd where
`sicily start` was invoked). No path can escape that root.

Tool tiers
----------
Read-only tools  — safe:     read_text_file, list_directory, file_tree,
                              search_files, get_file_info,
                              list_allowed_directories
Write tools      — safe-ish: create_text_file, make_directory
                  Guarantee: never overwrite or delete existing content.
"""

import datetime
import fnmatch
import stat
from pathlib import Path
from typing import Optional
import importlib

from langchain_core.tools import tool


# ── Noise directories — skipped in trees and searches ─────────────────────────
SKIP_DIRS = {
    ".venv", "venv", "env", ".env",
    "node_modules",
    "__pycache__",
    ".git",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
    ".tox", ".nox",
    ".idea", ".vscode",
}


# ── Allowed extensions for new text-based files ───────────────────────────────
# Binary formats (.docx, .xlsx, .pdf, …) are intentionally excluded — writing
# them requires structured serialisation, not raw text I/O.
ALLOWED_WRITE_EXTENSIONS: frozenset[str] = frozenset({
    # Documents & notes
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex",
    # Config & data interchange
    ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".env",
    # Web & markup
    ".html", ".htm", ".css", ".scss", ".sass", ".xml", ".svg",
    # Source code — common languages
    ".py", ".pyi",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".sh", ".bash", ".zsh", ".fish",
    ".rb", ".go", ".rs",
    ".java", ".kt", ".scala",
    ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".fs",
    ".php", ".lua", ".r", ".sql",
    # Data & logs
    ".csv", ".tsv", ".log",
    # Misc text
    ".diff", ".patch", ".gitignore", ".editorconfig",
})


# ── Sandbox root ───────────────────────────────────────────────────────────────
_SANDBOX_ROOT: Optional[Path] = None


def set_sandbox_root(path: Path) -> None:
    global _SANDBOX_ROOT
    _SANDBOX_ROOT = path.resolve()


def get_sandbox_root() -> Path:
    if _SANDBOX_ROOT is None:
        raise RuntimeError("Sandbox root has not been set. Call set_sandbox_root() first.")
    return _SANDBOX_ROOT


# ── Internal helpers ───────────────────────────────────────────────────────────
def _safe_path(relative: str) -> Path:
    """
    Resolve a user/AI-supplied path against the sandbox root.
    Raises PermissionError if the resolved path would escape the root.
    """
    root = get_sandbox_root()
    candidate = root / relative
    try:
        resolved = candidate.resolve()
    except OSError:
        # On Windows, resolve() can raise FileNotFoundError for paths
        # that don't exist yet. Fall back to normpath-based resolution,
        # which works for non-existent paths.
        import os
        resolved = Path(os.path.normpath(candidate))

    if not resolved.is_relative_to(root):
        raise PermissionError(
            f"Access denied: '{relative}' resolves outside the allowed directory."
        )
    return resolved


def _is_skipped(path: Path) -> bool:
    """True if this is a noise directory that should be excluded."""
    return path.is_dir() and path.name in SKIP_DIRS


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_permissions(mode: int) -> str:
    """Convert a stat st_mode integer to a human-readable 'rwxrwxrwx' string."""
    result = []
    for who in ("USR", "GRP", "OTH"):
        for perm, letter in (("R", "r"), ("W", "w"), ("X", "x")):
            flag = getattr(stat, f"S_I{perm}{who}")
            result.append(letter if mode & flag else "-")
    return "".join(result)


# ── Tools ──────────────────────────────────────────────────────────────────────
@tool
def read_file(path: str, head: int = 0, tail: int = 0) -> str:
    """
    Read the contents of any file and return it as plain text.

    Handles two categories transparently — the caller does not need to
    know or care which category a file falls into:

    Text-based files (.txt, .md, .py, .json, .csv, .yaml, .html, etc.)
        Raw UTF-8 content is returned as-is.

    Binary document formats
        .pdf   — text is extracted page by page, each labelled [Page N]
        .docx  — all paragraph text is extracted in document order
        .xlsx / .xls — every sheet is extracted as a tab-separated table,
                       each labelled [Sheet: name]

    Args:
        path: Relative path to the file.
        head: If > 0, return only the first N lines of the extracted text.
        tail: If > 0, return only the last N lines of the extracted text.
              Cannot be combined with head.
    """
    if head > 0 and tail > 0:
        return "Error: Cannot specify both `head` and `tail` simultaneously."

    try:
        file_path = _safe_path(path)
    except PermissionError as e:
        return str(e)

    if not file_path.exists():
        return f"File '{path}' does not exist."
    if not file_path.is_file():
        return f"'{path}' is a directory, not a file."

    # ── Binary formats — route to dedicated parser ────────────────────────────
    if file_path.suffix.lower() in _BINARY_EXTENSIONS:
        try:
            content = _read_binary(file_path)
        except ImportError as e:
            return f"Cannot read '{path}': missing required package — {e}"
        except Exception as e:
            return f"Could not extract text from '{path}': {e}"

    # ── Text files — UTF-8 read ───────────────────────────────────────────────
    else:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Could not read '{path}': {e}"

    if head > 0:
        return "".join(content.splitlines(keepends=True)[:head])
    if tail > 0:
        return "".join(content.splitlines(keepends=True)[-tail:])

    return content


def _read_binary(path: Path) -> str:
    """
    Extract human-readable text from binary file formats.
    Dispatches to the appropriate parser based on file extension.
    Raises ImportError with an install hint if the required library is missing.
    Raises ValueError for unsupported binary extensions.
    """
    ext = path.suffix.lower()

    if ext == ".pdf":
        if importlib.util.find_spec("pdfplumber") is None:
            raise ImportError("pip install pdfplumber")
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n\n".join(f"[Page {i+1}]\n{text}" for i, text in enumerate(pages) if text.strip())

    if ext in {".xlsx", ".xls"}:
        if importlib.util.find_spec("openpyxl") is None:
            raise ImportError("pip install openpyxl")
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows = [
                "\t".join("" if cell.value is None else str(cell.value) for cell in row)
                for row in ws.iter_rows()
            ]
            sheets.append(f"[Sheet: {name}]\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(sheets)

    if ext in {".docx", ".doc"}:
        if importlib.util.find_spec("docx") is None:
            raise ImportError("pip install python-docx")
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    raise ValueError(
        f"No binary reader available for '{ext}'. "
        "For plain text files this tool reads UTF-8 directly. "
        "For other binary formats, a dedicated tool may be needed."
    )


# Extensions that require binary parsing rather than UTF-8 text reads
_BINARY_EXTENSIONS = frozenset({".pdf", ".xlsx", ".xls", ".docx", ".doc"})


@tool
def list_directory(path: str = ".") -> str:
    """
    List the immediate contents of a directory.
    Each entry is prefixed with [FILE] or [DIR].
    Does NOT recurse into subdirectories.

    Args:
        path: Relative path to the directory. Defaults to "." (sandbox root).
    """
    try:
        target = _safe_path(path)
    except PermissionError as e:
        return str(e)

    if not target.exists():
        return f"Directory '{path}' does not exist."
    if not target.is_dir():
        return f"'{path}' is a file, not a directory."

    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return f"Permission denied: cannot list '{path}'."

    if not entries:
        return "Directory is empty."

    lines = []
    for entry in entries:
        tag = "[DIR] " if entry.is_dir() else "[FILE]"
        note = "  [skipped — noise dir]" if _is_skipped(entry) else ""
        lines.append(f"{tag} {entry.name}{note}")

    return "\n".join(lines)


@tool
def file_tree(subdirectory: str = ".") -> str:
    """
    Show a full recursive visual tree of files and folders (like the `tree` command).
    Noise directories (.venv, __pycache__, .git, node_modules, etc.) are shown
    but not expanded — they appear as [skipped].

    Args:
        subdirectory: Relative path to start the tree from. Defaults to ".".
    """
    try:
        target = _safe_path(subdirectory)
    except PermissionError as e:
        return str(e)

    if not target.exists():
        return f"Directory '{subdirectory}' does not exist."

    root = get_sandbox_root()
    label = str(target.relative_to(root)) if subdirectory != "." else "."
    lines = [f"📁 {label}"]

    def _render(directory: Path, prefix: str = "") -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            lines.append(f"{prefix}└── [permission denied]")
            return

        for i, child in enumerate(children):
            is_last = i == len(children) - 1
            connector = "└── " if is_last else "├── "

            if _is_skipped(child):
                lines.append(f"{prefix}{connector}📁 {child.name}/  [skipped]")
                continue

            icon = "📁 " if child.is_dir() else "📄 "
            lines.append(f"{prefix}{connector}{icon}{child.name}")

            if child.is_dir():
                extension = "    " if is_last else "│   "
                _render(child, prefix + extension)

    _render(target)
    return "\n".join(lines)


@tool
def search_files(path: str, pattern: str, exclude_patterns: list[str] = []) -> str:
    """
    Recursively search for files and directories whose name matches a
    glob-style pattern (e.g. "*.py", "config.*", "test_*").
    Returns relative paths to all matches.

    Noise directories (.venv, node_modules, __pycache__, etc.) are
    automatically excluded. Additional paths can be excluded via
    `exclude_patterns`.

    Args:
        path:             Starting directory (relative path).
        pattern:          Glob pattern matched against each entry's name.
        exclude_patterns: Optional list of glob patterns to exclude from results,
                          matched against both the entry name and its relative path.
    """
    try:
        start = _safe_path(path)
    except PermissionError as e:
        return str(e)

    if not start.exists():
        return f"Directory '{path}' does not exist."
    if not start.is_dir():
        return f"'{path}' is not a directory."

    root = get_sandbox_root()
    matches: list[str] = []

    def _walk(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return

        for child in children:
            # Always skip noise dirs
            if _is_skipped(child):
                continue

            rel = str(child.relative_to(root))

            # Apply caller-supplied exclusions (match on name OR rel path)
            if any(
                fnmatch.fnmatch(child.name, xp) or fnmatch.fnmatch(rel, xp)
                for xp in exclude_patterns
            ):
                continue

            # Match the search pattern against the entry name
            if fnmatch.fnmatch(child.name, pattern):
                matches.append(rel)

            if child.is_dir():
                _walk(child)

    _walk(start)

    if not matches:
        return "No matches found."

    return "\n".join(matches)


@tool
def get_file_info(path: str) -> str:
    """
    Get detailed metadata about a file or directory.
    Returns: name, type, size, permissions, created, modified, and accessed times.

    Args:
        path: Relative path to the file or directory.
    """
    try:
        target = _safe_path(path)
    except PermissionError as e:
        return str(e)

    if not target.exists():
        return f"'{path}' does not exist."

    try:
        s = target.stat()
    except PermissionError:
        return f"Permission denied: cannot stat '{path}'."

    kind = "Directory" if target.is_dir() else "File"
    size = f"{s.st_size:,} bytes" if target.is_file() else "—"
    permissions = _fmt_permissions(s.st_mode)

    # Creation time:
    #   macOS  → st_birthtime (real creation time)
    #   Windows→ st_ctime     (real creation time)
    #   Linux  → st_ctime     (last metadata change; true birthtime not exposed by Python)
    created = _fmt_ts(getattr(s, "st_birthtime", s.st_ctime))

    return "\n".join([
        f"Name:        {target.name}",
        f"Type:        {kind}",
        f"Size:        {size}",
        f"Permissions: {permissions}",
        f"Created:     {created}",
        f"Modified:    {_fmt_ts(s.st_mtime)}",
        f"Accessed:    {_fmt_ts(s.st_atime)}",
        f"Path:        {path}",
    ])


@tool
def list_allowed_directories() -> str:
    """
    List all directories the agent is allowed to access.
    Returns the sandbox root that was locked in when `sicily start` was invoked.
    No input required.
    """
    root = get_sandbox_root()
    return f"Allowed directories:\n  {root}"



# ── Write tools (safe-ish) ─────────────────────────────────────────────────────
@tool
def create_text_file(
    path: str,
    content: str,
    create_parents: bool = True,
) -> str:
    """
    Create a NEW text file at the given relative path with the provided content.

    Safety guarantees
    -----------------
    - Will NEVER overwrite an existing file or directory.  If the path already
      exists the operation is aborted immediately and an error is returned.
    - The resolved path must stay inside the sandbox root; any traversal attempt
      (e.g. "../../etc/passwd") is blocked before any I/O occurs.
    - Only recognised text-based extensions are accepted (see list below).
    - Parent directories are created automatically when `create_parents=True`
      (the default), as long as they remain inside the sandbox.

    Supported extensions
    --------------------
    Documents/notes : .txt .md .markdown .rst .org .tex
    Config/data     : .json .jsonl .ndjson .yaml .yml .toml .ini .cfg .conf .env
    Web/markup      : .html .htm .css .scss .sass .xml .svg
    Source code     : .py .pyi .js .mjs .cjs .ts .tsx .jsx .sh .bash .zsh .fish
                      .rb .go .rs .java .kt .scala .c .cpp .cc .h .hpp .cs .fs
                      .php .lua .r .sql
    Data/logs       : .csv .tsv .log
    Misc text       : .diff .patch .gitignore .editorconfig

    Args:
        path:           Relative path for the new file, including its name and
                        extension (e.g. "notes/meeting.md").
        content:        UTF-8 text content to write.
        create_parents: When True (default), any missing parent directories are
                        created automatically.  Set to False if you want the
                        operation to fail when a parent does not exist.
    """
    # ── 1. Sandbox enforcement ────────────────────────────────────────────────
    try:
        target = _safe_path(path)
    except PermissionError as e:
        return str(e)

    # ── 2. No-overwrite guard ─────────────────────────────────────────────────
    if target.exists():
        kind = "directory" if target.is_dir() else "file"
        return (
            f"Refused: '{path}' already exists as a {kind}. "
            "This tool will not overwrite existing entries."
        )

    # ── 3. Extension whitelist ────────────────────────────────────────────────
    ext = target.suffix.lower()
    if not ext:
        return (
            f"Refused: '{path}' has no file extension. "
            "Please include one (e.g. report.md, config.yaml)."
        )
    if ext not in ALLOWED_WRITE_EXTENSIONS:
        allowed_str = "  " + "\n  ".join(sorted(ALLOWED_WRITE_EXTENSIONS))
        return (
            f"Refused: extension '{ext}' is not in the allowed list.\n"
            f"Supported extensions:\n{allowed_str}"
        )

    # ── 4. Parent directory handling ──────────────────────────────────────────
    parent = target.parent
    if not parent.exists():
        if not create_parents:
            rel_parent = parent.relative_to(get_sandbox_root())
            return (
                f"Error: parent directory '{rel_parent}' does not exist. "
                "Pass create_parents=True to create it automatically, "
                "or use make_directory first."
            )
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Could not create parent directories for '{path}': {e}"

    # ── 5. Write ──────────────────────────────────────────────────────────────
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Could not write '{path}': {e}"

    size = target.stat().st_size
    return (
        f"Created '{path}'.\n"
        f"Size: {size:,} bytes | Encoding: utf-8"
    )


@tool
def make_directory(path: str) -> str:
    """
    Create a new directory at the given relative path, including any missing
    intermediate parents.  Idempotent: succeeds silently if the directory
    already exists.

    Safety guarantees
    -----------------
    - Will NOT fail or overwrite if the directory already exists.
    - Will NOT touch any existing files or directories inside the path.
    - The resolved path must stay inside the sandbox root.
    - Will refuse if the path already exists as a *file*.

    Args:
        path: Relative path of the directory to create (e.g. "reports/q3").
    """
    # ── 1. Sandbox enforcement ────────────────────────────────────────────────
    try:
        target = _safe_path(path)
    except PermissionError as e:
        return str(e)

    # ── 2. Collision check ────────────────────────────────────────────────────
    if target.is_file():
        return (
            f"Refused: '{path}' already exists as a file. "
            "Cannot create a directory at that path."
        )

    if target.is_dir():
        return f"Directory '{path}' already exists — nothing to do."

    # ── 3. Create ─────────────────────────────────────────────────────────────
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"Could not create directory '{path}': {e}"

    return f"Directory '{path}' created."


# ── Exported tool list ─────────────────────────────────────────────────────────
LOCAL_TOOLS = [
    # ── Read-only (safe) ──────────────────────────────────────────────────────
    read_file,
    list_directory,
    file_tree,
    search_files,
    get_file_info,
    list_allowed_directories,

    # ── Write (safe-ish — create only, never overwrite or delete) ─────────────
    create_text_file,
    make_directory,
]


# Mapping tool names to dynamic, user-friendly status messages
TOOL_STATUS_MAP = {
    "read_file": lambda args: f"Reading file [white]'{args.get('path')}'[/white]",
    "list_directory": lambda args: f"Listing contents of [white]'{args.get('path', '.')}'[/white]",
    "file_tree": lambda args: f"Mapping out directory tree from [white]'{args.get('subdirectory', '.')}'[/white]",
    "search_files": lambda args: f"Searching for [white]'{args.get('pattern')}'[/white] inside [white]'{args.get('path')}'[/white]",
    "get_file_info": lambda args: f"Inspecting file metadata for [white]'{args.get('path')}'[/white]",
    "list_allowed_directories": lambda args: "Checking project boundary restrictions",
    "create_text_file": lambda args: f"Creating new text file [white]'{args.get('path')}'[/white]",
    "make_directory": lambda args: f"Creating directory [white]'{args.get('path')}'[/white]",
}

def get_friendly_tool_message(tool_call: dict) -> str:
    """Extracts the tool name and args to build a readable status update."""
    name = tool_call.get("name")
    args = tool_call.get("args", {})
    
    if name in TOOL_STATUS_MAP:
        return TOOL_STATUS_MAP[name](args)
    
    # Fallback just in case you add new tools later without mapping them
    return name
