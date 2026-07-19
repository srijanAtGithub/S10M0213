"""
Reading_List_Groups.py
-----------------------
Backend logic for "Reading List Groups" — the '+' button that hovers over
each result card (Find More Like This today; any other link-bearing card
later) and lets the user file that link into a named group, the same way
dropping a text snippet onto the Collections zone files it into a named
collection.

Design note — why this replaced the old flat Reading_List.py:

  The original reading list was intentionally a flat, folder-less list —
  one tap, no picker, fast to scan. That held up fine as long as there
  was exactly one entry point (the small '+' on Find More Like This
  results) and exactly one implicit "list". Once that same '+' needed to
  work the same way Collections' drag-and-drop does — pick an existing
  bucket or create one on the fly, filtered by a text box — the flat
  model stopped matching the interaction: you can't "pick a list" if
  there's only ever one. So this module mirrors Collections.py's shape
  (named groups, each holding many items) instead of the old single-table
  design. The old reason/source provenance fields carry over unchanged;
  only the grouping layer is new.

Storage: same plain-sqlite3-no-ORM pattern as Collections.py.

  READING_LIST_DATA_DIR = ~/.sicily/Navigator/ReadingList
  DB_PATH                = ~/.sicily/Navigator/ReadingList/reading_list.db

Two tables:
  groups(id, name, created_at)
  items(id, group_id, title, url, reason, source_title, source_url,
        is_read, created_at)

  - groups.name           : user-facing label, unique (case-insensitive),
                             created inline via "Create & Add" same as
                             Collections.
  - title / url            : the saved link itself.
  - reason                 : optional one-liner carried over from context,
                              e.g. Find More Like This's "why this matches"
                              blurb — shown dimmed under the title, same
                              slot as .link-result-reason today.
  - source_title/source_url: the page the user was ON when they saved
                              this link (distinct from the link's own
                              url) — mirrors Collections' tab_title/url
                              provenance fields.
  - is_read                : the one field that actually distinguishes a
                              reading list from a bookmark list. Toggled
                              from the view, not set at save time.

De-duping is scoped to a group, not global — the same link can legitimately
live in two different groups (e.g. "Weekend Reading" and "ML Papers"), it
just can't be added twice to the *same* group.

Endpoints wired up in navigator_bridge.py the same way Collections.py's
are: this module owns the request/response models and process_*
functions, the bridge just imports and exposes them as routes.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

from pydantic import BaseModel
import structlog

log = structlog.get_logger()

# ── STORAGE LOCATION ────────────────────────────────────────────────────
SICILY_HOME = Path.home() / ".sicily"
READING_LIST_DATA_DIR = SICILY_HOME / "Navigator" / "ReadingList"
DB_PATH = READING_LIST_DATA_DIR / "reading_list.db"


def _ensure_db():
    """Create the data dir + schema on first use. Safe to call repeatedly."""
    READING_LIST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id      INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                title         TEXT NOT NULL,
                url           TEXT NOT NULL,
                reason        TEXT,
                source_title  TEXT,
                source_url    TEXT,
                is_read       INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_group_id
                ON items(group_id);
            CREATE INDEX IF NOT EXISTS idx_items_created_at
                ON items(created_at);
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(url: str) -> str:
    """Same normalization as Find_More_Like_This.py's dedup helper, kept
    local (not imported) so this module has no dependency on that one —
    it's a small, self-contained rule and importing across features for
    a three-line helper isn't worth the coupling."""
    from urllib.parse import urlparse

    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


# ── DATA MODELS ──────────────────────────────────────────────────────────
class ReadingListGroupOut(BaseModel):
    id: int
    name: str
    item_count: int


class ListReadingListGroupsResponse(BaseModel):
    groups: list[ReadingListGroupOut]


class ReadingListItemOut(BaseModel):
    id: int
    title: str
    url: str
    reason: Optional[str] = None
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    is_read: bool
    created_at: str


class ReadingListGroupDetailResponse(BaseModel):
    group_id: int
    group_name: str
    items: list[ReadingListItemOut]


class AddReadingListItemRequest(BaseModel):
    group_name: str
    title: str
    url: str
    reason: str = ""
    source_title: str = ""
    source_url: str = ""


class AddReadingListItemResponse(BaseModel):
    group_id: int
    group_name: str
    created_new_group: bool
    item: ReadingListItemOut
    already_existed: bool


class SetReadRequest(BaseModel):
    is_read: bool


# ── INTERNAL HELPERS ────────────────────────────────────────────────────
def _row_to_item(row: sqlite3.Row) -> ReadingListItemOut:
    return ReadingListItemOut(
        id=row["id"],
        title=row["title"],
        url=row["url"],
        reason=row["reason"],
        source_title=row["source_title"],
        source_url=row["source_url"],
        is_read=bool(row["is_read"]),
        created_at=row["created_at"],
    )


def _find_group_by_name(conn: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    """Case-insensitive match, same convention as Collections.py uses for
    collection names — lets "Create & Add" fall through to an existing
    group instead of creating a near-duplicate on casing alone."""
    return conn.execute(
        "SELECT * FROM groups WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()


# ── LOGIC ────────────────────────────────────────────────────────────────
async def process_list_reading_list_groups() -> ListReadingListGroupsResponse:
    """Return every group with its item count, for the picker list and the
    top-level 'Reading List Groups' view — same shape as
    process_list_collections()."""
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT g.id, g.name, COUNT(i.id) AS item_count
            FROM groups g
            LEFT JOIN items i ON i.group_id = g.id
            GROUP BY g.id
            ORDER BY g.created_at DESC
            """
        ).fetchall()

    groups = [
        ReadingListGroupOut(id=row["id"], name=row["name"], item_count=row["item_count"])
        for row in rows
    ]
    return ListReadingListGroupsResponse(groups=groups)


async def process_get_reading_list_group(group_id: int) -> ReadingListGroupDetailResponse:
    """Return one group's saved links, newest first."""
    _ensure_db()
    with _connect() as conn:
        group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise ValueError(f"No reading list group with id {group_id}")

        rows = conn.execute(
            "SELECT * FROM items WHERE group_id = ? ORDER BY created_at DESC",
            (group_id,),
        ).fetchall()

    return ReadingListGroupDetailResponse(
        group_id=group["id"],
        group_name=group["name"],
        items=[_row_to_item(row) for row in rows],
    )


async def process_add_reading_list_item(
    req: AddReadingListItemRequest,
) -> AddReadingListItemResponse:
    """
    Add a link to a reading list group, creating the group first if it
    doesn't exist yet — same "type a name, get an existing group or a new
    one" behaviour as process_add_snippet() in Collections.py.

    De-dupes on normalized URL *within that group* so clicking '+' twice
    on the same result into the same group doesn't create two rows —
    returns the existing row instead (already_existed=True) rather than
    erroring, since from the user's point of view "save it" succeeding
    idempotently is the expected behaviour, not a failure.
    """
    _ensure_db()
    group_name = req.group_name.strip()
    title = req.title.strip()
    url = req.url.strip()

    if not group_name:
        raise ValueError("group_name cannot be empty")
    if not url:
        raise ValueError("url cannot be empty")
    if not title:
        title = url

    now = _now_iso()
    normalized = _normalize_url(url)

    with _connect() as conn:
        group = _find_group_by_name(conn, group_name)
        created_new_group = False

        if not group:
            cur = conn.execute(
                "INSERT INTO groups (name, created_at) VALUES (?, ?)",
                (group_name, now),
            )
            group_id = cur.lastrowid
            group_name_final = group_name
            created_new_group = True
        else:
            group_id = group["id"]
            group_name_final = group["name"]

        existing_items = conn.execute(
            "SELECT * FROM items WHERE group_id = ?", (group_id,)
        ).fetchall()
        match = next(
            (r for r in existing_items if _normalize_url(r["url"]) == normalized), None
        )

        if match:
            return AddReadingListItemResponse(
                group_id=group_id,
                group_name=group_name_final,
                created_new_group=created_new_group,
                item=_row_to_item(match),
                already_existed=True,
            )

        cur = conn.execute(
            """
            INSERT INTO items (group_id, title, url, reason, source_title, source_url, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                group_id,
                title,
                url,
                req.reason.strip() or None,
                req.source_title.strip() or None,
                req.source_url.strip() or None,
                now,
            ),
        )
        item_id = cur.lastrowid

    log.info(
        "Reading list item added",
        url=url,
        source=req.source_url,
        group_id=group_id,
        group_name=group_name_final,
        created_new_group=created_new_group,
    )

    return AddReadingListItemResponse(
        group_id=group_id,
        group_name=group_name_final,
        created_new_group=created_new_group,
        item=ReadingListItemOut(
            id=item_id,
            title=title,
            url=url,
            reason=req.reason.strip() or None,
            source_title=req.source_title.strip() or None,
            source_url=req.source_url.strip() or None,
            is_read=False,
            created_at=now,
        ),
        already_existed=False,
    )


async def process_set_read(item_id: int, is_read: bool) -> dict:
    """Toggle read/unread on a single item."""
    _ensure_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE items SET is_read = ? WHERE id = ?", (1 if is_read else 0, item_id)
        )
        if cur.rowcount == 0:
            raise ValueError(f"No reading list item with id {item_id}")

    return {"status": "success", "item_id": item_id, "is_read": is_read}


async def process_delete_reading_list_group(group_id: int) -> dict:
    """Deletes a group and every item inside it (ON DELETE CASCADE)."""
    _ensure_db()
    with _connect() as conn:
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))

    log.info("Reading list group deleted", group_id=group_id)
    return {"status": "success", "deleted_group_id": group_id}


async def process_delete_reading_list_item(item_id: int) -> dict:
    """Deletes a single saved link."""
    _ensure_db()
    with _connect() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    log.info("Reading list item deleted", item_id=item_id)
    return {"status": "success", "deleted_item_id": item_id}