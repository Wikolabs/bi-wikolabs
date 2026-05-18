"""Apply SQL migrations to PostgreSQL on startup."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations() -> None:
    from db import get_pg_pool

    pool = await get_pg_pool()

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            already = await conn.fetchval(
                "SELECT 1 FROM _migrations WHERE filename = $1", sql_file.name
            )
            if already:
                continue

            sql = sql_file.read_text(encoding="utf-8")
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)", sql_file.name
            )
            logger.info("Migration applied: %s", sql_file.name)

    logger.info("All migrations up to date.")
