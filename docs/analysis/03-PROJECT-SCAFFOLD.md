# Project Scaffold & Infrastructure Setup

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 2.0
- **Date**: 2026-06-11
- **Status**: Reference — scaffold completed; document now describes what was built

> This is a **reference document** describing the project scaffold (directory
> structure, dependencies, Docker Compose, Alembic, pre-commit hooks) that the
> application is built on. It is **not** a phase plan. For phase status and
> remaining work, see [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md).
>
> For the agent architecture and node designs, see
> [docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md).
>
> Originally the scaffold work was scoped under "Phase 4" of an older phase
> numbering (1–13). The current implementation plan uses phases 1–14 where
> Phase 4 = Product RAG; the scaffold is simply the foundation all phases
> build on.

---

## 1. Objective

The project scaffold establishes the full development foundation:

- Repository structure with all module boundaries defined
- Python dependency tree pinned to exact stable versions
- Docker Compose stack (11 services) runnable locally with one command
- Configuration system (`pydantic-settings`) loading all env vars from the spec
- Health and readiness endpoints verifying all external connections
- Saleor GraphQL client + JWKS client wired up
- Qdrant service (collection creation with hybrid dense + sparse config)
- Pre-commit hooks (`ruff` + `pyright`) enforced on every commit
- Integration test suite verifying all external connections work

The system boots, passes `/health` and `/ready`, connects to all 11 services,
and runs a compiled LangGraph graph with the full 8-node topology. Pipeline
nodes (profiler, summarize, orchestrate, product_rag) are real; the rest
(synthesize, generate_title, generate_image, trend_scout) are stubs awaiting
the phases in [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md).

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
│   ├── README.md                       # Documentation index
│   ├── 05-IMPLEMENTATION-PLAN.md       # Master implementation plan
│   ├── analysis/
│   │   ├── 01-USE-CASE-ANALYSIS.md
│   │   ├── 02-REQUIREMENTS-SPECIFICATION.md
│   │   ├── 03-PROJECT-SCAFFOLD.md      # ← this file
│   │   └── 04-MULTI-AGENT-ARCHITECTURE-DESIGN.md
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
│       │   │   ├── generate_title.py    # STUB — LLM call + retry + SSE thread_title (Phase 12)
│       │   │   ├── profiler.py          # DONE — LLM merge from snapshot + message, AsyncPostgresStore
│       │   │   ├── orchestrate.py       # DONE — 6-intent routing + remaining_steps guard
│       │   │   ├── summarize.py         # DONE — threshold + RemoveMessage + SUMMARIZE_MODEL
│       │   │   ├── synthesize.py        # STUB — SSE streaming (Phase 12)
│       │   │   └── generate_image.py    # STUB — DALL-E + S3 + Valkey quota (Phase 13)
│       │   │
│       │   ├── subagents/             # Complex sub-agents with internal pipelines / ReAct loops
│       │   │   ├── __init__.py
│       │   │   ├── product_rag/       # ProductRAGAgent — 3-stage LangGraph StateGraph
│       │   │   │   ├── __init__.py
│       │   │   │   ├── agent.py       # Compiled subgraph + run_product_rag wrapper
│       │   │   │   ├── nodes.py       # prepare_query_node, hybrid_search_node, llm_postprocess_node
│       │   │   │   ├── state.py       # ProductRAGState TypedDict
│       │   │   │   ├── schemas.py     # PrepareQueryOutput, structured-output schemas
│       │   │   │   └── fault_tolerance.py  # RetryPolicy, TimeoutPolicy, error handlers
│       │   │   └── trend_scout/       # TrendScoutNode — create_agent (LangChain) ReAct loop
│       │   │       ├── __init__.py
│       │   │       ├── agent.py       # STUB — create_agent + run_trend_scout wrapper (Phase 11)
│       │   │       └── tools.py       # STUB — @tool tavily_search, duckduckgo_search (Phase 11)
│       │   │
│       │   └── prompts/               # Externalized LLM prompt templates (11 .md files)
│       │       ├── __init__.py            # load_prompt(name) helper
│       │       ├── orchestrator_system.md
│       │       ├── profiler_system.md
│       │       ├── summarize_system.md
│       │       ├── prepare_query_system.md
│       │       ├── rerank_system.md
│       │       ├── title_system.md
│       │       ├── trend_scout_system.md
│       │       ├── synthesize_sufficient_system.md
│       │       ├── synthesize_clarification_system.md
│       │       ├── synthesize_out_of_scope_system.md
│       │       └── synthesize_fallback_system.md
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
│       │   ├── process_webhook.py     # process_webhook (upsert/delete vector)
│       │   ├── reindex_products.py    # reindex_products (full catalog sync)
│       │   ├── cleanup_expired_threads.py # cleanup_expired_threads (nightly Beat)
│       │   └── delete_thread.py       # delete_thread (cascade S3 + DB cleanup)
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
│   │   ├── Dockerfile
│   │   └── entrypoint.sh
│   ├── promtail/
│   │   └── config.yaml
│   └── grafana/
│       ├── datasources/
│       │   └── datasources.yml
│       └── dashboards/
│           └── main.json
│
├── docker-compose.yml                 # Full 11-service stack (actual; NOTE: original spec said 9)
├── docker-compose.override.yml        # Dev overrides: hot reload, debug ports — SKIP: not created
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
| `nodes/generate_title.py` | Simple node | Plain function | No |
| `nodes/profiler.py` | Simple node | Plain function + LLM call | No |
| `nodes/orchestrate.py` | Simple node | Plain function + LLM tool binding | No |
| `nodes/summarize.py` | Simple node | Plain function + LLM call | No |
| `nodes/synthesize.py` | Simple node | Plain function + LLM stream | No |
| `nodes/generate_image.py` | Simple node | Plain function | No |
| `subagents/product_rag/` | Sub-agent | **LangGraph `StateGraph`** (3 stages) | No internal loop — fixed pipeline |
| `subagents/trend_scout/` | Sub-agent | **LangChain `create_agent`** | YES — ReAct tool-use loop |

Both sub-agents expose a single entry-point function called by the LangGraph
node wrapper in `graph.py`.  For product_rag, the internal pipeline is a
fixed 3-stage `StateGraph` (no LLM decision points).  For trend_scout, the
internal pattern is a ReAct loop driven by `create_agent` so the LLM decides
when to invoke the search tools.

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
| `celery` | `5.6.3` | Async task queue (redis transport via `redis==8.0.0` in deps) |
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
    "celery==5.6.3",
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
| `postgres` | `postgres` | `16.14-alpine` |
| `qdrant` | `qdrant/qdrant` | `v1.18.1` |
| `valkey` | `valkey/valkey` | `9.1.0-alpine` |
| `rabbitmq` | `rabbitmq` | `4.3.1-management-alpine` |
| `prometheus` | `prom/prometheus` | `v3.4.0` |
| `grafana` | `grafana/grafana` | `13.0.2` |
| `loki` | `grafana/loki` | `3.7.2` |
| `promtail` | `grafana/promtail` | `3.5.0` |

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
    command: uvicorn app.main:app --host 0.0.0.0 --port 8080
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s

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
    image: postgres:16.14-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-app}
      POSTGRES_USER: ${POSTGRES_USER:-app}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks: [app-net]
    ports:
      - "5433:5432"
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

  valkey:
    image: valkey/valkey:9.1.0-alpine
    command: valkey-server --save 60 1 --loglevel warning
    volumes:
      - valkey_data:/data
    networks: [app-net]
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:4.3.1-management-alpine
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
    image: grafana/promtail:3.5.0
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock
      - ./docker/promtail/config.yaml:/etc/promtail/config.yaml
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

The actual `docker/app/Dockerfile` is a single-stage build that:

- Installs `libpq-dev` (required by `psycopg` binary and `cryptography`).
- Copies `uv` from `ghcr.io/astral-sh/uv:latest` (no pinned version — the
  image is rebuilt on every `uv` release, so a multi-stage layer pin
  would cause stale caches).
- Layer-caches dependencies by copying `pyproject.toml` and `uv.lock`
  first and running `uv sync --frozen --no-dev` before any source.
- Copies `src/`, `alembic/`, `alembic.ini`, and `docker/app/entrypoint.sh`.
- Exposes **8080** and uses `/entrypoint.sh` as the entrypoint (the
  entrypoint runs `alembic upgrade head` then `exec`s uvicorn — see note
  in Section 8.7 about migration timing).

```dockerfile
# docker/app/Dockerfile (actual)
FROM python:3.12-slim

# Install system dependencies required by psycopg binary and cryptography.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency installation.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests first for layer caching.
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev extras).
RUN uv sync --frozen --no-dev

# Copy application source.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Copy Docker entrypoint script.
COPY docker/app/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
```

---

## 5. Environment Variables

### 5.1 .env.example

The shipped `.env.example` matches the values in `.env` for local Docker
Compose development.  All env vars are read by `src/app/config.py` and surfaced
as `Settings` fields.

```dotenv
# ── PostgreSQL ────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql+psycopg://app:changeme@postgres:5432/app
POSTGRES_DB=app
POSTGRES_USER=app
POSTGRES_PASSWORD=changeme

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=products

# ── Valkey (Redis-compatible) ─────────────────────────────────────────────────
# DB 0 = rate limiting (slowapi), DB 1 = response cache (fastapi-cache2), DB 2 = Celery results
VALKEY_URL=redis://valkey:6379

# ── Celery / RabbitMQ ─────────────────────────────────────────────────────────
CELERY_BROKER_URL=amqp://guest:guest@rabbitmq:5672//
CELERY_RESULT_BACKEND=redis://valkey:6379/2
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...

# ── LLM Model Names ───────────────────────────────────────────────────────────
RESPONSE_MODEL=gpt-5.4
ORCHESTRATOR_MODEL=gpt-5.4-mini
TITLE_MODEL=gpt-5.4-nano
SUMMARIZE_MODEL=gpt-5.4-mini
RERANK_MODEL=gpt-5.4-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMS=1536

# ── Tavily Search ─────────────────────────────────────────────────────────────
TAVILY_API_KEY=tvly-...

# ── Saleor ────────────────────────────────────────────────────────────────────
SALEOR_URL=http://host.docker.internal:8000
SALEOR_APP_TOKEN=
SALEOR_WEBHOOK_SECRET=changeme-32-char-secret

# ── AWS S3 ────────────────────────────────────────────────────────────────────
AWS_S3_BUCKET=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-southeast-1

# ── Message Summarization ────────────────────────────────────────────────────
MESSAGE_SUMMARIZE_THRESHOLD=12
MESSAGE_SUMMARIZE_COUNT=8

# ── Qdrant Search Top-K ──────────────────────────────────────────────────────
QDRANT_SPARSE_TOP_K=12
QDRANT_SIMILARITY_TOP_K=12
QDRANT_HYBRID_TOP_K=9
QDRANT_RERANK_TOP_K=3

# ── Ingestion ─────────────────────────────────────────────────────────────────
DESCRIPTION_MAX_CHARS=500
SALEOR_STOREFRONT_URL=

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
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=agentic-rag-ecommerce
# LangSmith endpoint — choose the region matching your account:
#   GCP US (default): https://api.smith.langchain.com
#   GCP EU:           https://eu.api.smith.langchain.com
#   GCP APAC:         https://apac.api.smith.langchain.com
#   AWS US:           https://aws.api.smith.langchain.com
LANGSMITH_ENDPOINT=https://aws.api.smith.langchain.com

# ── Grafana (dev only) ────────────────────────────────────────────────────────
GRAFANA_USER=admin
GRAFANA_PASSWORD=admin
```

---

## 6. Configuration Module Design

### 6.1 `src/app/config.py` — Key Design Points

- Single `Settings` class extending `pydantic_settings.BaseSettings`
- `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`
- All fields have type annotations; required fields have no default
- Computed properties for derived values (e.g., `valkey_rate_limit_url`, `valkey_cache_url`)
- `@lru_cache(maxsize=1)` on `get_settings()` to avoid re-parsing on every dependency injection call

The structure below is a faithful reproduction of the live `Settings` class
(see [src/app/config.py](../../src/app/config.py) for the source of truth).

```python
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── PostgreSQL ──────────────────────────────────────────────────────────
    database_url: str

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_name: str = "products"

    # ── Valkey / Redis ──────────────────────────────────────────────────────
    valkey_url: str = "redis://localhost:6379"

    # ── Celery / RabbitMQ ───────────────────────────────────────────────────
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str

    # ── LLM Model Names ─────────────────────────────────────────────────────
    response_model: str = "gpt-5.4"
    orchestrator_model: str = "gpt-5.4-mini"
    title_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 1536

    # ── Tavily ──────────────────────────────────────────────────────────────
    tavily_api_key: str = ""

    # ── Saleor ──────────────────────────────────────────────────────────────
    saleor_url: str = "http://localhost:8080"
    saleor_app_token: str = ""
    saleor_webhook_secret: str   # min 32 chars (required)

    # ── AWS S3 ──────────────────────────────────────────────────────────────
    aws_s3_bucket: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-southeast-1"

    # ── Message Summarization ───────────────────────────────────────────────
    message_summarize_threshold: int = 12
    message_summarize_count: int = 8

    # ── LLM Model Names (extended) ──────────────────────────────────────────
    rerank_model: str = "gpt-5.4-mini"
    summarize_model: str = "gpt-5.4-mini"

    # ── Qdrant Search Top-K ─────────────────────────────────────────────────
    qdrant_sparse_top_k: int = 12
    qdrant_similarity_top_k: int = 12
    qdrant_hybrid_top_k: int = 9
    qdrant_rerank_top_k: int = 3

    # ── Ingestion ───────────────────────────────────────────────────────────
    description_max_chars: int = 500
    saleor_storefront_url: str = ""

    # ── Agent Behavior ──────────────────────────────────────────────────────
    max_agent_steps: int = 10
    agent_fallback_threshold: int = 2
    image_daily_limit: int = 10

    # ── Thread Auto-Naming ──────────────────────────────────────────────────
    title_generation_max_attempts: int = 3
    title_truncation_length: int = 50

    # ── Rate Limiting ───────────────────────────────────────────────────────
    rate_limit_chat: str = "20/minute"
    rate_limit_thread_create: str = "10/minute"
    rate_limit_read: str = "60/minute"
    rate_limit_write: str = "10/minute"
    rate_limit_reindex: str = "2/hour"

    # ── Caching ─────────────────────────────────────────────────────────────
    thread_list_cache_ttl: int = 120

    # ── Observability ───────────────────────────────────────────────────────
    log_level: str = "INFO"
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "agentic-rag-ecommerce"
    langsmith_endpoint: str = "https://aws.api.smith.langchain.com"

    @property
    def valkey_rate_limit_url(self) -> str:
        """Valkey DB 0 — rate limiting (slowapi)."""
        return f"{self.valkey_url}/0"

    @property
    def valkey_cache_url(self) -> str:
        """Valkey DB 1 — response cache (fastapi-cache2)."""
        return f"{self.valkey_url}/1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()   # type: ignore[call-arg]
```

---

## 7. Health and Readiness Endpoints

### 7.1 `GET /health` — Liveness + Readiness Probe

Checks all critical dependencies. Returns `200 OK` if all pass; `503 Service Unavailable` if any fail.

| Check | Method | Success Condition |
|---|---|---|
| PostgreSQL | `SELECT 1` | Returns row without error |
| Qdrant | `get_collection()` | Returns without error |
| Valkey | `PING` | Returns `True` |

Response on success (`200`):
```json
{
  "status": "ok",
  "checks": {
    "postgres": true,
    "qdrant": true,
    "valkey": true
  }
}
```

Response on failure (`503`):
```json
{
  "status": "degraded",
  "checks": {
    "postgres": true,
    "qdrant": false,
    "valkey": true
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
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                    TEXT NOT NULL,
            title                      TEXT,
            title_generated            BOOLEAN NOT NULL DEFAULT FALSE,
            title_generation_attempts  SMALLINT NOT NULL DEFAULT 0,
            status                     TEXT NOT NULL DEFAULT 'idle'
                                       CHECK (status IN ('idle', 'busy', 'deleting')),
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_activity_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_threads_user_id ON threads (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_threads_last_activity_at ON threads (last_activity_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS generated_images (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id             UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            user_id               TEXT NOT NULL,
            prompt                TEXT NOT NULL,
            s3_key                TEXT NOT NULL,
            s3_url                TEXT NOT NULL,
            model                 TEXT NOT NULL,
            request_message_id    TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_generated_images_thread_id ON generated_images (thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_generated_images_user_id_date ON generated_images (user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_generated_images_request_message_id ON generated_images (request_message_id)")


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

The actual startup sequence implemented in `src/app/main.py` (high-level):

```
1.  Load Settings (pydantic-settings, cached via @lru_cache)
2.  Configure structlog JSON logging
3.  Setup OTel tracing + LangSmith (no-op if LANGSMITH_TRACING=false)
4.  Create asyncpg connection pool
5.  Initialize psycopg pool (for LangGraph checkpointer/store)
6.  Call AsyncPostgresSaver.setup() \u2014 creates LangGraph checkpoint tables (idempotent)
7.  Call AsyncPostgresStore.setup() \u2014 creates LangGraph store tables (idempotent)
8.  Compile LangGraph graph (8 nodes, see agent/graph.py)
9.  Initialize Qdrant client + ensure_collection() (Section 10)
10. Initialize Valkey (redis) client
11. Initialize S3 client + ensure_bucket() (Phase 5.7)
12. Initialize Celery app (for task scheduling)
13. Start Prometheus instrumentation
14. Yield (app is ready to serve traffic)
15. [Shutdown] Close all pools, clients, and instrumentator
```

> **Migration timing**: `alembic upgrade head` runs as a **separate step
> before** the app starts \u2014 either in a Docker entrypoint script or as a
> dedicated `migrate` step in the deployment pipeline. It is intentionally
> NOT inside the FastAPI lifespan to keep startup fast and to support
> zero-downtime deployments (a new pod can boot while migrations run
> against the old schema).

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

The `qdrant_service.py` creates the `products` collection on startup. The
collection uses **named vectors** that match the LlamaIndex
`QdrantVectorStore` defaults (`enable_hybrid=True`):

- `text-dense`  — OpenAI embedding vector (size = `embedding_dims`,
  distance = COSINE, HNSW m=16, ef_construct=100)
- `text-sparse` — BM25 sparse vector (`on_disk=False`)

`QdrantService.ensure_collection()` is **drop-and-recreate tolerant**: if
the existing collection has a different `vectors_config` /
`sparse_vectors_config` (e.g. legacy `dense` / `sparse` names from before
Phase 4), it logs a warning and recreates the collection. This is safe
because ingestion (Phase 5) has not run yet at the lifespan start of
Phase 1–4.

```python
# Faithful reproduction of src/app/services/qdrant_service.py
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    HnswConfigDiff,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

_DENSE_VECTOR_NAME = "text-dense"
_SPARSE_VECTOR_NAME = "text-sparse"


class QdrantService:
    def __init__(self, settings: Settings) -> None:
        self._collection = settings.qdrant_collection_name
        self._dims = settings.embedding_dims
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self._collection):
            await self._create_collection()
            return

        info = await self._client.get_collection(self._collection)
        vectors = info.config.params.vectors
        sparse_vectors = info.config.params.sparse_vectors

        dense_ok = isinstance(vectors, dict) and _DENSE_VECTOR_NAME in vectors
        sparse_ok = (
            isinstance(sparse_vectors, dict)
            and _SPARSE_VECTOR_NAME in sparse_vectors
        )

        if dense_ok and sparse_ok:
            return   # already correct
        await self._client.delete_collection(collection_name=self._collection)
        await self._create_collection()

    async def _create_collection(self) -> None:
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                _DENSE_VECTOR_NAME: VectorParams(
                    size=self._dims,
                    distance=Distance.COSINE,
                    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
                ),
            },
            sparse_vectors_config={
                _SPARSE_VECTOR_NAME: SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                ),
            },
        )
```

---

## 11. Saleor GraphQL Client

The `saleor_client.py` is fully implemented and exposes:

- `async def fetch_all_products(after: str | None = None) -> list[dict]`
  - Cursor-based paginated GraphQL query over Saleor's `products` channel
  - Returns raw Saleor product dicts (multiple pages fetched in one call
    by paging internally)
  - Used by `reindex_products` Celery task (Phase 8) and the admin
    `/admin/reindex` endpoint
- `def node_to_product_payload(node: dict) -> ProductItem`
  - Mapper from Saleor's GraphQL product node to the `ProductItem`
    domain model (used by RAG, agent state, and SSE)
  - Strips HTML, truncates description to `description_max_chars`,
    extracts `price_min`, `price_max`, `currency`, and the Saleor
    `collections` slug list
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

## 13. Scaffold Verification Checklist

The scaffold work described in this document is **complete** (Phases 1–4
of the implementation plan). The checklist below is the operational
verification a developer runs after `git clone` to confirm the scaffold
is intact. It is **not** a future task list.

```bash
# 1. Install + lockfile sync
uv sync

# 2. Install pre-commit hooks
pre-commit install

# 3. Lint + format + type check
ruff check .
ruff format --check .
pyright

# 4. Database migration (run before first boot)
alembic upgrade head

# 5. Bring up the full stack
docker compose up -d

# 6. Verify all 11 services are healthy
docker compose ps

# 7. Hit the health endpoints
curl -f http://localhost:8080/health      # 200 ok
curl -f http://localhost:8080/ready       # 200 ok
curl -f http://localhost:8080/metrics     # 200 prometheus format

# 8. Inspect observability stack
open http://localhost:3000                # Grafana (admin / admin)
open http://localhost:15672               # RabbitMQ management (guest / guest)

# 9. Run the integration suite against the live stack
pytest tests/integration/ -v

# 10. Confirm the compiled LangGraph graph imports cleanly
uv run python -c "from app.agent.graph import build_graph; g = build_graph(); print(g)"
```

If any of the above fail, compare the failing component against the
sections in this document and against the live source in `src/app/`.

---

## 14. Out of Scope for the Scaffold

The scaffold is the foundation. Everything below belongs to a later
phase in [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md)
and is documented there:

- RAG indexing pipeline (Phase 5)
- Full agent node logic — synthesize, generate_title, generate_image,
  trend_scout (Phases 11–13)
- Webhook processing logic (Phase 8)
- Rate limiting + caching wiring (Phase 6)
- LangSmith trace verification (Phase 9)
- Security review of JWT expiry, HMAC timing (Phase 10)
- Coverage enforcement above the 80% baseline (Phase 11)
