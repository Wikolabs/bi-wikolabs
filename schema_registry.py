"""Schema Registry · extracts MongoDB schemas and stores them in PostgreSQL.

Flow:
  refresh_collection(name)  →  sample client MongoDB  →  upsert into PostgreSQL
  get_full_schema(name)     →  read from PostgreSQL   →  detailed prompt string
  get_all_summaries()       →  read from PostgreSQL   →  {name: one-line summary}
  find_similar_collections()→  pgvector cosine search →  top-k collection names
  needs_refresh()           →  check if registry is empty
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# In-memory cache: collection_name → prompt_summary (one-line)
_SUMMARY_CACHE: dict[str, str] = {}


# ── Document helpers ──────────────────────────────────────────────────────────

def _flatten(doc: dict, prefix: str = "") -> dict:
    """Flatten nested document to dot-notation keys, skip _id."""
    result: dict = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, key))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            result.update(_flatten(v[0], f"{key}[]"))
        else:
            result[key] = v
    return result


def _type_name(value) -> str:
    from bson import ObjectId
    # None must be first · isinstance(None, …) is always False but explicit is safer
    if value is None:               return "null"
    # bool before int · in Python bool is a subclass of int
    if isinstance(value, bool):     return "bool"
    if isinstance(value, int):      return "int"
    if isinstance(value, float):    return "float"
    if isinstance(value, str):      return "str"
    if isinstance(value, list):     return "array"
    if isinstance(value, dict):     return "object"
    if isinstance(value, ObjectId): return "oid"
    if isinstance(value, datetime): return "date"
    return type(value).__name__


def _extract_fields(docs: list[dict]) -> dict:
    """Return {field_path: {types: {type: freq}, enum?: [values]}}."""
    fields: dict[str, dict] = {}
    for doc in docs:
        for key, value in _flatten(doc).items():
            t = _type_name(value)
            entry = fields.setdefault(key, {"types": {}, "_str_vals": []})
            entry["types"][t] = entry["types"].get(t, 0) + 1
            if isinstance(value, str) and len(entry["_str_vals"]) < 20:
                entry["_str_vals"].append(value)

    result = {}
    for key, data in fields.items():
        total = sum(data["types"].values())
        normalized = {t: round(c / total, 2) for t, c in data["types"].items()}
        entry: dict = {"types": normalized}
        # Detect low-cardinality string fields as enums
        if normalized.get("str", 0) >= 0.85:
            distinct = list(set(data["_str_vals"]))
            if 2 <= len(distinct) <= 15:
                entry["enum"] = sorted(distinct)
        result[key] = entry
    return result


def _build_summary(collection_name: str, fields: dict) -> str:
    """One-line summary injected into collection-selector LLM prompt."""
    parts = []
    for field, info in fields.items():
        dominant = max(info["types"], key=info["types"].get)
        if "enum" in info:
            parts.append(f"{field}[{'|'.join(str(v) for v in info['enum'][:6])}]")
        elif dominant == "oid":
            parts.append(f"{field}(ObjectId)")
        elif dominant == "date":
            parts.append(f"{field}(date)")
        else:
            parts.append(field)
    return f"{collection_name}({', '.join(parts)})"


# ── Core refresh ──────────────────────────────────────────────────────────────

async def refresh_collection(collection_name: str, tenant_id: str = "default") -> None:
    """Sample one collection from client MongoDB and upsert schema into PostgreSQL."""
    from db import get_client_db, get_pg_pool
    from embeddings import embed

    db = get_client_db()
    try:
        docs = list(db[collection_name].aggregate([{"$sample": {"size": 500}}]))
    except Exception as exc:
        logger.error("Failed to sample '%s': %s", collection_name, exc)
        return

    if not docs:
        logger.warning("Collection '%s' is empty · skipping.", collection_name)
        return

    fields = _extract_fields(docs)
    summary = _build_summary(collection_name, fields)
    doc_count = db[collection_name].estimated_document_count()
    vec = embed(summary)

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        coll_id = await conn.fetchval(
            """
            INSERT INTO schema_collections (tenant_id, name, doc_count, last_scanned, schema_version)
            VALUES ($1, $2, $3, NOW(), 1)
            ON CONFLICT (tenant_id, name) DO UPDATE
              SET doc_count      = $3,
                  last_scanned   = NOW(),
                  schema_version = schema_collections.schema_version + 1
            RETURNING id
            """,
            tenant_id, collection_name, doc_count,
        )

        # Replace all fields for this collection
        await conn.execute("DELETE FROM schema_fields WHERE collection_id = $1", coll_id)
        if fields:
            await conn.executemany(
                """
                INSERT INTO schema_fields (collection_id, field_path, types, enum_values)
                VALUES ($1, $2, $3, $4)
                """,
                [
                    (
                        coll_id,
                        path,
                        json.dumps(info["types"]),
                        json.dumps(info["enum"]) if "enum" in info else None,
                    )
                    for path, info in fields.items()
                ],
            )

        # Upsert summary + embedding
        await conn.execute(
            """
            INSERT INTO schema_summaries (collection_id, prompt_summary, embedding, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (collection_id) DO UPDATE
              SET prompt_summary = $2,
                  embedding      = $3,
                  updated_at     = NOW()
            """,
            coll_id, summary, vec,
        )

    _SUMMARY_CACHE[collection_name] = summary
    logger.info("Schema refreshed: %s (%d fields)", collection_name, len(fields))


async def refresh_all(tenant_id: str = "default") -> None:
    """Refresh schema for every non-system collection in the client MongoDB."""
    from db import get_client_db

    db = get_client_db()
    collections = [
        c for c in db.list_collection_names()
        if not c.startswith("_") and c != "system.views"
    ]
    logger.info("Schema refresh started: %d collections", len(collections))
    for name in collections:
        await refresh_collection(name, tenant_id)
    logger.info("Schema refresh complete.")


# ── Read helpers ──────────────────────────────────────────────────────────────

async def needs_refresh(tenant_id: str = "default") -> bool:
    """True when the schema registry has no entries (first boot)."""
    from db import get_pg_pool

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM schema_collections WHERE tenant_id = $1", tenant_id
        )
    return count == 0


async def get_all_summaries(tenant_id: str = "default") -> dict[str, str]:
    """Return {collection_name: one-line summary} for all collections."""
    from db import get_pg_pool

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sc.name, ss.prompt_summary
            FROM schema_summaries ss
            JOIN schema_collections sc ON sc.id = ss.collection_id
            WHERE sc.tenant_id = $1
            ORDER BY sc.name
            """,
            tenant_id,
        )
    result = {r["name"]: r["prompt_summary"] for r in rows}
    _SUMMARY_CACHE.update(result)
    return result


async def get_full_schema(collection_name: str, tenant_id: str = "default") -> str:
    """Return a detailed schema string ready to be injected into the LLM prompt."""
    from db import get_pg_pool

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        coll = await conn.fetchrow(
            "SELECT id FROM schema_collections WHERE tenant_id=$1 AND name=$2",
            tenant_id, collection_name,
        )
        if not coll:
            return f"{collection_name}: (schema not yet extracted)"

        rows = await conn.fetch(
            "SELECT field_path, types, enum_values FROM schema_fields WHERE collection_id=$1 ORDER BY field_path",
            coll["id"],
        )

    parts = []
    for row in rows:
        types = json.loads(row["types"])
        dominant = max(types, key=types.get)
        enums = json.loads(row["enum_values"]) if row["enum_values"] else None
        if enums:
            parts.append(f"  {row['field_path']} ({dominant}: {' | '.join(str(v) for v in enums[:8])})")
        else:
            parts.append(f"  {row['field_path']} ({dominant})")

    return collection_name + ":\n" + "\n".join(parts)


async def find_similar_collections(
    question: str,
    top_k: int = 3,
    tenant_id: str = "default",
) -> list[str]:
    """Return the top-k collection names most semantically similar to the question."""
    from db import get_pg_pool
    from embeddings import embed

    vec = embed(question)
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sc.name
            FROM schema_summaries ss
            JOIN schema_collections sc ON sc.id = ss.collection_id
            WHERE sc.tenant_id = $1 AND ss.embedding IS NOT NULL
            ORDER BY ss.embedding <=> $2::vector
            LIMIT $3
            """,
            tenant_id, vec, top_k,
        )
    return [r["name"] for r in rows]
