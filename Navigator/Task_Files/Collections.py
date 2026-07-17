"""
Collections.py
---------------
Backend logic for "Saved Collections" — the drag-and-drop-to-collections
feature in the side panel.

A user drags selected page text onto the "Collections" drop zone, picks
(or types a new) collection name from the floating window, and the
snippet gets stored under that collection along with a little metadata
(source tab title, page URL, timestamp) so it can be traced back later.

Storage: a small local SQLite database, structured the same way
ChatStore.py handles chat persistence — plain sqlite3, no ORM, schema
created on first connect.

  SICILY_HOME          = ~/.sicily
  COLLECTIONS_DATA_DIR  = ~/.sicily/Navigator/Collections
  DB_PATH               = ~/.sicily/Navigator/Collections/collections.db

Two tables:
  collections(id, name, created_at)
  snippets(id, collection_id, text, tab_title, url, created_at)

Endpoints are wired up in navigator_bridge.py exactly like
Summarise_Page.py: this module defines the request/response models and
a process_* function per endpoint, and the bridge just imports and
exposes them as routes.
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
COLLECTIONS_DATA_DIR = SICILY_HOME / "Navigator" / "Collections"
DB_PATH = COLLECTIONS_DATA_DIR / "collections.db"


def _ensure_db():
    """Create the data dir + schema on first use. Safe to call repeatedly."""
    COLLECTIONS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collections (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snippets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                text          TEXT NOT NULL,
                tab_title     TEXT,
                url           TEXT,
                created_at    TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snippets_collection_id
                ON snippets(collection_id);
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


# ── DATA MODELS ──────────────────────────────────────────────────────────
class SnippetOut(BaseModel):
    id: int
    text: str
    tab_title: Optional[str] = None
    url: Optional[str] = None
    created_at: str


class CollectionOut(BaseModel):
    id: int
    name: str
    created_at: str
    snippet_count: int


class ListCollectionsResponse(BaseModel):
    collections: list[CollectionOut]


class AddSnippetRequest(BaseModel):
    collection_name: str
    text: str
    tab_title: str = ""
    url: str = ""


class AddSnippetResponse(BaseModel):
    collection_id: int
    collection_name: str
    snippet: SnippetOut
    created_new_collection: bool


class CollectionDetailResponse(BaseModel):
    id: int
    name: str
    created_at: str
    snippets: list[SnippetOut]


# ── LOGIC ────────────────────────────────────────────────────────────────
async def process_list_collections() -> ListCollectionsResponse:
    """
    Return every collection with its snippet count, newest collection
    first. Powers the scrollable list in the drop overlay, filtered
    client-side as the user types.
    """
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.created_at, COUNT(s.id) AS snippet_count
            FROM collections c
            LEFT JOIN snippets s ON s.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """
        ).fetchall()

    collections = [
        CollectionOut(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            snippet_count=row["snippet_count"],
        )
        for row in rows
    ]
    return ListCollectionsResponse(collections=collections)


async def process_add_snippet(req: AddSnippetRequest) -> AddSnippetResponse:
    """
    Add a dropped snippet to a collection, creating the collection first
    if it doesn't exist yet (covers both "add to existing" and
    "create and add" from the frontend, with one endpoint).
    """
    _ensure_db()
    name = req.collection_name.strip()
    text = req.text.strip()

    if not name:
        raise ValueError("collection_name cannot be empty")
    if not text:
        raise ValueError("text cannot be empty")

    now = _now_iso()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, name FROM collections WHERE name = ?", (name,)
        ).fetchone()

        created_new = existing is None
        if existing is None:
            cur = conn.execute(
                "INSERT INTO collections (name, created_at) VALUES (?, ?)",
                (name, now),
            )
            collection_id = cur.lastrowid
        else:
            collection_id = existing["id"]

        cur = conn.execute(
            """
            INSERT INTO snippets (collection_id, text, tab_title, url, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (collection_id, text, req.tab_title.strip(), req.url.strip(), now),
        )
        snippet_id = cur.lastrowid

    log.info(
        "Snippet added to collection",
        collection=name,
        created_new_collection=created_new,
        url=req.url,
    )

    return AddSnippetResponse(
        collection_id=collection_id,
        collection_name=name,
        snippet=SnippetOut(
            id=snippet_id,
            text=text,
            tab_title=req.tab_title.strip() or None,
            url=req.url.strip() or None,
            created_at=now,
        ),
        created_new_collection=created_new,
    )


async def process_get_collection(collection_id: int) -> CollectionDetailResponse:
    """Fetch a single collection with all of its snippets, newest first."""
    _ensure_db()
    with _connect() as conn:
        collection = conn.execute(
            "SELECT id, name, created_at FROM collections WHERE id = ?",
            (collection_id,),
        ).fetchone()

        if collection is None:
            raise ValueError(f"No collection with id {collection_id}")

        rows = conn.execute(
            """
            SELECT id, text, tab_title, url, created_at
            FROM snippets
            WHERE collection_id = ?
            ORDER BY created_at DESC
            """,
            (collection_id,),
        ).fetchall()

    snippets = [
        SnippetOut(
            id=row["id"],
            text=row["text"],
            tab_title=row["tab_title"],
            url=row["url"],
            created_at=row["created_at"],
        )
        for row in rows
    ]

    return CollectionDetailResponse(
        id=collection["id"],
        name=collection["name"],
        created_at=collection["created_at"],
        snippets=snippets,
    )