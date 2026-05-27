# BI Wikolabs — Agent BI en langage naturel

> Posez vos questions business en français. Obtenez graphiques, analyses et rapports — sans SQL, sans analyste intermédiaire.

[![Chainlit](https://img.shields.io/badge/Chainlit-1.x-FF6B35?style=flat)](https://chainlit.io)
[![Groq](https://img.shields.io/badge/Groq-LLaMA_3.3_70B-FF6B35?style=flat)](https://groq.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248?style=flat&logo=mongodb)](https://mongodb.com)
[![pgvector](https://img.shields.io/badge/pgvector-0.3-336791?style=flat&logo=postgresql)](https://github.com/pgvector/pgvector)

An enterprise-grade **natural language BI chatbot** powered by Groq LLM, MongoDB, and PostgreSQL+pgvector. Ask business questions in plain English and get instant insights, interactive charts, and exportable reports — no SQL or query language needed.

**Live demo:** [bi.wikolabs.com](https://bi.wikolabs.com)  
**Domaine :** Business Intelligence / Natural Language Querying / Data Analytics

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [6-Step Query Pipeline](#6-step-query-pipeline)
- [Technology Stack](#technology-stack)
- [Data Model](#data-model)
- [Dynamic Schema Extraction](#dynamic-schema-extraction)
- [Chat History Persistence](#chat-history-persistence)
- [Project Structure](#project-structure)
- [Getting Started (Local Development)](#getting-started-local-development)
- [Deployment with Docker](#deployment-with-docker)
- [CI/CD — GitHub Actions](#cicd--github-actions)
- [GitHub Secrets Reference](#github-secrets-reference)
- [Environment Variables](#environment-variables)
- [Test Suite](#test-suite)
- [Example Queries](#example-queries)
- [Export Features](#export-features)
- [Contributing & Developer Onboarding](#contributing--developer-onboarding)

---

## Overview

BI Wikolabs translates natural language questions into MongoDB aggregation pipelines, executes them with automatic error correction, and returns business narratives with interactive Plotly charts — all within a Chainlit chat interface.

**Key capabilities:**
- Natural language → MongoDB query (MQL) generation
- Compound question decomposition ("Show revenue AND top products")
- Root-cause drill-down for WHY questions
- Entity name resolution to ObjectIds (resolves "Acme Corp" → `ObjectId("...")`)
- Self-correcting queries (retry with error feedback, up to 3 attempts)
- Dynamic schema extraction — LLM always sees current field names from MongoDB
- Semantic collection search via pgvector embeddings
- Golden records — save verified question/query pairs for future few-shot examples
- Export results as Excel (.xlsx) or PDF
- Full chat history persisted in PostgreSQL

---

## Architecture

```mermaid
flowchart TD
    UI["Chainlit Chat UI\n(app.py)"]
    ORCH["Pipeline Orchestrator\n(pipeline/orchestrator.py)"]
    S0["Step 0 — Compound Question Decomposition"]
    S1["Step 1 — Query Analysis\nclassify + extract entities"]
    S2["Step 2 — Entity Resolution\nname → ObjectId"]
    S3["Step 3 — Collection Selection\npgvector similarity + LLM"]
    S4["Step 4 — MQL Generation\ndynamic schema + golden records"]
    S5["Step 5 — Execute + Auto-Retry\nmax 3 attempts"]
    S6["Step 6 — Narrative + Chart + DataFrame"]
    OUT1["Chainlit message\nnarrative + chart"]
    OUT2["Export XLSX / PDF\n(exporter.py)"]

    UI -->|user message| ORCH
    ORCH --> S0 --> S1
    S1 --> S2 & S3
    S2 & S3 --> S4 --> S5 --> S6
    S6 --> OUT1
    S6 --> OUT2
```

### Infrastructure (Docker Compose)

```mermaid
flowchart TD
    Internet[":80 / :443\n(public)"]
    Caddy["Caddy 2\nreverse proxy + TLS\nserves /exports/* from app_data volume"]
    App["Chainlit app:8000\nPython app\nwrites exports to /data/exports/"]
    MongoDB["MongoDB 7\nmongo:27017\nclient data — 7 collections"]
    PG["PostgreSQL 16 + pgvector\npostgres:5432\nschema registry + chat history\n+ golden records"]

    Internet --> Caddy
    Caddy -->|"proxy to :8000"| App
    App -->|pymongo read-only| MongoDB
    App -->|asyncpg read/write| PG
```

### Two-Database Architecture

| Database | Role | Access |
|---|---|---|
| **MongoDB 7** | Client's business data — orders, customers, products, employees, transactions, etc. | Read-only via pymongo |
| **PostgreSQL 16 + pgvector** | Internal app data — schema registry, chat history (threads/steps/users), golden records, vector embeddings | Read/write via asyncpg |

---

## 6-Step Query Pipeline

The pipeline is the core of the system. Each step is a focused LLM call or database operation.

### Step 0 — Compound Question Decomposition (`pipeline/orchestrator.py`)

Detects compound questions using regex patterns ("and also", "as well as", "both X and Y") and asks the LLM to split them into independent sub-questions. All sub-questions run in parallel and results are merged.

### Step 1 — Query Analysis (`pipeline/analyzer.py`)

**Model:** `llama-3.1-8b-instant` (fast, low-latency)

Classifies the question and extracts named entities in a single LLM call using `instructor` + Pydantic for structured output.

| Output field | Description |
|---|---|
| `query_type` | `smalltalk` / `lookup` / `list` / `metric` / `ranking` / `comparison` |
| `is_why_question` | `true` if the user wants a root-cause explanation |
| `intent` | One-line description of what the user wants |
| `entities` | List of `{type, value}` named entities found in the question |

If `query_type` is `smalltalk`, the pipeline stops here and returns a greeting.

### Step 2 — Entity Resolution (`pipeline/entity_resolver.py`) ← parallel

Converts entity names to MongoDB ObjectIds via case-insensitive regex search across collections (`customers`, `employees`, `products`, `contacts`, `suppliers`). Prevents fragile name-based queries by resolving to authoritative IDs.

### Step 3 — Collection Selection (`pipeline/collection_selector.py`) ← parallel

Selects the 1–3 most relevant MongoDB collections for the query using a 3-tier fallback:

1. **pgvector similarity search** — cosine similarity on schema embeddings stored in PostgreSQL
2. **LLM selection** — `llama-3.1-8b-instant` with collection summaries if similarity score is low
3. **Keyword fallback** — deterministic keyword matching if both LLM tiers fail

> Steps 2 and 3 run **concurrently** via `asyncio.gather()` — saving ~500ms per query.

### Step 4 — MQL Generation (`pipeline/mql_generator.py`)

**Model:** `llama-3.3-70b-versatile` (highest reasoning quality)

Generates a valid MongoDB query JSON given the question, entity map, collection schemas, and few-shot examples. Uses `instructor` + Pydantic (`MongoQuery`) for guaranteed valid output with auto-retries.

The system prompt includes:
- **Dynamic schema** (from PostgreSQL `schema_registry`, extracted from live MongoDB docs)
- **Entity context** with resolved ObjectIds and correct filter syntax
- **Golden record examples** — similar past verified question/query pairs retrieved by pgvector
- **Static few-shot examples** per query type

Outputs a `MongoQuery` Pydantic model serialized to:
```json
{
  "collection": "orders",
  "operation": "aggregate",
  "pipeline": [ ... ]
}
```

Supports `{"$oid": "..."}` syntax for ObjectIds, which are auto-converted to BSON at execution time.

### Step 5 — Execute & Auto-Retry (`pipeline/executor.py`)

Runs the generated query against MongoDB. On failure, feeds the error message back to Step 4 for automatic self-correction (up to 3 retries).

### Step 6 — Response Generation (`pipeline/responder.py`)

**Model:** `llama-3.3-70b-versatile`

1. Builds a Pandas DataFrame from results
2. Generates a business narrative (LLM)
3. Selects the appropriate Plotly chart type:
   - **None** — for single-value metrics and entity lookups
   - **Horizontal bar** — rankings (sorted values)
   - **Pie** — 2–10 category comparisons with 1 metric
   - **Grouped bar** — multi-metric comparisons
   - **Vertical bar** — list/trend data
4. If `is_why_question = true`, triggers a second MQL query for root-cause drill-down

**Total LLM calls per query:** 3 minimum (analyze + generate + respond), 4 if collection selection uses LLM, +1 for compound question decomposition, +1 for WHY drill-down.

---

## Technology Stack

| Layer | Technology | Role |
|---|---|---|
| Chat UI | [Chainlit](https://docs.chainlit.io/) ≥ 1.0 | Python-native chat framework, streaming, file uploads |
| LLM Provider | [Groq](https://groq.com/) | Ultra-fast inference for llama-3.x models |
| LLM Models | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` | High-quality reasoning + fast classification |
| Structured Output | [instructor](https://python.useinstructor.com/) ≥ 1.3 | Pydantic-validated LLM responses with auto-retry |
| Client Database | MongoDB 7 | Document store for all business data (read-only) |
| MongoDB Driver | pymongo ≥ 4.8 | Sync MongoDB Python driver |
| Internal Database | PostgreSQL 16 + pgvector | Schema registry, chat history, golden records, embeddings |
| PostgreSQL Driver | asyncpg ≥ 0.29 | Async PostgreSQL driver |
| Vector Search | pgvector ≥ 0.3 + HNSW index | Cosine similarity for collection selection and golden records |
| Embeddings | [FastEmbed](https://github.com/qdrant/fastembed) ≥ 0.3.6 | ONNX-based 384-dim embeddings (no PyTorch needed) |
| Data Processing | Pandas ≥ 2.0 | DataFrame manipulation and analysis |
| Charts | Plotly ≥ 5.0 | Interactive charts in Chainlit |
| Excel Export | openpyxl ≥ 3.1 | `.xlsx` file generation |
| PDF Export | fpdf2 ≥ 2.7 | Landscape PDF reports |
| Demo Data | Faker ≥ 24.0 | Realistic synthetic data generation |
| Container | Docker + Docker Compose | Service orchestration |
| Reverse Proxy | Caddy 2 | TLS termination (Let's Encrypt) + static file serving |
| CI/CD | GitHub Actions | Auto-deploy on push to `main` |
| Testing | pytest ≥ 8.0, pytest-asyncio ≥ 0.23 | 198 tests across unit + integration suites |

---

## Data Model

MongoDB database `bi_wikolabs` contains 7 collections with ObjectId-based foreign keys.

### Collections

#### `customers` (80 documents)
| Field | Type | Notes |
|---|---|---|
| `name` | String | Company name |
| `industry` | String | Technology, Finance, Healthcare, … |
| `segment` | String | `Enterprise` / `SMB` / `Individual` |
| `region` | String | North, South, East, West, International |
| `country` | String | |
| `account_manager_id` | ObjectId | → `employees._id` |
| `status` | String | `active` / `inactive` / `churned` |
| `annual_revenue` | Number | |
| `lifetime_value` | Number | |
| `registration_date` | Date | |
| `is_active` | Boolean | |

#### `contacts` (30+ documents)
| Field | Type | Notes |
|---|---|---|
| `name` | String | |
| `title` | String | CEO, CTO, CFO, VP Sales, … |
| `email` | String | |
| `phone` | String | |
| `company_id` | ObjectId | → `customers._id` |
| `company_name` | String | Denormalized for convenience |
| `is_primary` | Boolean | Primary contact for the company |
| `last_contact_date` | Date | |

#### `employees` (48 documents)
| Field | Type | Notes |
|---|---|---|
| `name` | String | |
| `department` | String | Sales, Engineering, Finance, Marketing, HR, Operations |
| `role` | String | |
| `salary` | Number | |
| `hire_date` | Date | |
| `manager_id` | ObjectId | → `employees._id` (self-reference) |
| `performance_score` | Number | 1.0–5.0 |
| `is_active` | Boolean | |
| `quota` | Number | Sales quota (Sales dept only) |
| `quota_attainment` | Number | % of quota achieved |

#### `products` (26 documents)
| Field | Type | Notes |
|---|---|---|
| `sku` | String | Product code |
| `name` | String | |
| `category` | String | Electronics, Software, Furniture, Services |
| `unit_price` | Number | |
| `cost_price` | Number | |
| `margin_pct` | Number | Gross margin percentage |
| `supplier_id` | ObjectId | → `suppliers._id` |
| `stock_quantity` | Number | |
| `is_active` | Boolean | |

#### `suppliers` (8 documents)
| Field | Type | Notes |
|---|---|---|
| `name` | String | Dell, Microsoft, Herman Miller, … |
| `country` | String | |
| `lead_time_days` | Number | |
| `contact_email` | String | |
| `is_active` | Boolean | |

#### `orders` (900 documents)
| Field | Type | Notes |
|---|---|---|
| `order_number` | String | |
| `customer_id` | ObjectId | → `customers._id` |
| `product_id` | ObjectId | → `products._id` |
| `account_manager_id` | ObjectId | → `employees._id` |
| `contact_id` | ObjectId | → `contacts._id` |
| `quantity` | Number | |
| `unit_price` | Number | |
| `discount_pct` | Number | |
| `total_amount` | Number | |
| `region` | String | |
| `status` | String | `completed` / `pending` / `cancelled` / `refunded` |
| `payment_status` | String | `paid` / `pending` / `failed` |
| `order_date` | Date | |
| `delivery_date` | Date | |

#### `transactions` (600 documents)
| Field | Type | Notes |
|---|---|---|
| `type` | String | `revenue` / `expense` / `refund` |
| `category` | String | Sales Revenue, Payroll, Operating Expense, … |
| `amount` | Number | |
| `currency` | String | USD |
| `date` | Date | |
| `status` | String | `completed` / `pending` / `failed` |
| `customer_id` | ObjectId | → `customers._id` (nullable) |
| `order_id` | ObjectId | → `orders._id` (nullable) |
| `employee_id` | ObjectId | → `employees._id` (nullable) |
| `description` | String | |

### Entity Relationship Diagram

```mermaid
erDiagram
    suppliers ||--o{ products : "supplies"
    products ||--o{ orders : "included in"
    customers ||--o{ orders : "places"
    employees ||--o{ orders : "account_manager"
    contacts }o--|| customers : "belongs to"
    orders ||--o{ contacts : "linked to"
    employees ||--o{ customers : "account_manager"
    employees ||--o{ employees : "manages"
    customers ||--o{ transactions : "generates"
    orders ||--o{ transactions : "recorded in"
    employees ||--o{ transactions : "involved in"
```

---

## Dynamic Schema Extraction

A core design principle: the LLM prompt always contains **live field names from real MongoDB documents**, never hardcoded strings.

### How it works

```
MongoDB docs
    ↓  sample 500 docs per collection
_extract_fields()          [schema_registry.py]
    ↓  {field_path → {types, enum_values}}
PostgreSQL schema_collections + schema_fields tables
    ↓  HNSW vector index (384-dim FastEmbed)
PostgreSQL schema_summaries + embedding
    ↓
get_full_schema(collection) → injected into MQL prompt
find_similar_collections(question) → used by collection selector
```

### Schema tables (PostgreSQL)

| Table | Purpose |
|---|---|
| `schema_collections` | One row per MongoDB collection, with `last_scanned` and `doc_count` |
| `schema_fields` | One row per field path, with type frequencies and enum values |
| `schema_summaries` | One-line prompt summary + 384-dim vector embedding per collection |

### Nightly refresh

`scheduler.py` runs a nightly APScheduler job that calls `schema_registry.refresh_all()`, re-sampling each collection and updating the schema tables. A first-boot refresh runs automatically on startup if the registry is empty.

---

## Chat History Persistence

Chainlit's built-in `SQLiteDataLayer` has been replaced with a custom `PostgresDataLayer` (`data_layer.py`) backed by our internal PostgreSQL.

### Tables (PostgreSQL)

| Table | Purpose |
|---|---|
| `chat_users` | One row per authenticated user (`identifier` + `metadata`) |
| `chat_threads` | One row per conversation thread (links to `chat_users`) |
| `chat_steps` | All messages and tool steps within each thread |
| `chat_elements` | File/image elements (reserved for future use) |
| `chat_feedbacks` | User thumbs-up/down feedback on steps |

### Auth flow

`app.py` registers a `@cl.header_auth_callback` that returns a `cl.User(identifier="demo")` for all connections (suitable for a single-tenant deployment). Chainlit then calls `get_user` / `create_user` on the data layer to get a persisted user with a stable UUID, which is used to associate threads.

**Important:** `get_thread_author` returns the user's `identifier` string (not their UUID), because Chainlit compares it against the session's `user.identifier` to authorize thread access.

---

## Project Structure

```
bi-wikolabs/
├── app.py                      # Chainlit entry point — dynamic welcome, chat handlers, exports
├── db.py                       # DB connections: MongoDB (pymongo) + PostgreSQL (asyncpg pool)
├── data_layer.py               # PostgreSQL-backed Chainlit data layer (threads, steps, users)
├── schema_registry.py          # Dynamic schema extraction: MongoDB → PostgreSQL + embeddings
├── embeddings.py               # FastEmbed 384-dim sentence embeddings (ONNX, no PyTorch)
├── golden_records.py           # Save/search verified question→MQL pairs via pgvector
├── migrations.py               # SQL migration runner (applied on startup, idempotent)
├── migrations/
│   └── 001_initial.sql         # Full PostgreSQL schema (schema registry + chat + golden records)
├── scheduler.py                # APScheduler nightly schema refresh job
├── exporter.py                 # Excel (.xlsx) and PDF export utilities
├── seed.py                     # MongoDB demo data seeder (Faker seed=42, deterministic)
│
├── pipeline/                   # 6-step query pipeline
│   ├── __init__.py
│   ├── orchestrator.py         # Coordinator — compound decomposition + merge results
│   ├── models.py               # Pydantic models: MongoQuery, AnalysisResult
│   ├── analyzer.py             # Step 1: query classification + entity extraction (instructor)
│   ├── entity_resolver.py      # Step 2: entity name → MongoDB ObjectId resolution
│   ├── collection_selector.py  # Step 3: pgvector → LLM → keyword fallback chain
│   ├── mql_generator.py        # Step 4: MQL generation (dynamic schema + golden records)
│   ├── executor.py             # Step 5: MongoDB execution + auto-retry with error feedback
│   └── responder.py            # Step 6: DataFrame + Plotly chart + narrative (instructor)
│
├── tests/
│   ├── conftest.py             # Shared fixtures; sets GROQ_API_KEY before any imports
│   ├── test_schema_extraction.py     # schema_registry._extract_fields, _build_summary
│   ├── test_executor.py              # MongoDB query execution and retry logic
│   ├── test_responder.py             # DataFrame building, chart type routing, _should_use_pie
│   ├── test_pipeline_analyzer.py     # Query analysis with mocked Groq
│   ├── test_pipeline_collection_selector.py  # 3-tier collection selection fallback
│   └── test_integration.py           # 10 behavioral end-to-end scenarios
│
├── .chainlit/
│   └── config.toml             # Chainlit UI configuration (dark theme, sidebar, features)
├── .github/
│   └── workflows/
│       └── deploy.yml          # GitHub Actions CI/CD — SSH deploy to VPS on push to main
│
├── docker-compose.yml          # Services: mongo + postgres + app + caddy
├── Dockerfile                  # Python 3.12-slim image
├── Caddyfile                   # Caddy: TLS + reverse proxy + /exports/* static serving
├── requirements.txt            # Production Python dependencies
├── requirements-dev.txt        # Dev dependencies (requirements.txt + pytest)
└── .env.example                # Environment variable template
```

---

## Getting Started (Local Development)

### Prerequisites

- Python 3.12+
- MongoDB running locally (or Docker)
- PostgreSQL running locally (or Docker) — required for schema registry and chat history
- A [Groq API key](https://console.groq.com/) (free tier available)

### 1. Clone the repository

```bash
git clone https://github.com/Wikolabs/bi-wikolabs.git
cd bi-wikolabs
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

For development (includes pytest):
```bash
pip install -r requirements-dev.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
MONGODB_URI=mongodb://localhost:27017/bi_wikolabs
POSTGRES_URI=postgresql://bi:yourpassword@localhost:5432/bi_wikolabs
CHAINLIT_AUTH_SECRET=any-random-string-for-local-dev
```

### 5. Start databases

```bash
# MongoDB
docker run -d -p 27017:27017 --name mongo mongo:7

# PostgreSQL with pgvector
docker run -d -p 5432:5432 --name pg \
  -e POSTGRES_DB=bi_wikolabs \
  -e POSTGRES_USER=bi \
  -e POSTGRES_PASSWORD=yourpassword \
  pgvector/pgvector:pg16
```

### 6. Seed demo data

```bash
python seed.py
```

This creates the full dataset: 8 suppliers, 26 products, 48 employees, 80 customers, 30+ contacts, 900 orders, 600 transactions — all with consistent ObjectId relationships.

### 7. Run the app

```bash
chainlit run app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser. On first start, the app automatically:
1. Runs SQL migrations (creates all PostgreSQL tables)
2. Extracts MongoDB schemas and stores them in PostgreSQL
3. Generates FastEmbed embeddings for collection similarity search

---

## Deployment with Docker

### Full stack (recommended for production)

```bash
# 1. Set up environment variables
cp .env.example .env
# Edit .env with your secrets

# 2. Build and start all services
docker compose up -d --build

# 3. Seed the database (first time only)
docker compose exec app python seed.py
```

Services started:

| Service | Port | Internal host | Volume |
|---|---|---|---|
| `mongo` | internal only | `mongo:27017` | `mongo_data:/data/db` |
| `postgres` | internal only | `postgres:5432` | `pg_data:/var/lib/postgresql/data` |
| `app` | 8000 (proxied by Caddy) | `app:8000` | `app_data:/data`, `model_cache:/root/.cache/fastembed` |
| `caddy` | 80, 443 | — | `caddy_data`, `caddy_config`, `app_data:ro` |

**Startup order:** Both `mongo` and `postgres` must pass their Docker healthchecks before the `app` starts (`condition: service_healthy`).

### Caddyfile (TLS + routing)

```
bi.wikolabs.com {
    handle /exports/* {
        root * /srv/app_data
        file_server
    }

    handle {
        reverse_proxy app:8000
    }
}
```

Replace `bi.wikolabs.com` with your domain. Caddy handles TLS automatically via Let's Encrypt.

### Useful commands

```bash
# View logs
docker compose logs -f app

# Restart the app only (after code changes)
docker compose up -d --build app

# Stop everything
docker compose down

# Wipe all data and restart fresh
docker compose down -v
docker compose up -d --build
docker compose exec app python seed.py
```

---

## CI/CD — GitHub Actions

The workflow file is at `.github/workflows/deploy.yml`.

**Trigger:** Every push to the `main` branch (also supports manual dispatch via `workflow_dispatch`).

### What the pipeline does

```mermaid
flowchart TD
    PUSH["Push to main branch"]
    GHA["GitHub Actions\nubuntu-latest"]
    VAL["Validate all 6 required secrets\nfail fast if any missing"]
    SSH["SSH into VPS\nappleboy/ssh-action@v1.2.0"]
    CHK{"/opt/bi-wikolabs\nexists?"}
    CLONE["git clone repo"]
    PULL["git pull origin main"]
    ENV["Write .env from GitHub Secrets\nGROQ_API_KEY · MONGODB_URI · CHAINLIT_AUTH_SECRET\nPOSTGRES_PASSWORD · POSTGRES_URI"]
    BUILD["docker compose up -d --build --remove-orphans"]
    WAIT["Wait up to 60s for app container\n12 × 5s retries"]
    SEED["docker compose exec app python seed.py\nbest-effort (|| true)"]
    DONE["Deployment complete"]

    PUSH --> GHA --> VAL --> SSH --> CHK
    CHK -->|No| CLONE --> ENV
    CHK -->|Yes| PULL --> ENV
    ENV --> BUILD --> WAIT --> SEED --> DONE
```

**Deploy destination:** `/opt/bi-wikolabs` on the VPS, running as `root`.

---

## GitHub Secrets Reference

Configure these in **GitHub → Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Required | Description |
|---|---|---|
| `VPS_HOST` | Yes | IP address or hostname of your VPS |
| `SSH_PRIVATE_KEY` | Yes | Private SSH key with access to the VPS root account |
| `GROQ_API_KEY` | Yes | Your Groq API key from [console.groq.com](https://console.groq.com/) |
| `POSTGRES_PASSWORD` | Yes | Password for the PostgreSQL `bi` user (generate with `openssl rand -hex 32`) |
| `MONGODB_URI` | Yes | MongoDB connection URI (e.g. `mongodb://mongo:27017/bi_wikolabs`) |
| `CHAINLIT_AUTH_SECRET` | Yes | Random secret for Chainlit session tokens (generate with `openssl rand -hex 32`) |

### Generating secrets

```bash
openssl rand -hex 32   # Use for POSTGRES_PASSWORD and CHAINLIT_AUTH_SECRET
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes | — | Groq API authentication key |
| `MONGODB_URI` | No | `mongodb://mongo:27017` | MongoDB connection URI (include `/dbname` for multi-db hosts) |
| `MONGODB_DB` | No | `bi_wikolabs` | Database name (fallback when URI has no `/dbname`) |
| `POSTGRES_URI` | Yes | — | PostgreSQL connection string (e.g. `postgresql://bi:pw@postgres:5432/bi_wikolabs`) |
| `POSTGRES_PASSWORD` | Yes (Docker) | — | Used by docker-compose to set the PostgreSQL password |
| `CHAINLIT_AUTH_SECRET` | Yes (prod) | — | Secret for signing Chainlit session tokens |
| `BASE_URL` | No | `https://bi.wikolabs.com/exports` | Base URL prefix for export file download links |

---

## Test Suite

The project has **198 tests** covering all pipeline stages plus 10 behavioral integration scenarios.

### Running tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run a specific test file
pytest tests/test_integration.py -v

# Run with output
pytest -s
```

### Test files

| File | Coverage | Count |
|---|---|---|
| `test_schema_extraction.py` | `schema_registry._extract_fields`, `_build_summary`, type inference | ~30 |
| `test_executor.py` | MongoDB query execution, auto-retry, error injection | ~25 |
| `test_responder.py` | DataFrame building, chart routing, `_should_use_pie` | ~40 |
| `test_pipeline_analyzer.py` | All 6 query types, entity extraction, Pydantic validation | ~30 |
| `test_pipeline_collection_selector.py` | 3-tier fallback, entity boost, smalltalk short-circuit | ~35 |
| `test_integration.py` | 10 end-to-end behavioral scenarios + dynamic schema validation | ~38 |

### Key design decisions in tests

- `conftest.py` sets `GROQ_API_KEY` before any imports (pipeline modules instantiate `AsyncGroq` at module level)
- All tests are fully isolated — no real network calls, no real database connections
- Schema functions return dynamically computed values from seeded in-memory MongoDB documents
- `AsyncMock` is used for all async functions; patch at the usage site, not the definition site
- Integration test 10 verifies that custom field names (`sale_value`, `buyer_region`) appear in the LLM prompt's schema section while fallback names (`total_amount`) do not

---

## Example Queries

### Metrics (single KPIs)
- "What is our total revenue this year?"
- "How many orders were completed last month?"
- "What is the average order value?"

### Rankings (Top-N)
- "Show me the top 5 products by revenue"
- "Which 3 account managers have the highest quota attainment?"
- "What are our best-selling product categories?"

### Lists (filtered records)
- "Show all orders from Enterprise customers in the North region"
- "List employees in the Sales department hired after 2023"
- "Which customers have churned status?"

### Comparisons
- "Compare revenue by region for Q1 vs Q2"
- "How does the margin differ across product categories?"
- "Revenue breakdown by customer segment"

### Entity lookups
- "What is the lifetime value of Acme Corp?"
- "Show all orders placed by Alice Johnson"
- "What products does Dell supply?"

### WHY / root-cause questions
- "Why is revenue down in the South region?"
- "Why is employee quota attainment below target?"

### Compound questions
- "Show total revenue AND the top 3 products by sales"
- "What is our headcount by department and also the average salary per department?"

---

## Export Features

After any query that returns tabular data, export buttons appear in the chat:

| Format | Button | Notes |
|---|---|---|
| Excel | **Export XLSX** | Bold headers, auto-width columns, all data rows |
| PDF | **Export PDF** | Landscape A4, capped at 500 rows |

Exported files are written to `/data/exports/` inside the container and served via Caddy at `https://bi.wikolabs.com/exports/<uuid>.<ext>`.

---

## Contributing & Developer Onboarding

### Understanding the codebase

Start with these files in order:

1. **`app.py`** — The Chainlit entry point. Understand the message flow and how results are displayed.
2. **`pipeline/orchestrator.py`** — The main pipeline coordinator. Read the `run()` function.
3. **`pipeline/analyzer.py`** → **`entity_resolver.py`** → **`collection_selector.py`** → **`mql_generator.py`** → **`executor.py`** → **`responder.py`** — Each step in sequence.
4. **`db.py`** — How both database connections (MongoDB + PostgreSQL) work.
5. **`schema_registry.py`** — The dynamic schema extraction and pgvector similarity search.
6. **`data_layer.py`** — How Chainlit's chat history is persisted in PostgreSQL.
7. **`seed.py`** — The full data model. Read this to understand every collection's shape.

### Adding a new query type

1. Add the type name to the `query_type` enum in `pipeline/analyzer.py`'s system prompt.
2. Add a `MongoQuery` variant in `pipeline/models.py` if needed.
3. Add static few-shot examples for the new type in `pipeline/mql_generator.py`.
4. Add chart logic in `pipeline/responder.py` if needed.
5. Add behavioral tests in `tests/test_integration.py`.

### Adding a new MongoDB collection

1. Add seed data in `seed.py` (create the collection and insert documents).
2. Add the collection name and summary to `pipeline/collection_selector.py`'s keyword fallback map.
3. Add entity search logic in `pipeline/entity_resolver.py` if the collection contains named entities.
4. The schema registry picks it up automatically on next refresh (no code change needed).

### Key design decisions

| Decision | Reason |
|---|---|
| Groq over OpenAI | 10–20× faster inference — critical for a chat interface |
| Two databases (MongoDB + PostgreSQL) | MongoDB = flexible client data; PostgreSQL = structured app metadata + vector search |
| Dynamic schema extraction | LLM gets current field names even after client schema changes |
| pgvector for collection selection | Semantic match scales to hundreds of collections without prompt bloat |
| `instructor` + Pydantic for LLM output | Eliminates JSON parse errors with auto-retry; enforces output contracts |
| Parallel Steps 2 & 3 | ~500ms saved per query with `asyncio.gather` |
| Entity → ObjectId resolution | Prevents brittle name-matching; handles name duplicates |
| Self-correcting MQL (retry) | Eliminates ~30% of failures without user intervention |
| Chainlit (not FastAPI + custom UI) | Python-native, handles streaming + file uploads out of the box |
| Caddy (not Nginx) | Automatic TLS via Let's Encrypt, zero config for static files |
| PostgreSQL for chat history | Same DB used for schema registry and golden records; avoids SQLite file lock issues in containers |

---

## PRD

### Problème
Les équipes métier dépendent des équipes data pour obtenir des analyses : chaque question non-standard nécessite un ticket, une attente, et un aller-retour. Les managers ne peuvent pas requêter directement leur base MongoDB. Les outils BI classiques (Tableau, Power BI) nécessitent une formation et restent inaccessibles pour des questions ad-hoc en français.

### Solution
BI Wikolabs traduit les questions business en langage naturel en pipelines d'agrégation MongoDB valides, exécute les requêtes avec auto-correction, et retourne des analyses narratives avec graphiques Plotly interactifs — le tout en quelques secondes, sans SQL, sans formation.

### Utilisateurs cibles
| Persona | Besoin |
|---------|--------|
| CEO / Directeur | Réponses immédiates aux questions business sans passer par un analyste |
| Responsable Commercial | Analyser le pipeline, les performances, les clients en autonomie |
| Équipe Data | Réduire le volume de requêtes ad-hoc répétitives |

### OKRs
- Précision génération MQL : ≥ 90% (requêtes valides au premier essai)
- Latence end-to-end : < 4 secondes par question
- Taux auto-correction : -30% d'échecs sans intervention utilisateur

---

## User Stories

```
US-01 [CEO] En tant que CEO,
      je veux poser la question "quel est notre chiffre d'affaires ce trimestre par région"
      et obtenir un graphique interactif avec la réponse narrative
      afin d'avoir une réponse immédiate sans attendre un rapport.

US-02 [Commercial] En tant que responsable commercial,
      je veux demander "quels sont nos 5 meilleurs clients par lifetime value"
      et voir un tableau avec les détails de chaque compte
      afin de prioriser mes efforts de fidélisation.

US-03 [Manager] En tant que manager,
      je veux poser une question de type "pourquoi le chiffre baisse dans le Sud"
      et obtenir une analyse root-cause automatique
      afin de comprendre les causes sans analyser manuellement les données.

US-04 [Analyst] En tant qu'analyste data,
      je veux valider une question/requête correcte comme "golden record"
      pour qu'elle serve d'exemple few-shot pour les questions futures similaires
      afin d'améliorer continuellement la précision du système.

US-05 [Manager] En tant que manager,
      je veux exporter les résultats en Excel ou PDF
      pour les partager en réunion ou les archiver
      afin de diffuser les insights sans accès à l'outil.
```

---

## Règles métier

| # | Règle | Description | Simulable UI |
|---|-------|-------------|-------------|
| R1 | Auto-correction MQL | Requête invalide → erreur renvoyée au LLM → re-génération (max 3 essais) | ✅ Retry display |
| R2 | Steps parallèles | Entity resolution + Collection selection en asyncio.gather() (~500ms économisés) | ✅ Pipeline trace |
| R3 | Schema dynamique | Noms de champs extraits live depuis MongoDB, jamais hardcodés | ✅ Schema view |
| R4 | Golden records | Paire question/MQL validée → few-shot example pour prochaines queries similaires | ✅ Save golden |
| R5 | Types de charts | None (KPI), barre horizontale (ranking), pie (2-10 catégories), barre groupée | ✅ Chart types |
| R6 | WHY drill-down | Question "pourquoi" → 2ème requête MQL root-cause automatique | ✅ WHY query |
| R7 | Compound split | "Montre X et aussi Y" → 2 requêtes parallèles + fusion résultats | ✅ Compound |
| R8 | Entity resolution | Nom → ObjectId MongoDB (ex: "Acme Corp" → ObjectId("...")) | ✅ Entity map |
| R9 | Export XLSX/PDF | Après tout résultat tabulaire, boutons Export apparaissent | ✅ Export buttons |
| R10 | Smalltalk filter | Classification "smalltalk" → réponse directe sans requête DB | ✅ Greeting |

---

## License

Proprietary — Wikolabs. All rights reserved.

---

*Un produit [Wikolabs](https://wikolabs.com) — Intelligence artificielle appliquée aux métiers*
