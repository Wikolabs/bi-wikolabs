-- BI Wikolabs — PostgreSQL schema
-- Our internal application database (NOT the client's MongoDB)

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Schema Registry ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_collections (
    id              SERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,
    doc_count       BIGINT DEFAULT 0,
    last_scanned    TIMESTAMPTZ,
    schema_version  INT DEFAULT 1,
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS schema_fields (
    id              SERIAL PRIMARY KEY,
    collection_id   INT NOT NULL REFERENCES schema_collections(id) ON DELETE CASCADE,
    field_path      TEXT NOT NULL,
    types           JSONB DEFAULT '{}',
    enum_values     JSONB,
    sample_values   JSONB,
    is_indexed      BOOLEAN DEFAULT FALSE,
    UNIQUE (collection_id, field_path)
);

CREATE TABLE IF NOT EXISTS schema_summaries (
    collection_id   INT PRIMARY KEY REFERENCES schema_collections(id) ON DELETE CASCADE,
    prompt_summary  TEXT NOT NULL,
    embedding       VECTOR(384),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast cosine similarity on schema embeddings
CREATE INDEX IF NOT EXISTS idx_schema_emb
    ON schema_summaries USING hnsw (embedding vector_cosine_ops);

-- ── Golden Records ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS golden_records (
    id               SERIAL PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    question         TEXT NOT NULL,
    mql_query        JSONB NOT NULL,
    collection_names TEXT[] DEFAULT '{}',
    embedding        VECTOR(384),
    validated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_golden_emb
    ON golden_records USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_golden_tenant
    ON golden_records (tenant_id);

-- ── Chat History (replaces SQLite data_layer) ─────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_users (
    id          TEXT PRIMARY KEY,
    identifier  TEXT NOT NULL UNIQUE,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    user_id     TEXT REFERENCES chat_users(id),
    metadata    JSONB DEFAULT '{}',
    tags        JSONB DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    deleted     BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_threads_user    ON chat_threads (user_id);
CREATE INDEX IF NOT EXISTS idx_threads_updated ON chat_threads (updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_steps (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT REFERENCES chat_threads(id),
    parent_id   TEXT,
    type        TEXT,
    name        TEXT,
    input_      TEXT DEFAULT '',
    output      TEXT DEFAULT '',
    metadata    JSONB DEFAULT '{}',
    is_error    BOOLEAN DEFAULT FALSE,
    favorite    BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_steps_thread ON chat_steps (thread_id);

CREATE TABLE IF NOT EXISTS chat_elements (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT,
    step_id     TEXT,
    type        TEXT,
    name        TEXT,
    url         TEXT,
    metadata    JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS chat_feedbacks (
    id          TEXT PRIMARY KEY,
    step_id     TEXT REFERENCES chat_steps(id),
    value       INT,
    comment     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
