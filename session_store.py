"""
session_store.py
----------------
Persistent SQLite-backed store for UserSession metadata.

Stores: user_id → (session_id, user_name, started_at, last_interaction_at)

This is intentionally separate from LangGraph's checkpoint store.
LangGraph's AsyncSqliteSaver handles its own tables in the same DB file.
We just need to persist the user_id → session_id mapping so that on
restart, we can hand the same thread_id back to LangGraph and it will
reload the full conversation from its own checkpoint tables.
"""

import aiosqlite
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path("DATA/bot_state.db")


@dataclass
class PersistedSession:
    session_id: str
    user_name: str
    started_at: float
    last_interaction_at: float


async def init_db():
    """Create tables if they don't exist. Call once at startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id             TEXT PRIMARY KEY,
                session_id          TEXT NOT NULL,
                user_name           TEXT NOT NULL,
                started_at          REAL NOT NULL,
                last_interaction_at REAL NOT NULL
            )
        """)
        await db.commit()
    print(f"✅ Session DB initialized at {DB_PATH}")


async def load_session(user_id: str) -> PersistedSession | None:
    """Load a persisted session for a user. Returns None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT session_id, user_name, started_at, last_interaction_at "
            "FROM sessions WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return PersistedSession(
                session_id=row["session_id"],
                user_name=row["user_name"],
                started_at=row["started_at"],
                last_interaction_at=row["last_interaction_at"],
            )


async def save_session(user_id: str, session: "UserSession"):  # type: ignore[name-defined]
    """
    Upsert the session record for this user.
    Call this whenever session data changes (new session, new message).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO sessions (user_id, session_id, user_name, started_at, last_interaction_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                session_id          = excluded.session_id,
                user_name           = excluded.user_name,
                started_at          = excluded.started_at,
                last_interaction_at = excluded.last_interaction_at
        """, (
            user_id,
            session.session_id,
            session.user_name,
            session.started_at,
            session.last_interaction_at,
        ))
        await db.commit()


async def delete_session(user_id: str):
    """Remove a session record (called on expiry)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()


async def load_all_sessions() -> dict[str, PersistedSession]:
    """
    Load every session row from DB.
    Called once at startup to rebuild the in-memory _sessions dict.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, session_id, user_name, started_at, last_interaction_at FROM sessions"
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row["user_id"]: PersistedSession(
                    session_id=row["session_id"],
                    user_name=row["user_name"],
                    started_at=row["started_at"],
                    last_interaction_at=row["last_interaction_at"],
                )
                for row in rows
            }
