"""
local_tools.py
--------------
Sandboxed file-system tools for `sicily start`.

ALL tools are locked to a single root directory (the cwd where
`sicily start` was invoked).  No path can escape that root.
"""

from pathlib import Path
from langchain_core.tools import tool

# ── Sandbox root ──────────────────────────────────────────────────────────────
# Set once at startup by local_session.py before any tool is called.
_SANDBOX_ROOT: Path | None = None


def set_sandbox_root(path: Path) -> None:
    global _SANDBOX_ROOT
    _SANDBOX_ROOT = path.resolve()


def get_sandbox_root() -> Path:
    if _SANDBOX_ROOT is None:
        raise RuntimeError("Sandbox root has not been set. Call set_sandbox_root() first.")
    return _SANDBOX_ROOT


# ── Path validator ────────────────────────────────────────────────────────────
def _safe_path(relative: str) -> Path:
    """
    Resolve a user/AI-supplied path against the sandbox root.
    Raises PermissionError if the resolved path escapes the root.
    """
    root = get_sandbox_root()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError(
            f"Access denied: '{relative}' resolves outside the project directory."
        )
    return resolved


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def list_files(subdirectory: str = ".") -> str:
    """
    List all files and folders inside the sandbox (or a subdirectory of it).
    Returns relative paths only — the AI never sees absolute paths.

    Args:
        subdirectory: Relative path to list. Defaults to "." (the root).
    """
    try:
        target = _safe_path(subdirectory)
    except PermissionError as e:
        return str(e)

    if not target.exists():
        return f"Directory '{subdirectory}' does not exist."
    if not target.is_dir():
        return f"'{subdirectory}' is a file, not a directory."

    root = get_sandbox_root()
    entries = sorted(target.rglob("*"))
    lines = []
    for entry in entries:
        rel = entry.relative_to(root)
        prefix = "📁 " if entry.is_dir() else "📄 "
        lines.append(f"{prefix}{rel}")

    if not lines:
        return "Directory is empty."

    return "\n".join(lines)


@tool
def read_file(relative_path: str) -> str:
    """
    Read and return the text content of a file inside the sandbox.

    Args:
        relative_path: Path to the file, relative to the sandbox root.
    """
    try:
        path = _safe_path(relative_path)
    except PermissionError as e:
        return str(e)

    if not path.exists():
        return f"File '{relative_path}' does not exist."
    if not path.is_file():
        return f"'{relative_path}' is a directory, not a file."

    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read '{relative_path}': {e}"


@tool
def file_tree(subdirectory: str = ".") -> str:
    """
    Show a visual tree of files and folders (like the `tree` command).

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
    lines = [f"📁 {target.relative_to(root) if subdirectory != '.' else '.'}"]

    def _render(directory: Path, prefix: str = ""):
        children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        for i, child in enumerate(children):
            is_last = i == len(children) - 1
            connector = "└── " if is_last else "├── "
            icon = "📁 " if child.is_dir() else "📄 "
            lines.append(f"{prefix}{connector}{icon}{child.name}")
            if child.is_dir():
                extension = "    " if is_last else "│   "
                _render(child, prefix + extension)

    _render(target)
    return "\n".join(lines)


@tool
def file_info(relative_path: str) -> str:
    """
    Get metadata about a file or folder: size, type, last modified time.

    Args:
        relative_path: Path relative to the sandbox root.
    """
    try:
        path = _safe_path(relative_path)
    except PermissionError as e:
        return str(e)

    if not path.exists():
        return f"'{relative_path}' does not exist."

    import datetime
    stat = path.stat()
    kind = "Directory" if path.is_dir() else "File"
    size = f"{stat.st_size:,} bytes" if path.is_file() else "—"
    modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    return (
        f"Name:     {path.name}\n"
        f"Type:     {kind}\n"
        f"Size:     {size}\n"
        f"Modified: {modified}\n"
        f"Path:     {relative_path}"
    )


# ── Exported list of all local tools ─────────────────────────────────────────
LOCAL_TOOLS = [list_files, read_file, file_tree, file_info]