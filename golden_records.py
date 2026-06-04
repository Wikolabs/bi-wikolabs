"""Golden Records · validated (question → MQL) pairs stored in PostgreSQL.

Every time a user marks a query result as correct, we save the
(question, MQL, collections) triple with its embedding. Future similar
questions retrieve these as few-shot context, dramatically reducing
token usage and improving accuracy.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def search_similar(
    question: str,
    top_k: int = 3,
    min_similarity: float = 0.82,
    tenant_id: str = "default",
) -> list[dict]:
    """Return golden records whose question is semantically close to `question`.

    Each result: {question, mql, collections, similarity}
    """
    from db import get_pg_pool
    from embeddings import embed

    vec = embed(question)
    pool = await get_pg_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT question,
                   mql_query,
                   collection_names,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM golden_records
            WHERE tenant_id = $2
              AND 1 - (embedding <=> $1::vector) >= $3
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,
            vec, tenant_id, min_similarity, top_k,
        )

    return [
        {
            "question":    row["question"],
            "mql":         dict(row["mql_query"]),
            "collections": list(row["collection_names"] or []),
            "similarity":  round(float(row["similarity"]), 3),
        }
        for row in rows
    ]


async def save(
    question: str,
    mql_query: dict,
    collection_names: list[str],
    tenant_id: str = "default",
) -> None:
    """Persist a validated (question → MQL) pair."""
    from db import get_pg_pool
    from embeddings import embed

    vec = embed(question)
    pool = await get_pg_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO golden_records
                (tenant_id, question, mql_query, collection_names, embedding)
            VALUES ($1, $2, $3, $4, $5)
            """,
            tenant_id,
            question,
            json.dumps(mql_query),
            collection_names,
            vec,
        )

    logger.info("Golden record saved: %.60s", question)


async def delete(record_id: int, tenant_id: str = "default") -> None:
    from db import get_pg_pool

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM golden_records WHERE id = $1 AND tenant_id = $2",
            record_id, tenant_id,
        )
