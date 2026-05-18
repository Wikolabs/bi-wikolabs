"""PostgreSQL-backed data layer for Chainlit thread history.

Replaces the previous SQLiteDataLayer. All data lives in our internal
PostgreSQL database — never in the client's MongoDB.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from chainlit.data import BaseDataLayer
from chainlit.types import Feedback, Pagination, PaginatedResponse, ThreadDict, ThreadFilter
from chainlit.user import PersistedUser, User


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, str):
        return value
    return str(value) if value is not None else ""


class PostgresDataLayer(BaseDataLayer):
    """Chainlit data persistence backed by our internal PostgreSQL."""

    async def _pool(self):
        from db import get_pg_pool
        return await get_pg_pool()

    # ── Users ─────────────────────────────────────────────────────────────────

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM chat_users WHERE identifier = $1", identifier
            )
        if not row:
            return None
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=dict(row["metadata"]) if row["metadata"] else {},
            createdAt=row["created_at"].isoformat(),
        )

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        pool = await self._pool()
        uid = str(uuid.uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_users (id, identifier, metadata)
                VALUES ($1, $2, $3)
                ON CONFLICT (identifier) DO NOTHING
                """,
                uid, user.identifier, json.dumps(user.metadata or {}),
            )
            row = await conn.fetchrow(
                "SELECT * FROM chat_users WHERE identifier = $1", user.identifier
            )
        return PersistedUser(
            id=row["id"],
            identifier=row["identifier"],
            metadata=dict(row["metadata"]) if row["metadata"] else {},
            createdAt=row["created_at"].isoformat(),
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
        pool = await self._pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM chat_threads WHERE id = $1", thread_id
            )
            if exists:
                await conn.execute(
                    """
                    UPDATE chat_threads
                    SET name      = COALESCE($2, name),
                        user_id   = COALESCE($3, user_id),
                        metadata  = COALESCE($4::jsonb, metadata),
                        tags      = COALESCE($5::jsonb, tags),
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    thread_id,
                    name,
                    user_id,
                    json.dumps(metadata) if metadata is not None else None,
                    json.dumps(tags) if tags is not None else None,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO chat_threads (id, name, user_id, metadata, tags)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    thread_id,
                    name or "New conversation",
                    user_id,
                    json.dumps(metadata or {}),
                    json.dumps(tags or []),
                )

    async def delete_thread(self, thread_id: str) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chat_threads SET deleted = TRUE WHERE id = $1", thread_id
            )
        return True

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            if filters.userId:
                rows = await conn.fetch(
                    """
                    SELECT t.*,
                           (SELECT output FROM chat_steps
                            WHERE thread_id = t.id AND type = 'assistant_message'
                            ORDER BY created_at DESC LIMIT 1) AS last_output
                    FROM chat_threads t
                    WHERE t.deleted = FALSE AND t.user_id = $1
                    ORDER BY t.updated_at DESC
                    LIMIT $2
                    """,
                    filters.userId, pagination.first + 1,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT t.*,
                           (SELECT output FROM chat_steps
                            WHERE thread_id = t.id AND type = 'assistant_message'
                            ORDER BY created_at DESC LIMIT 1) AS last_output
                    FROM chat_threads t
                    WHERE t.deleted = FALSE
                    ORDER BY t.updated_at DESC
                    LIMIT $1
                    """,
                    pagination.first + 1,
                )

        has_next = len(rows) > pagination.first
        rows = rows[: pagination.first]

        threads = [
            ThreadDict(
                id=r["id"],
                name=r["name"] or "New conversation",
                createdAt=r["created_at"].isoformat(),
                userId=r["user_id"],
                userIdentifier=None,
                metadata=dict(r["metadata"]) if r["metadata"] else {},
                steps=[],
                tags=list(r["tags"]) if r["tags"] else [],
            )
            for r in rows
        ]
        return PaginatedResponse(
            data=threads,
            pageInfo={
                "hasNextPage": has_next,
                "startCursor": rows[0]["id"] if rows else None,
                "endCursor":   rows[-1]["id"] if rows else None,
            },
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            t = await conn.fetchrow(
                "SELECT * FROM chat_threads WHERE id = $1 AND deleted = FALSE", thread_id
            )
            if not t:
                return None
            steps = await conn.fetch(
                "SELECT * FROM chat_steps WHERE thread_id = $1 ORDER BY created_at",
                thread_id,
            )

        return ThreadDict(
            id=t["id"],
            name=t["name"] or "New conversation",
            createdAt=t["created_at"].isoformat(),
            userId=t["user_id"],
            userIdentifier=None,
            metadata=dict(t["metadata"]) if t["metadata"] else {},
            tags=list(t["tags"]) if t["tags"] else [],
            steps=[
                {
                    "id":        s["id"],
                    "threadId":  thread_id,
                    "parentId":  s["parent_id"],
                    "type":      s["type"] or "undefined",
                    "name":      s["name"] or "",
                    "input":     s["input_"] or "",
                    "output":    s["output"] or "",
                    "metadata":  dict(s["metadata"]) if s["metadata"] else {},
                    "createdAt": s["created_at"].isoformat(),
                    "isError":   bool(s["is_error"]),
                    "streaming": False,
                    "tags":      [],
                }
                for s in steps
            ],
        )

    async def get_thread_author(self, thread_id: str) -> Optional[str]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_id FROM chat_threads WHERE id = $1", thread_id
            )

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def create_step(self, step_dict: Dict) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_steps
                    (id, thread_id, parent_id, type, name, input_, output, metadata, is_error, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (id) DO UPDATE
                  SET output    = EXCLUDED.output,
                      is_error  = EXCLUDED.is_error,
                      metadata  = EXCLUDED.metadata
                """,
                step_dict.get("id", str(uuid.uuid4())),
                step_dict.get("threadId"),
                step_dict.get("parentId"),
                step_dict.get("type"),
                step_dict.get("name"),
                _serialize(step_dict.get("input", "")),
                _serialize(step_dict.get("output", "")),
                json.dumps(step_dict.get("metadata", {})),
                bool(step_dict.get("isError", False)),
                step_dict.get("createdAt", _now()),
            )

    async def update_step(self, step_dict: Dict) -> None:
        await self.create_step(step_dict)

    async def delete_step(self, step_id: str) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM chat_steps WHERE id = $1", step_id)

    async def get_favorite_steps(self, thread_id: str) -> List[Dict]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM chat_steps WHERE thread_id = $1 AND favorite = TRUE", thread_id
            )
        return [dict(r) for r in rows]

    async def set_step_favorite(self, step_id: str, is_favorite: bool) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chat_steps SET favorite = $1 WHERE id = $2", is_favorite, step_id
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
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_feedbacks (id, step_id, value, comment)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE
                  SET value = EXCLUDED.value, comment = EXCLUDED.comment
                """,
                fid, feedback.forId, feedback.value, feedback.comment,
            )
        return fid

    async def delete_feedback(self, feedback_id: str) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM chat_feedbacks WHERE id = $1", feedback_id)
        return True

    # ── Misc ──────────────────────────────────────────────────────────────────

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        from db import close_pg_pool
        await close_pg_pool()
