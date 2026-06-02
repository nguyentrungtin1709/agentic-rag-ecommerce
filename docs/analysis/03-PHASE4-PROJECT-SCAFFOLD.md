# Phase 4 — Project Scaffold & Infrastructure Setup

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 1.0
- **Date**: 2026-06-02
- **Status**: Planned — Ready for implementation review

> This document covers all decisions and blueprints for Phase 4.
> All package versions were verified against PyPI and Docker Hub on 2026-06-02.
> Implement this phase before starting Phase 5 (Product RAG Engine).

---

## 1. Objective

Phase 4 establishes the full development foundation:

- Repository structure with all module boundaries defined
- Python dependency tree pinned to exact stable versions
- Docker Compose stack (10 services) runnable locally with one command
- Configuration system (`pydantic-settings`) loading all env vars from Phase 2 spec
- Health and readiness endpoints verifying all external connections
- Saleor GraphQL client + JWKS client wired up
- Qdrant service (collection creation, upsert, delete, search stubs)
- Pre-commit hooks (`ruff` + `pyright`) enforced on every commit
- Integration test suite verifying all external connections work

At the end of Phase 4, the system must boot, pass `/health` and `/ready`, connect to all 10
services, and have an empty-but-compilable LangGraph graph. No agent logic is implemented yet.

---

## 2. Project Directory Structure

```
agentic-rag-ecommerce/
│
├── .github/                           # Copilot instructions, agents, skills, rules
│   ├── agents/
│   ├── skills/
│   ├── prompts/
│   ├── rules/
│   ├── workflows/
│   └── copilot-instructions.md
│
├── docs/
│   ├── PLAN.md
│   ├── analysis/
│   │   ├── 01-USE-CASE-ANALYSIS.md
│   │   ├── 02-REQUIREMENTS-SPECIFICATION.md
│   │   └── 03-PHASE4-PROJECT-SCAFFOLD.md   ← this file
│   └── diagrams/
│       ├── 01-use-case-overview.mermaid
│       ├── 02-system-context.mermaid
│       ├── 03-customer-chat-sequence.mermaid
│       ├── 04-webhook-sync-sequence.mermaid
│       └── 05-agent-workflow.mermaid
│
├── history/                           # Decision records (one per feature/change)
│   └── 1_0_0_INITIAL_PROJECT_SETUP.md
│
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py                    # FastAPI app factory + lifespan manager
│       ├── config.py                  # pydantic-settings Settings class
│       ├── dependencies.py            # FastAPI DI: get_settings, get_db_pool, etc.
│       │
│       ├── api/                       # HTTP layer — routers only, no business logic
│       │   ├── __init__.py
│       │   ├── router.py              # Aggregate all APIRouters
│       │   ├── threads.py             # POST/GET/DELETE /api/v1/threads
│       │   ├── chat.py                # POST /api/v1/threads/{id}/runs/stream (SSE)
│       │   ├── profile.py             # GET /api/v1/users/{user_id}/profile
│       │   ├── admin.py               # POST /admin/reindex, GET /admin/threads
│       │   ├── webhooks.py            # POST /webhooks/saleor
│       │   └── health.py              # GET /health, /ready, /metrics
│       │
│       ├── agent/                     # LangGraph graph, nodes, and sub-agents
│       │   ├── __init__.py
│       │   ├── graph.py               # Compiled LangGraph graph (builder + compile)
│       │   ├── state.py               # AgentState TypedDict
│       │   │
│       │   ├── nodes/                 # Simple nodes — plain Python functions, no tool loop
│       │   │   ├── __init__.py
│       │   │   ├── title_generation.py  # LLM call + retry + SSE thread_title
│       │   │   ├── profiler.py          # LLM call: profile merge from snapshot + message
│       │   │   ├── orchestrator.py      # 4-intent routing + remaining_steps guard
│       │   │   ├── response_generator.py # LLM stream: synthesize + SSE token/products/done
│       │   │   └── image_generation.py  # DALL-E + S3 upload + Valkey quota + SSE image_*
│       │   │
│       │   ├── subagents/             # Complex sub-agents with internal ReAct tool loops
│       │   │   ├── __init__.py
│       │   │   ├── product_rag/       # ProductRAGAgent — LlamaIndex hybrid search sub-agent
│       │   │   │   ├── __init__.py
│       │   │   │   ├── agent.py       # ReAct agent: query_rewrite -> search -> rerank
│       │   │   │   └── tools.py       # @tool: rewrite_query, hybrid_search, filter_products
│       │   │   └── trend_scout/       # TrendScoutAgent — web search + summarize sub-agent
│       │   │       ├── __init__.py
│       │   │       ├── agent.py       # ReAct agent: search -> summarize -> generate prompts
│       │   │       └── tools.py       # @tool: tavily_search, duckduckgo_search
│       │   │
│       │   └── prompts/               # Externalized LLM prompt templates (Phase 7)
│       │       ├── orchestrator_intent.md
│       │       ├── profiler_merge.md
│       │       ├── response_generator.md
│       │       ├── title_generation.md
│       │       ├── image_generation.md
│       │       ├── product_rag_react.md
│       │       └── trend_scout_react.md
│       │
│       ├── models/                    # Domain entities — shared across modules (not API-specific)
│       │   ├── __init__.py
│       │   ├── product.py             # ProductItem (used by RAG, agent state, SSE response)
│       │   ├── profile.py             # UserProfile (used by profiler node, LangGraph Store)
│       │   ├── thread.py              # ThreadStatus enum, Thread domain model
│       │   └── image.py               # GeneratedImage domain model
│       │
│       ├── rag/                       # LlamaIndex RAG pipeline
│       │   ├── __init__.py
│       │   ├── indexer.py             # Saleor -> parse nodes -> embed -> Qdrant upsert
│       │   └── retriever.py           # Hybrid search (dense + BM25 + metadata filter)
│       │
│       ├── services/                  # External service clients (no business logic)
│       │   ├── __init__.py
│       │   ├── saleor_client.py       # GraphQL product fetch + JWKS fetch
│       │   ├── qdrant_service.py      # Collection management, upsert, delete, search
│       │   ├── s3_service.py          # Upload, delete, generate presigned URL
│       │   └── valkey_service.py      # Image quota counter, cache invalidation
│       │
│       ├── auth/                      # Authentication and authorization
│       │   ├── __init__.py
│       │   ├── jwt_verifier.py        # RS256 JWT verification + JWKS cache
│       │   └── hmac_verifier.py       # Constant-time HMAC-SHA256 for webhooks
│       │
│       ├── db/                        # Database access layer
│       │   ├── __init__.py
│       │   └── session.py             # asyncpg pool + psycopg pool factories + lifespan
│       │
│       ├── repositories/              # Data access — one file per aggregate root
│       │   ├── __init__.py
│       │   ├── thread_repo.py         # CRUD for threads table (uses asyncpg)
│       │   └── image_repo.py          # CRUD for generated_images table (uses asyncpg)
│       │
│       ├── tasks/                     # Celery async tasks
│       │   ├── __init__.py
│       │   ├── celery_app.py          # Celery app factory + Beat schedule
│       │   ├── webhook_task.py        # process_webhook (upsert/delete vector)
│       │   ├── reindex_task.py        # reindex_products (full catalog sync)
│       │   └── cleanup_task.py        # cleanup_expired_threads (nightly Beat)
│       │
│       ├── schemas/                   # API request/response contracts (HTTP boundary only)
│       │   ├── __init__.py
│       │   ├── thread.py              # CreateThreadRequest, ThreadResponse, ThreadListResponse
│       │   ├── chat.py                # ChatRequest, SSEEvent, DonePayload, UsagePayload
│       │   ├── webhook.py             # SaleorWebhookPayload, ProductEventPayload
│       │   └── common.py             # PaginatedResponse, ErrorResponse, CursorPage
│       │
│       └── observability/             # Logging and tracing setup
│           ├── __init__.py
│           ├── logging.py             # structlog JSON configuration
│           └── tracing.py             # LangSmith + OpenInference OTel setup
│
├── alembic/                           # Database migration tool
│   ├── versions/
│   │   └── 0001_initial_schema.py     # threads + generated_images DDL
│   ├── env.py                         # Alembic runtime config (async-compatible)
│   └── script.py.mako                 # Migration file template
│
├── alembic.ini                        # Alembic config file (sqlalchemy.url from env)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                    # pytest fixtures: async client, mock services
│   ├── unit/
│   │   ├── agent/
│   │   │   ├── nodes/                 # Node tests with mocked LLM
│   │   │   └── subagents/             # Sub-agent + tool tests with mocked clients
│   │   ├── auth/                      # JWT + HMAC verifier tests
│   │   ├── services/                  # Service layer unit tests
│   │   └── tasks/                     # Celery task unit tests
│   ├── integration/
│   │   ├── test_postgres.py
│   │   ├── test_qdrant.py
│   │   ├── test_valkey.py
│   │   ├── test_saleor_client.py
│   │   ├── test_health.py
│   │   ├── test_ready_degraded.py
│   │   ├── test_webhook_endpoint.py     # Phase 8
│   │   └── test_api_endpoints.py       # Phase 6
│   └── evaluation/                    # Agent quality evaluation (Phase 11)
│       ├── test_agent_routing.py
│       └── test_rag_retrieval.py
│
├── docker/
│   ├── app/
│   │   └── Dockerfile
│   ├── promtail/
│   │   └── config.yml
│   └── grafana/
│       ├── datasources/
│       │   └── datasources.yml
│       └── dashboards/
│           └── main.json
│
├── docker-compose.yml                 # Full 9-service stack
├── docker-compose.override.yml        # Dev overrides: hot reload, debug ports
├── pyproject.toml                     # uv + dependencies + tool config
├── .env.example                       # All required env vars with safe defaults
├── .pre-commit-config.yaml            # ruff + pyright hooks
├── pyrightconfig.json                 # Pyright type checker config
└── README.md
```

### Key Module Boundaries

| Module | Responsibility | Must NOT import from |
|---|---|---|
| `api/` | HTTP request/response, route wiring | `agent/`, `tasks/` directly |
| `agent/nodes/` | Simple nodes — plain Python functions | `api/`, `tasks/` |
| `agent/subagents/` | Sub-agents with ReAct tool loops | `api/`, `tasks/` |
| `rag/` | LlamaIndex indexing and retrieval | `agent/`, `api/` |
| `models/` | Domain entities shared across modules | `api/`, `agent/`, `tasks/` |
| `schemas/` | API request/response contracts (HTTP boundary) | `agent/`, `tasks/` |
| `services/` | External client wrappers | `agent/`, `api/`, `tasks/` |
| `auth/` | JWT + HMAC verification | Everything except `config.py` |
| `db/` | DB session management | `api/`, `agent/`, `tasks/` |
| `repositories/` | Data access per aggregate root | `api/`, `agent/`, `tasks/` |
| `tasks/` | Celery workers | `api/`, `agent/` |
| `observability/` | Logging + tracing setup | Only stdlib + third-party |

### models/ vs schemas/ — Clear Separation Rule

```
models/           ← Domain entities. Defined once. Used everywhere.
  product.py      ProductItem — created by RAG, stored in AgentState, serialized in SSE
  profile.py      UserProfile — read/written by ProfilerNode, returned by /users/profile
  thread.py       ThreadStatus enum, Thread entity — used by repo, API, and tasks
  image.py        GeneratedImage — used by ImageGenerationNode, image_repo, history API

schemas/          ← HTTP boundary contracts only. Used only in api/ layer.
  thread.py       CreateThreadRequest, ThreadResponse (wraps models.Thread for API)
  chat.py         ChatRequest, SSEEvent union type, DonePayload
  webhook.py      SaleorWebhookPayload (input from Saleor — never a domain model)
  common.py       PaginatedResponse[T], ErrorResponse, CursorPage[T]
```

**Rule**: `api/` imports from both `schemas/` and `models/`. `agent/`, `rag/`, `tasks/`
import only from `models/`. `schemas/` never imports from `agent/` or `tasks/`.

### agent/nodes/ vs agent/subagents/ — Node Type Classification

| File | Type | Pattern | Has tool loop? |
|---|---|---|---|
| `nodes/title_generation.py` | Simple node | Plain function | No |
| `nodes/profiler.py` | Simple node | Plain function | No |
| `nodes/orchestrator.py` | Simple node | Plain function | No |
| `nodes/response_generator.py` | Simple node | Plain function + LLM stream | No |
| `nodes/image_generation.py` | Simple node | Plain function | No |
| `subagents/product_rag/agent.py` | Sub-agent | LangChain ReAct | YES — query rewrite → search → rerank |
| `subagents/trend_scout/agent.py` | Sub-agent | LangChain ReAct | YES — search → fallback → summarize |

Both sub-agents expose a single entry-point function called by the LangGraph node wrapper
in `graph.py`. The internal ReAct loop is hidden inside the sub-agent.

---

## 3. Python Dependencies

### 3.1 Package Versions (verified 2026-06-02)

All versions are pinned to the exact latest stable release. Update only after testing.

#### Runtime — Web Framework

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | `0.136.3` | REST API framework + built-in SSE (`fastapi.sse.EventSourceResponse`, `fastapi.sse.ServerSentEvent`) |
| `uvicorn[standard]` | `0.48.0` | ASGI server (includes uvloop + httptools) |
| `httpx` | `0.28.1` | Async HTTP client (Saleor GraphQL, JWKS fetch) |

#### Runtime — LangChain / LangGraph

| Package | Version | Purpose |
|---|---|---|
| `langgraph` | `1.2.2` | Agent graph orchestration |
| `langgraph-checkpoint-postgres` | `3.1.0` | `AsyncPostgresSaver` + `AsyncPostgresStore` |
| `langchain` | `1.3.2` | LangChain primitives (tools, messages) |
| `langchain-openai` | `1.2.2` | OpenAI LLM + embedding integration |
| `langchain-community` | `0.4.2` | DuckDuckGo search tool |
| `langsmith` | `0.8.8` | LangSmith tracing client |

#### Runtime — LlamaIndex (RAG)

| Package | Version | Purpose |
|---|---|---|
| `llama-index-core` | `0.14.22` | Core RAG pipeline primitives |
| `llama-index-vector-stores-qdrant` | `0.10.1` | Qdrant vector store integration |
| `llama-index-embeddings-openai` | `0.6.0` | OpenAI embedding model |
| `llama-index-retrievers-bm25` | `0.7.1` | BM25 sparse retriever |
| `openinference-instrumentation-llama-index` | `4.4.2` | LlamaIndex -> OTel -> LangSmith bridge |

#### Runtime — OpenAI

| Package | Version | Purpose |
|---|---|---|
| `openai` | `2.40.0` | LLM inference, embeddings, DALL-E image gen |

#### Runtime — Databases

| Package | Version | Purpose |
|---|---|---|
| `psycopg[binary,pool]` | `3.3.4` | PostgreSQL async driver (required by LangGraph) |
| `psycopg-pool` | `3.3.1` | psycopg connection pool |
| `asyncpg` | `0.31.0` | High-performance async PostgreSQL (custom repositories) |
| `qdrant-client` | `1.18.0` | Qdrant vector database client |

> **Note on DB drivers**: `psycopg` (v3) is required by `langgraph-checkpoint-postgres`.
> `asyncpg` is used for custom repository queries (`thread_repo`, `image_repo`) where
> raw performance matters. Both drivers connect to the same PostgreSQL instance.

#### Runtime — Cache / Tasks

| Package | Version | Purpose |
|---|---|---|
| `redis` | `8.0.0` | Redis/Valkey client (rate limiting + response cache) |
| `celery[redis]` | `5.6.3` | Async task queue; `[redis]` adds result backend |
| `aiohttp` | `3.14.0` | Celery async transport dependency |

#### Runtime — Storage / Search

| Package | Version | Purpose |
|---|---|---|
| `boto3` | `1.43.19` | AWS S3 upload/delete |
| `tavily-python` | `0.7.25` | Tavily web search API |
| `duckduckgo-search` | `8.1.1` | DuckDuckGo fallback search |

#### Runtime — Auth

| Package | Version | Purpose |
|---|---|---|
| `PyJWT` | `2.13.0` | JWT decode and RS256 verification |
| `cryptography` | `48.0.0` | RSA public key loading from JWKS |

#### Runtime — Rate Limiting / Caching

| Package | Version | Purpose |
|---|---|---|
| `slowapi` | `0.1.9` | Per-user rate limiting with Valkey backend |
| `fastapi-cache2` | `0.2.2` | Response caching with Valkey backend |

#### Runtime — Observability

| Package | Version | Purpose |
|---|---|---|
| `structlog` | `25.5.0` | Structured JSON logging |
| `prometheus-fastapi-instrumentator` | `8.0.0` | FastAPI Prometheus metrics auto-export |

#### Runtime — Resilience

| Package | Version | Purpose |
|---|---|---|
| `pydantic` | `2.13.4` | Data validation and serialization |
| `pydantic-settings` | `2.14.1` | Environment-based configuration |
| `tenacity` | `9.1.4` | Retry with exponential backoff |
#### Runtime — Database Migrations

| Package | Version | Purpose |
|---|---|---|
| `alembic` | `1.18.4` | Database migration tool (pure SQL mode, no ORM required) |

> **Why Alembic without SQLAlchemy ORM**: The project uses `asyncpg` directly for
> custom queries. Alembic is used only for its migration runner (`alembic upgrade head`);
> migrations are written as raw SQL via `op.execute()`, not as ORM model diffs.
> SQLAlchemy core (`sqlalchemy`) is listed as a dependency only because Alembic requires
> it internally — no ORM models are defined.

| Package | Version | Purpose |
|---|---|---|
| `ruff` | `0.15.15` | Linter + formatter |
| `pyright` | latest | Type checker (installed globally via npm or pip) |
| `pytest` | `9.0.3` | Test runner |
| `pytest-asyncio` | `1.4.0` | Async test support |
| `pytest-cov` | `7.1.0` | Coverage reporting |
| `pytest-mock` | `3.15.1` | `mocker` fixture for unit tests |
| `respx` | `0.23.1` | Mock `httpx` requests in tests |
| `fakeredis` | `2.36.0` | In-memory Redis/Valkey mock for tests |

### 3.2 pyproject.toml Structure

```toml
[project]
name = "agentic-rag-ecommerce"
version = "0.1.0"
description = "AI POD Stylist & Recommendation System"
requires-python = ">=3.12"
dependencies = [
    # Web (FastAPI >= 0.135.0 includes built-in SSE via fastapi.sse — no sse-starlette needed)
    "fastapi==0.136.3",
    "uvicorn[standard]==0.48.0",
    "httpx==0.28.1",
    # LangGraph / LangChain
    "langgraph==1.2.2",
    "langgraph-checkpoint-postgres==3.1.0",
    "langchain==1.3.2",
    "langchain-openai==1.2.2",
    "langchain-community==0.4.2",
    "langsmith==0.8.8",
    # LlamaIndex
    "llama-index-core==0.14.22",
    "llama-index-vector-stores-qdrant==0.10.1",
    "llama-index-embeddings-openai==0.6.0",
    "llama-index-retrievers-bm25==0.7.1",
    "openinference-instrumentation-llama-index==4.4.2",
    # OpenAI
    "openai==2.40.0",
    # Databases
    "psycopg[binary,pool]==3.3.4",
    "psycopg-pool==3.3.1",
    "asyncpg==0.31.0",
    "qdrant-client==1.18.0",
    # Migrations (raw SQL via op.execute — no ORM models)
    "alembic==1.18.4",
    # Cache / Tasks
    "redis==8.0.0",
    "celery[redis]==5.6.3",
    "aiohttp==3.14.0",
    # Storage / Search
    "boto3==1.43.19",
    "tavily-python==0.7.25",
    "duckduckgo-search==8.1.1",
    # Auth
    "PyJWT==2.13.0",
    "cryptography==48.0.0",
    # Rate limiting / Caching
    "slowapi==0.1.9",
    "fastapi-cache2==0.2.2",
    # Observability
    "structlog==25.5.0",
    "prometheus-fastapi-instrumentator==8.0.0",
    # Core
    "pydantic==2.13.4",
    "pydantic-settings==2.14.1",
    "tenacity==9.1.4",
]

[project.optional-dependencies]
dev = [
    "ruff==0.15.15",
    "pytest==9.0.3",
    "pytest-asyncio==1.4.0",
    "pytest-cov==7.1.0",
    "pytest-mock==3.15.1",
    "respx==0.23.1",
    "fakeredis==2.36.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "ruff==0.15.15",
    "pytest==9.0.3",
    "pytest-asyncio==1.4.0",
    "pytest-cov==7.1.0",
    "pytest-mock==3.15.1",
    "respx==0.23.1",
    "fakeredis==2.36.0",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM", "S"]
ignore = ["S101"]  # allow assert in tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S", "N"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--cov=src/app --cov-report=term-missing --cov-fail-under=80"

[tool.pyright]
pythonVersion = "3.12"
pythonPlatform = "Linux"
typeCheckingMode = "standard"
include = ["src", "tests"]
exclude = ["**/__pycache__"]
```

---

## 4. Docker Compose Services

### 4.1 Image Versions (verified 2026-06-02)

| Service | Image | Version |
|---|---|---|
| `app` | `python` (via Dockerfile) | `3.12-slim` |
| `celery-worker` | Same Dockerfile as `app` | — |
| `celery-beat` | Same Dockerfile as `app` | — |
| `postgres` | `postgres` | `16.14` |
| `qdrant` | `qdrant/qdrant` | `v1.18.1` |
| `valkey` | `valkey/valkey` | `9.1.0` |
| `rabbitmq` | `rabbitmq` | `4.3.1-management` |
| `grafana` | `grafana/grafana` | `13.0.2` |
| `loki` | `grafana/loki` | `3.7.2` |
| `promtail` | `grafana/promtail` | `3.6.11` |

### 4.2 docker-compose.yml

```yaml
version: "3.9"

networks:
  app-net:
    driver: bridge

volumes:
  postgres_data:
  qdrant_data:
  valkey_data:
  rabbitmq_data:
  grafana_data:
  loki_data:

x-app-common: &app-common
  build:
    context: .
    dockerfile: docker/app/Dockerfile
  env_file: .env
  networks: [app-net]
  depends_on:
    postgres:
      condition: service_healthy
    qdrant:
      condition: service_healthy
    valkey:
      condition: service_healthy
    rabbitmq:
      condition: service_healthy

services:

  # ── Application ──────────────────────────────────────────────────────────────

  app:
    <<: *app-common
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  celery-worker:
    <<: *app-common
    command: >
      celery -A app.tasks.celery_app worker
      --loglevel=info
      --queues=webhook,reindex,cleanup
      --concurrency=4
    healthcheck:
      test: ["CMD", "celery", "-A", "app.tasks.celery_app", "inspect", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3

  celery-beat:
    <<: *app-common
    command: >
      celery -A app.tasks.celery_app beat
      --loglevel=info
      --scheduler=celery.beat:PersistentScheduler

  # ── Data Layer ────────────────────────────────────────────────────────────────

  postgres:
    image: postgres:16.14
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-app}
      POSTGRES_USER: ${POSTGRES_USER:-app}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks: [app-net]
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-app} -d ${POSTGRES_DB:-app}"]
      interval: 10s
      timeout: 5s
      retries: 5

  qdrant:
    image: qdrant/qdrant:v1.18.1
    volumes:
      - qdrant_data:/qdrant/storage
    networks: [app-net]
    ports:
      - "6333:6333"
      - "6334:6334"  # gRPC
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5

  valkey:
    image: valkey/valkey:9.1.0
    command: valkey-server --save 60 1 --loglevel warning
    volumes:
      - valkey_data:/data
    networks: [app-net]
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:4.3.1-management
    environment:
      RABBITMQ_DEFAULT_USER: ${RABBITMQ_USER:-guest}
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_PASSWORD:-guest}
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    networks: [app-net]
    ports:
      - "5672:5672"
      - "15672:15672"  # Management UI
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 20s
      timeout: 10s
      retries: 5

  # ── Observability ─────────────────────────────────────────────────────────────

  grafana:
    image: grafana/grafana:13.0.2
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
      GF_PATHS_PROVISIONING: /etc/grafana/provisioning
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/grafana/datasources:/etc/grafana/provisioning/datasources
      - ./docker/grafana/dashboards:/etc/grafana/provisioning/dashboards
    networks: [app-net]
    ports:
      - "3000:3000"
    depends_on: [loki]

  loki:
    image: grafana/loki:3.7.2
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - loki_data:/loki
    networks: [app-net]
    ports:
      - "3100:3100"

  promtail:
    image: grafana/promtail:3.6.11
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock
      - ./docker/promtail/config.yml:/etc/promtail/config.yml
    networks: [app-net]
    depends_on: [loki]
```

### 4.3 docker-compose.override.yml (dev only)

```yaml
version: "3.9"

services:
  app:
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    volumes:
      - ./src:/app/src   # hot reload source mount
    environment:
      LOG_LEVEL: DEBUG

  celery-worker:
    environment:
      LOG_LEVEL: DEBUG
```

### 4.4 Dockerfile

```dockerfile
# docker/app/Dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv==0.11.18

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# curl required for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY src/ ./src/

ENV PYTHONPATH="/app/src"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Environment Variables

### 5.1 .env.example

```dotenv
# ── PostgreSQL ────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql+psycopg://app:changeme@localhost:5432/app
POSTGRES_DB=app
POSTGRES_USER=app
POSTGRES_PASSWORD=changeme

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=products

# ── Valkey (Redis-compatible) ─────────────────────────────────────────────────
# DB 0 = rate limiting (slowapi), DB 1 = response cache (fastapi-cache2)
VALKEY_URL=redis://localhost:6379

# ── Celery / RabbitMQ ─────────────────────────────────────────────────────────
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/2
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...

# ── LLM Model Names ───────────────────────────────────────────────────────────
RESPONSE_MODEL=gpt-4o
ORCHESTRATOR_MODEL=gpt-4o-mini
TITLE_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMS=1536

# ── Tavily Search ─────────────────────────────────────────────────────────────
TAVILY_API_KEY=tvly-...

# ── Saleor ────────────────────────────────────────────────────────────────────
SALEOR_URL=http://localhost:8080
SALEOR_APP_TOKEN=
SALEOR_WEBHOOK_SECRET=changeme-32-char-secret

# ── AWS S3 ────────────────────────────────────────────────────────────────────
AWS_S3_BUCKET=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-southeast-1

# ── Agent Behavior ────────────────────────────────────────────────────────────
MAX_AGENT_STEPS=10
AGENT_FALLBACK_THRESHOLD=2
IMAGE_DAILY_LIMIT=10

# ── Thread Auto-Naming ────────────────────────────────────────────────────────
TITLE_GENERATION_MAX_ATTEMPTS=3
TITLE_TRUNCATION_LENGTH=50

# ── Rate Limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_CHAT=20/minute
RATE_LIMIT_THREAD_CREATE=10/minute
RATE_LIMIT_READ=60/minute
RATE_LIMIT_WRITE=10/minute
RATE_LIMIT_REINDEX=2/hour

# ── Caching ───────────────────────────────────────────────────────────────────
THREAD_LIST_CACHE_TTL=120

# ── Observability ─────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
LANGCHAIN_TRACING_V2=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=agentic-rag-ecommerce

# ── Grafana (dev only) ────────────────────────────────────────────────────────
GRAFANA_USER=admin
GRAFANA_PASSWORD=admin
```

---

## 6. Configuration Module Design

### 6.1 `src/app/config.py` — Key Design Points

- Single `Settings` class extending `pydantic_settings.BaseSettings`
- `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`
- All fields have type annotations; required fields have no default
- Computed properties for derived values (e.g., `valkey_rate_limit_url`, `valkey_cache_url`)
- `@lru_cache` on `get_settings()` to avoid re-parsing on every dependency injection call

```python
# Illustrative structure — full implementation in Phase 4
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str
    qdrant_url: str
    qdrant_api_key: str = ""
    qdrant_collection_name: str = "products"
    valkey_url: str

    # Celery
    celery_broker_url: str
    celery_result_backend: str

    # OpenAI
    openai_api_key: str
    response_model: str
    orchestrator_model: str
    title_model: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 1536

    # Saleor
    saleor_url: str
    saleor_app_token: str
    saleor_webhook_secret: str

    # AWS S3
    aws_s3_bucket: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str

    # Search
    tavily_api_key: str

    # Agent behavior
    max_agent_steps: int = 10
    agent_fallback_threshold: int = 2
    image_daily_limit: int = 10

    # Thread naming
    title_generation_max_attempts: int = 3
    title_truncation_length: int = 50

    # Rate limits
    rate_limit_chat: str = "20/minute"
    rate_limit_thread_create: str = "10/minute"
    rate_limit_read: str = "60/minute"
    rate_limit_write: str = "10/minute"
    rate_limit_reindex: str = "2/hour"

    # Caching
    thread_list_cache_ttl: int = 120

    # Observability
    log_level: str = "INFO"
    langchain_tracing_v2: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "agentic-rag-ecommerce"

    @property
    def valkey_rate_limit_url(self) -> str:
        return f"{self.valkey_url}/0"

    @property
    def valkey_cache_url(self) -> str:
        return f"{self.valkey_url}/1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## 7. Health and Readiness Endpoints

### 7.1 `GET /health` — Liveness Probe

Always returns `200 OK` if the process is running. No external checks.

```json
{"status": "ok", "service": "agentic-rag-ecommerce"}
```

### 7.2 `GET /ready` — Readiness Probe

Checks all critical dependencies. Returns `200 OK` only if all pass; `503 Service Unavailable` if any fail. Each check has a 2-second timeout.

| Check | Method | Success Condition |
|---|---|---|
| PostgreSQL | `SELECT 1` | Returns row without error |
| Qdrant | `GET /healthz` | HTTP 200 |
| Valkey | `PING` | Returns `PONG` |
| RabbitMQ | Not checked | Only checked by Celery at startup |

Response on success:
```json
{
  "status": "ready",
  "checks": {
    "postgres": "ok",
    "qdrant": "ok",
    "valkey": "ok"
  }
}
```

Response on failure (503):
```json
{
  "status": "not_ready",
  "checks": {
    "postgres": "ok",
    "qdrant": "error: connection refused",
    "valkey": "ok"
  }
}
```

### 7.3 `GET /metrics` — Prometheus Metrics

Auto-exported by `prometheus-fastapi-instrumentator`. Exposes:
- `http_requests_total` — counter by method, path, status code
- `http_request_duration_seconds` — histogram by method, path
- Standard Python process metrics

No authentication required (control via network/firewall in production).

---

## 8. Database Migrations

### 8.1 Tool: Alembic (version `1.18.4`)

**Why Alembic, not raw SQL applied at startup:**

| | Raw SQL in lifespan | Alembic |
|---|---|---|
| Version tracking | None — no record of what ran | `alembic_version` table tracks current revision |
| Rollback | Manual | `alembic downgrade -1` |
| Schema drift detection | None | `alembic check` compares DB to head |
| Team collaboration | Re-runs entire file every boot | Each migration runs exactly once |
| CI integration | Custom scripts needed | `alembic upgrade head` \u2014 one command |
| New environments | Must manually track order | `alembic upgrade head` \u2014 applies all pending |

**Why NOT SQLAlchemy ORM:** The project uses `asyncpg` directly. Alembic is used only
as a migration runner \u2014 migrations are written as raw SQL via `op.execute()`.
Alembic internally requires `sqlalchemy` but no ORM models are defined.

### 8.2 Alembic Directory Layout

```
alembic/
\u251c\u2500\u2500 versions/
\u2502   \u2514\u2500\u2500 0001_initial_schema.py     # threads + generated_images tables
\u251c\u2500\u2500 env.py                         # Runtime config: async engine, migrations table
\u2514\u2500\u2500 script.py.mako                 # Template for new migration files

alembic.ini                        # Top-level config (sqlalchemy.url = env var)
```

### 8.3 `alembic.ini` (key settings)

```ini
[alembic]
script_location = alembic

# URL is overridden in env.py from DATABASE_URL env var
# Do not hard-code credentials here
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console
```

### 8.4 `alembic/env.py` (async + no ORM)

```python
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
fileConfig(config.config_file_name)

# Read DATABASE_URL from environment \u2014 never from alembic.ini
import os
DATABASE_URL = os.environ["DATABASE_URL"]


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        await conn.run_sync(
            lambda sync_conn: context.configure(connection=sync_conn)
        )
        async with conn.begin():
            await conn.run_sync(lambda _: context.run_migrations())
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

### 8.5 `alembic/versions/0001_initial_schema.py`

```python
"""Initial schema: threads and generated_images tables.

Revision ID: 0001
Revises:
Create Date: 2026-06-02
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # LangGraph manages its own tables (checkpoints, store) via checkpointer.setup()
    # Only custom application tables are managed here.
    op.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            thread_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                    TEXT NOT NULL,
            title                      TEXT,
            title_generated            BOOLEAN NOT NULL DEFAULT FALSE,
            title_generation_attempts  INT NOT NULL DEFAULT 0,
            status                     TEXT NOT NULL DEFAULT 'idle'
                                       CHECK (status IN ('idle', 'busy', 'deleting')),
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_activity_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_user_id ON threads (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_last_activity ON threads (last_activity_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads (status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS generated_images (
            image_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id             UUID NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
            request_message_id    UUID NOT NULL,
            user_id               TEXT NOT NULL,
            s3_key                TEXT NOT NULL,
            s3_url                TEXT NOT NULL,
            prompt_text           TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_generated_images_thread_id ON generated_images (thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_generated_images_user_id ON generated_images (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_generated_images_request_message_id ON generated_images (request_message_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS generated_images")
    op.execute("DROP TABLE IF EXISTS threads")
```

### 8.6 Common Alembic Commands

```bash
# Apply all pending migrations (run before starting app in any environment)
alembic upgrade head

# Check current revision in the database
alembic current

# Show migration history
alembic history --verbose

# Create a new migration file (empty, fill in upgrade/downgrade manually)
alembic revision -m "add_column_threads_metadata"

# Roll back the last migration
alembic downgrade -1

# Verify DB matches the latest migration head (use in CI)
alembic check
```

### 8.7 Startup Sequence (FastAPI lifespan)

```
1. Load Settings (pydantic-settings)
2. Configure structlog
3. Setup OTel tracing (if LANGCHAIN_TRACING_V2=true)
4. [MIGRATION] Run: alembic upgrade head  \u2190 before app boots (CI/CD or entrypoint script)
5. Create asyncpg connection pool
6. Initialize psycopg pool (for LangGraph)
7. Call AsyncPostgresSaver.setup() \u2014 creates LangGraph tables (idempotent)
8. Call AsyncPostgresStore.setup() \u2014 creates LangGraph store tables (idempotent)
9. Compile LangGraph graph
10. Initialize Qdrant client + ensure collection exists
11. Initialize Valkey (redis) client
12. Initialize Celery app
13. Start Prometheus instrumentation
14. Yield (app is ready)
15. [Shutdown] Close all pools and clients
```

> **Migration timing**: `alembic upgrade head` runs as a separate step **before** the app
> starts \u2014 either in a Docker entrypoint script or as a dedicated `migrate` service in
> Docker Compose. It does NOT run inside the FastAPI lifespan to avoid blocking startup
> and to support zero-downtime deployments.

---

## 9. Pre-commit Hooks

### 9.1 `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.15
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: local
    hooks:
      - id: pyright
        name: pyright type check
        entry: pyright src/
        language: system
        types: [python]
        pass_filenames: false
```

### 9.2 `pyrightconfig.json`

```json
{
  "pythonVersion": "3.12",
  "pythonPlatform": "Linux",
  "typeCheckingMode": "standard",
  "include": ["src", "tests"],
  "exclude": ["**/__pycache__", ".venv"],
  "venvPath": ".",
  "venv": ".venv"
}
```

---

## 10. Qdrant Collection Setup

The `qdrant_service.py` must create the `products` collection on startup if it does not exist.
The collection uses named vectors: `dense` (from OpenAI) and `sparse` (BM25 via FastEmbed).

```python
# Illustrative — full implementation in Phase 4
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
)

async def ensure_collection_exists(client: AsyncQdrantClient, settings: Settings) -> None:
    exists = await client.collection_exists(settings.qdrant_collection_name)
    if not exists:
        await client.create_collection(
            collection_name=settings.qdrant_collection_name,
            vectors_config={
                "dense": VectorParams(
                    size=settings.embedding_dims,
                    distance=Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False)
                )
            },
        )
```

---

## 11. Saleor GraphQL Client

The `saleor_client.py` must implement two functions in Phase 4:

**Product fetch** (used in Phase 5 — just stub it in Phase 4):
- `async def fetch_products_page(after: str | None) -> tuple[list[dict], str | None]`
- Cursor-based paginated GraphQL query
- Returns `(products, next_cursor)`

**JWKS fetch** (required for auth in Phase 4):
- `async def fetch_jwks() -> dict`  
- `GET {SALEOR_URL}/.well-known/jwks.json`
- Called at startup; cached in memory; refreshed on unknown `kid`
- 30-second HTTP timeout; raises on non-200 status

---

## 12. Integration Test Plan (Phase 4 scope only)

All integration tests run against Docker Compose services (not mocked).

| Test File | What It Verifies |
|---|---|
| `tests/integration/test_postgres.py` | Pool creation, migration runs, `threads` table CRUD, LangGraph table creation |
| `tests/integration/test_qdrant.py` | Collection creation, upsert a point, delete a point, basic search |
| `tests/integration/test_valkey.py` | SET/GET/DEL on DB 0 and DB 1, TTL expiry |
| `tests/integration/test_saleor_client.py` | JWKS fetch (mock Saleor server via `respx`) |
| `tests/integration/test_health.py` | `GET /health` = 200, `GET /ready` = 200 when all services up |
| `tests/integration/test_ready_degraded.py` | `GET /ready` = 503 when Qdrant is stopped |

---

## 13. Phase 4 Task Checklist

Work through tasks in this order. Each task must pass `ruff` + `pyright` before moving on.

### 13.1 Repository Init

- [ ] Initialize project with `uv init` + `uv sync`
- [ ] Create `pyproject.toml` with all pinned versions (Section 3.2)
- [ ] Create `.env.example` (Section 5.1)
- [ ] Create `.pre-commit-config.yaml` + `pyrightconfig.json` (Section 9)
- [ ] Create `src/app/__init__.py` and set `PYTHONPATH=src`
- [ ] Run `pre-commit install`

### 13.2 Configuration

- [ ] Implement `src/app/config.py` (Section 6.1)
- [ ] Verify all env vars load correctly from `.env`
- [ ] Add `get_settings` to `src/app/dependencies.py`

### 13.3 Observability Bootstrap

- [ ] Implement `src/app/observability/logging.py` — structlog JSON processor chain
- [ ] Implement `src/app/observability/tracing.py` — OTel init (no-op if tracing disabled)
- [ ] Call both in FastAPI lifespan before any other setup

### 13.4 Database Layer

- [ ] Implement `src/app/db/session.py` — asyncpg pool + psycopg pool factories
- [ ] Initialize Alembic: `alembic init alembic` — configure `env.py` (Section 8.4)
- [ ] Write `alembic/versions/0001_initial_schema.py` (Section 8.5)
- [ ] Verify migration runs: `alembic upgrade head`
- [ ] Verify rollback works: `alembic downgrade -1`, then `alembic upgrade head` again
- [ ] Implement `src/app/repositories/thread_repo.py` — CRUD stubs
- [ ] Implement `src/app/repositories/image_repo.py` — CRUD stubs

### 13.5 External Service Clients

- [ ] Implement `src/app/services/qdrant_service.py` — client init + collection setup
- [ ] Implement `src/app/services/valkey_service.py` — ping + basic key ops
- [ ] Implement `src/app/services/saleor_client.py` — JWKS fetch + product fetch stub
- [ ] Implement `src/app/services/s3_service.py` — boto3 client init stub

### 13.6 Auth

- [ ] Implement `src/app/auth/jwt_verifier.py` — JWKS cache + RS256 verify
- [ ] Implement `src/app/auth/hmac_verifier.py` — `hmac.compare_digest` wrapper

### 13.7 FastAPI App + Endpoints

- [ ] Implement `src/app/main.py` — app factory + full lifespan (Section 8.7)
- [ ] Implement `src/app/api/health.py` — `/health` and `/ready` (Section 7)
- [ ] Wire Prometheus metrics via `prometheus-fastapi-instrumentator`
- [ ] Register router in `src/app/api/router.py`

### 13.8 Domain Models

- [ ] Create `src/app/models/product.py` — `ProductItem` Pydantic model
- [ ] Create `src/app/models/profile.py` — `UserProfile` Pydantic model
- [ ] Create `src/app/models/thread.py` — `ThreadStatus` enum + `Thread` model
- [ ] Create `src/app/models/image.py` — `GeneratedImage` model

### 13.9 Agent Scaffold

- [ ] Define `src/app/agent/state.py` — `AgentState` TypedDict (references `models/`)
- [ ] Create stub files in `src/app/agent/nodes/` — 5 nodes, each returns `AgentState` unchanged
- [ ] Create stub directories `src/app/agent/subagents/product_rag/` and `trend_scout/` with empty `agent.py` + `tools.py`
- [ ] Implement `src/app/agent/graph.py` — empty but compilable LangGraph graph with all 7 node stubs wired
- [ ] Call `await checkpointer.setup()` and `await store.setup()` in lifespan

### 13.10 Celery Scaffold

- [ ] Implement `src/app/tasks/celery_app.py` — app factory + Beat schedule placeholder
- [ ] Create stub task files (`webhook_task`, `reindex_task`, `cleanup_task`)

### 13.11 Docker Compose

- [ ] Write `docker-compose.yml` (Section 4.2)
- [ ] Write `docker-compose.override.yml` (Section 4.3)
- [ ] Write `docker/app/Dockerfile` (Section 4.4)
- [ ] Write `docker/promtail/config.yml` — basic Docker log scraping config
- [ ] Write `docker/grafana/datasources/datasources.yml` — Prometheus + Loki
- [ ] Verify `docker compose up --build` starts all 9 services without errors

### 13.12 Integration Tests

- [ ] Write `tests/conftest.py` with async client fixture and Docker service fixtures
- [ ] Write all integration tests from Section 12
- [ ] All integration tests pass against live Docker Compose stack

### 13.13 Final Validation

- [ ] `ruff check src/ tests/` — zero errors
- [ ] `pyright src/` — zero errors
- [ ] `alembic check` — DB matches head revision
- [ ] `pytest tests/integration/ -v` — all pass
- [ ] `GET /health` → 200
- [ ] `GET /ready` → 200
- [ ] `GET /metrics` → 200 with Prometheus format
- [ ] `docker compose ps` — all 9 services healthy

---

## 14. Out of Scope for Phase 4

The following are explicitly deferred to later phases:

- Actual agent node logic (Phase 7)
- LlamaIndex indexing pipeline (Phase 5)
- SSE streaming endpoint full implementation (Phase 6)
- Webhook processing logic (Phase 8)
- Rate limiting + caching wiring (Phase 6)
- LangSmith trace verification (Phase 9)
- Security review of JWT expiry, HMAC timing (Phase 10)
- Coverage enforcement above scaffold (Phase 11)
