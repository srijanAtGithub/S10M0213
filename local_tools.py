"""
local_tools.py
--------------
Sandboxed read-only filesystem tools for `sicily start`.

Mirrors the read-only subset of @modelcontextprotocol/server-filesystem,
re-implemented in pure Python with zero extra dependencies.

ALL tools are locked to a single root directory (the cwd where
`sicily start` was invoked). No path can escape that root.
"""

import datetime
import fnmatch
import stat
from pathlib import Path
from typing import Optional

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
    resolved = (root / relative).resolve()
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
def read_text_file(path: str, head: int = 0, tail: int = 0) -> str:
    """
    Read the complete contents of a file as text (always UTF-8).
    Optionally return only the first N lines via `head`, or the last N
    lines via `tail`. Cannot specify both simultaneously.

    Args:
        path: Relative path to the file.
        head: If > 0, return only the first N lines.
        tail: If > 0, return only the last N lines.
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

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read '{path}': {e}"

    if head > 0:
        lines = content.splitlines(keepends=True)
        return "".join(lines[:head])

    if tail > 0:
        lines = content.splitlines(keepends=True)
        return "".join(lines[-tail:])

    return content


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


# ── Exported tool list ─────────────────────────────────────────────────────────
LOCAL_TOOLS = [
    read_text_file,
    list_directory,
    file_tree,
    search_files,
    get_file_info,
    list_allowed_directories,
]