"""
Conversation persistence.

Two pieces, both backed by the same Postgres database when DATABASE_URL is set:
  1. LangGraph checkpointer — stores each conversation's full agent state (messages, plan, ...),
     keyed by thread_id. Survives restarts, redeploys and Cloud Run scale-to-zero.
  2. Chat index (table `chat_threads`) — which chats belong to which owner, plus a title and
     timestamps, so the UI can show a "Your chats" list.

Without DATABASE_URL (local development) both fall back to in-process memory, exactly like
before: everything works, but conversations are lost when the API restarts.
"""
import re
import threading
from datetime import datetime, timezone

import logfire

from app.config import settings

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def valid_id(value) -> bool:
    """Chat / owner IDs are opaque tokens: letters, digits, '-' and '_' only (max 64 chars)."""
    return isinstance(value, str) and bool(_ID_RE.match(value))


class ChatOwnershipError(Exception):
    """The chat exists but belongs to a different owner."""


# ---------------------------------------------------------------------------------------
# Postgres connection pool (shared by the checkpointer and the chat index)
# ---------------------------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(
                settings.DATABASE_URL,
                min_size=1,
                max_size=5,
                max_idle=300,      # serverless Postgres (e.g. Neon) suspends idle compute
                open=True,
                # Re-check a connection before handing it out: an idle connection may have been
                # dropped while the database (or this Cloud Run instance) was scaled to zero.
                check=ConnectionPool.check_connection,
                # Required by LangGraph's PostgresSaver
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
    return _pool


def close() -> None:
    """Close the pool on API shutdown (no-op without a database)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def uses_database() -> bool:
    return bool(settings.DATABASE_URL)


# ---------------------------------------------------------------------------------------
# 1. LangGraph checkpointer
# ---------------------------------------------------------------------------------------
def create_checkpointer():
    if uses_database():
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver(_get_pool())
        checkpointer.setup()  # creates/migrates LangGraph's checkpoint tables (idempotent)
        logfire.info("💾 Conversation memory: Postgres (persistent).")
        return checkpointer

    from langgraph.checkpoint.memory import MemorySaver

    logfire.warning("💾 DATABASE_URL not set — conversation memory is in-process and lost on restart.")
    return MemorySaver()


# ---------------------------------------------------------------------------------------
# 2. Chat index
# ---------------------------------------------------------------------------------------
class _MemoryChatIndex:
    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    def touch(self, thread_id: str, owner_id: str, title: str) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._rows.get(thread_id)
            if row is None:
                self._rows[thread_id] = {"thread_id": thread_id, "owner_id": owner_id, "title": title,
                                         "created_at": now, "updated_at": now}
            elif row["owner_id"] != owner_id:
                raise ChatOwnershipError(thread_id)
            else:
                row["updated_at"] = now

    def owner_of(self, thread_id: str):
        row = self._rows.get(thread_id)
        return row["owner_id"] if row else None

    def list(self, owner_id: str, limit: int = 30) -> list[dict]:
        rows = [r for r in self._rows.values() if r["owner_id"] == owner_id]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return [{k: r[k] for k in ("thread_id", "title", "updated_at")} for r in rows[:limit]]

    def delete(self, thread_id: str, owner_id: str) -> bool:
        with self._lock:
            row = self._rows.get(thread_id)
            if row and row["owner_id"] == owner_id:
                del self._rows[thread_id]
                return True
        return False


class _PostgresChatIndex:
    def __init__(self):
        with _get_pool().connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_threads (
                    thread_id  TEXT PRIMARY KEY,
                    owner_id   TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS chat_threads_owner_idx "
                "ON chat_threads (owner_id, updated_at DESC)"
            )

    def touch(self, thread_id: str, owner_id: str, title: str) -> None:
        # Insert a new chat, or bump updated_at — but only if it belongs to the same owner.
        # When the WHERE clause fails (different owner) no row is returned.
        with _get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO chat_threads (thread_id, owner_id, title) VALUES (%s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()
                WHERE chat_threads.owner_id = EXCLUDED.owner_id
                RETURNING owner_id
                """,
                (thread_id, owner_id, title),
            ).fetchone()
        if row is None:
            raise ChatOwnershipError(thread_id)

    def owner_of(self, thread_id: str):
        with _get_pool().connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM chat_threads WHERE thread_id = %s", (thread_id,)
            ).fetchone()
        return row["owner_id"] if row else None

    def list(self, owner_id: str, limit: int = 30) -> list[dict]:
        with _get_pool().connection() as conn:
            return conn.execute(
                "SELECT thread_id, title, updated_at FROM chat_threads "
                "WHERE owner_id = %s ORDER BY updated_at DESC LIMIT %s",
                (owner_id, limit),
            ).fetchall()

    def delete(self, thread_id: str, owner_id: str) -> bool:
        with _get_pool().connection() as conn:
            row = conn.execute(
                "DELETE FROM chat_threads WHERE thread_id = %s AND owner_id = %s RETURNING thread_id",
                (thread_id, owner_id),
            ).fetchone()
        return row is not None


_chat_index = None
_index_lock = threading.Lock()


def chat_index():
    global _chat_index
    with _index_lock:
        if _chat_index is None:
            _chat_index = _PostgresChatIndex() if uses_database() else _MemoryChatIndex()
    return _chat_index


def make_title(first_message: str, max_len: int = 60) -> str:
    text = " ".join(first_message.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"
