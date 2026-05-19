"""Database connections.

- get_client_db()  → pymongo Database  (client's MongoDB, READ-ONLY)
- get_pg_pool()    → asyncpg Pool       (our internal PostgreSQL, read/write)
"""

import asyncio
import os

import asyncpg
from pymongo import MongoClient

_mongo_client: MongoClient | None = None
_pg_pool: asyncpg.Pool | None = None
_pg_lock: asyncio.Lock | None = None  # created lazily inside the running event loop


def get_client_db():
    """Return the client's MongoDB database (read-only connection).

    Tries get_default_database() first (works when the URI includes /dbname).
    Falls back to MONGODB_DB env var, then to "bi_wikolabs", so bare URIs
    like mongodb://host:27017 work without error.
    """
    global _mongo_client
    if _mongo_client is None:
        uri = os.getenv("MONGODB_URI", "mongodb://mongo:27017")
        _mongo_client = MongoClient(uri, readPreference="secondaryPreferred")
    try:
        return _mongo_client.get_default_database()
    except Exception:
        db_name = os.getenv("MONGODB_DB", "bi_wikolabs")
        return _mongo_client[db_name]


async def get_pg_pool() -> asyncpg.Pool:
    """Return the shared asyncpg connection pool to our internal PostgreSQL."""
    global _pg_pool, _pg_lock
    if _pg_pool is not None:
        return _pg_pool
    if _pg_lock is None:
        _pg_lock = asyncio.Lock()
    async with _pg_lock:
        if _pg_pool is None:
            dsn = os.getenv("POSTGRES_URI")

            # Safety net: ensure pgvector extension exists before pool creation.
            # The primary mechanism is initdb/01_extensions.sql (runs on fresh
            # databases via /docker-entrypoint-initdb.d). This covers existing
            # databases that pre-date that file.
            try:
                boot = await asyncpg.connect(dsn=dsn)
                try:
                    await boot.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                finally:
                    await boot.close()
            except Exception as _ext_err:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "Could not ensure vector extension: %s", _ext_err
                )

            from pgvector.asyncpg import register_vector

            _pg_pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=2,
                max_size=10,
                init=register_vector,
            )
    return _pg_pool


async def close_pg_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
