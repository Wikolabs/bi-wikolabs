-- PostgreSQL extensions required by bi-wikolabs.
-- This file is mounted into /docker-entrypoint-initdb.d/ and runs
-- automatically on first database initialization (empty pg_data volume).
-- Scripts here are idempotent and safe to re-run.

CREATE EXTENSION IF NOT EXISTS vector;
