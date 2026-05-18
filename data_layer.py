"""SQLite-backed data layer for Chainlit thread history."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from chainlit.data import BaseDataLayer
from chainlit.types import Feedback, Pagination, PaginatedResponse, ThreadDict, ThreadFilter
from chainlit.user import PersistedUser, User


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteDataLayer(BaseDataLayer):
    def __init__(self, db_path: str = "/data/threads.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id          TEXT PRIMARY KEY,
                    identifier  TEXT UNIQUE NOT NULL,
                    metadata    TEXT DEFAULT '{}',
                    created_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id          TEXT PRIMARY KEY,
                    name        TEXT,
                    user_id     TEXT,
                    metadata    TEXT DEFAULT '{}',
                    tags        TEXT DEFAULT '[]',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    deleted     INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id          TEXT PRIMARY KEY,
                    thread_id   TEXT NOT NULL,
                    type        TEXT,
                    name        TEXT,
                    input       TEXT,
                    output      TEXT,
                    metadata    TEXT DEFAULT '{}',
                    is_error    INTEGER DEFAULT 0,
                    favorite    INTEGER DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id)
                );
                CREATE TABLE IF NOT EXISTS elements (
                    id          TEXT PRIMARY KEY,
                    thread_id   TEXT,
                    type        TEXT,
                    name        TEXT,
                    url         TEXT,
                    metadata    TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS feedbacks (
                    id          TEXT PRIMARY KEY,
                    step_id     TEXT,
                    value       INTEGER,
                    comment     TEXT,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_threads_user ON threads(user_id);
                CREATE INDEX IF NOT EXISTS idx_steps_thread ON steps(thread_id);
            """)

    # ── Users ────────────────────────────────────────────────────────────────

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE identifier = ?", (identifier,)
            ).fetchone()
        if not row:
            return None
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=json.loads(row["metadata"]),
            createdAt=row["created_at"],
        )

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        uid = str(uuid.uuid4())
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)",
                (uid, user.identifier, json.dumps(user.metadata or {}), now),
            )
            row = c.execute(
                "SELECT * FROM users WHERE identifier = ?", (user.identifier,)
            ).fetchone()
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=json.loads(row["metadata"]),
            createdAt=row["created_at"],
        )

    # ── Threads ───────────────────────────────────────────────────────────────

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        now = _now()
        with self._conn() as c:
            existing = c.execute(
                "SELECT * FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if existing:
                c.execute(
                    """UPDATE threads
                       SET name=COALESCE(?, name),
                           user_id=COALESCE(?, user_id),
                           metadata=COALESCE(?, metadata),
                           tags=COALESCE(?, tags),
                           updated_at=?
                       WHERE id=?""",
                    (
                        name,
                        user_id,
                        json.dumps(metadata) if metadata is not None else None,
                        json.dumps(tags) if tags is not None else None,
                        now,
                        thread_id,
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        thread_id,
                        name or "New conversation",
                        user_id,
                        json.dumps(metadata or {}),
                        json.dumps(tags or []),
                        now,
                        now,
                    ),
                )

    async def delete_thread(self, thread_id: str) -> bool:
        with self._conn() as c:
            c.execute("UPDATE threads SET deleted=1 WHERE id=?", (thread_id,))
        return True

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        with self._conn() as c:
            user_filter = ""
            params: list = [pagination.first + 1]
            if filters.userId:
                user_filter = "AND user_id = ?"
                params.insert(0, filters.userId)

            rows = c.execute(
                f"""SELECT t.*, GROUP_CONCAT(s.output, '|||') AS last_output
                    FROM threads t
                    LEFT JOIN steps s ON s.thread_id = t.id AND s.type = 'assistant_message'
                    WHERE t.deleted = 0 {user_filter}
                    GROUP BY t.id
                    ORDER BY t.updated_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()

        has_next = len(rows) > pagination.first
        rows = rows[: pagination.first]

        threads = []
        for r in rows:
            outputs = (r["last_output"] or "").split("|||")
            preview = outputs[-1][:100] if outputs else ""
            threads.append(
                ThreadDict(
                    id=r["id"],
                    name=r["name"] or "New conversation",
                    createdAt=r["created_at"],
                    userId=r["user_id"],
                    userIdentifier=None,
                    metadata=json.loads(r["metadata"]),
                    steps=[],
                    tags=json.loads(r["tags"]),
                )
            )
        return PaginatedResponse(
            data=threads,
            pageInfo={
                "hasNextPage": has_next,
                "startCursor": rows[0]["id"] if rows else None,
                "endCursor": rows[-1]["id"] if rows else None,
            },
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        with self._conn() as c:
            t = c.execute(
                "SELECT * FROM threads WHERE id=? AND deleted=0", (thread_id,)
            ).fetchone()
            if not t:
                return None
            steps = c.execute(
                "SELECT * FROM steps WHERE thread_id=? ORDER BY created_at",
                (thread_id,),
            ).fetchall()

        return ThreadDict(
            id=t["id"],
            name=t["name"] or "New conversation",
            createdAt=t["created_at"],
            userId=t["user_id"],
            userIdentifier=None,
            metadata=json.loads(t["metadata"]),
            tags=json.loads(t["tags"]),
            steps=[
                {
                    "id": s["id"],
                    "threadId": thread_id,
                    "type": s["type"],
                    "name": s["name"],
                    "input": s["input"],
                    "output": s["output"],
                    "metadata": json.loads(s["metadata"]),
                    "createdAt": s["created_at"],
                    "isError": bool(s["is_error"]),
                }
                for s in steps
            ],
        )

    async def get_thread_author(self, thread_id: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT user_id FROM threads WHERE id=?", (thread_id,)
            ).fetchone()
        return row["user_id"] if row else None

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def create_step(self, step_dict: Dict) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    step_dict.get("id", str(uuid.uuid4())),
                    step_dict.get("threadId"),
                    step_dict.get("type"),
                    step_dict.get("name"),
                    json.dumps(step_dict.get("input", "")),
                    json.dumps(step_dict.get("output", "")),
                    json.dumps(step_dict.get("metadata", {})),
                    1 if step_dict.get("isError") else 0,
                    step_dict.get("createdAt", now),
                ),
            )

    async def update_step(self, step_dict: Dict) -> None:
        await self.create_step(step_dict)

    async def delete_step(self, step_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM steps WHERE id=?", (step_id,))

    async def get_favorite_steps(self, thread_id: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM steps WHERE thread_id=? AND favorite=1", (thread_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    async def set_step_favorite(self, step_id: str, is_favorite: bool) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE steps SET favorite=? WHERE id=?", (1 if is_favorite else 0, step_id)
            )

    # ── Elements ──────────────────────────────────────────────────────────────

    async def get_element(self, thread_id: str, element_id: str) -> Optional[Dict]:
        return None

    async def create_element(self, element) -> Optional[Dict]:
        return None

    async def delete_element(self, element_id: str, thread_id: Optional[str] = None) -> None:
        pass

    # ── Feedback ──────────────────────────────────────────────────────────────

    async def upsert_feedback(self, feedback: Feedback) -> str:
        fid = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO feedbacks VALUES (?, ?, ?, ?, ?)",
                (fid, feedback.forId, feedback.value, feedback.comment, _now()),
            )
        return fid

    async def delete_feedback(self, feedback_id: str) -> bool:
        with self._conn() as c:
            c.execute("DELETE FROM feedbacks WHERE id=?", (feedback_id,))
        return True

    # ── Misc ──────────────────────────────────────────────────────────────────

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass
