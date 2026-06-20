# Implementation Plan — AI POD Stylist

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 2.0
- **Date**: 2026-06-10
- **Status**: Active

> This document is the authoritative implementation guide for all
> remaining phases.  It is based on `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md`
> (DRAFT 0.6) as the primary source of truth.  Where older documents
> conflict with DRAFT 0.6, DRAFT 0.6 takes precedence.
>
> **Phase status legend**:
> - `[DONE]` — implementation complete, tests passing
> - `[PENDING]` — work not yet started
>
> Phases 1–14 are `[DONE]`.  No new phase is planned in this document.
>
> **Observability rollout (project phases 15–18) is tracked in
> [07-OBSERVABILITY-IMPLEMENTATION-PLAN.md](07-OBSERVABILITY-IMPLEMENTATION-PLAN.md).**
> Project phase 16 (observability phase 2: log pipeline — Alloy
> replaces Promtail) was shipped 2026-06-20 — see
> `history/16_0_0_LOG_PIPELINE_ALLOY.md`.

---

## Current State

Phases 1–4 of the agent layer are complete.  Many infrastructure pieces
already exist as real implementations (see audit table below).  The
remaining work focuses on (a) the API surface (5 stub endpoint files),
(b) the remaining agent nodes (synthesize, generate_title,
generate_image, trend_scout), (c) the RAG ingestion pipeline, (d) the
webhook → Celery dispatch chain, (e) the cleanup Celery tasks, and
(f) cross-cutting wiring (slowapi + fastapi-cache2).

### Audit — implementation status by component

| Component | File | Status |
|---|---|---|
| Application config | `src/app/config.py` | DONE — all FR-listed env vars present |
| DB session | `src/app/db/session.py` | DONE — asyncpg + psycopg pools |
| Alembic migration | `alembic/versions/0001_initial_schema.py` | DONE — `threads` + `generated_images` |
| `ThreadRepository` | `src/app/repositories/thread_repo.py` | DONE — full CRUD + cursor pagination |
| `ImageRepository` | `src/app/repositories/image_repo.py` | PARTIAL — needs `delete_by_thread`, `list_by_message_id`, `count_by_user_date`; `ThreadRepository.find_expired` also needed |
| `QdrantService` | `src/app/services/qdrant_service.py` | DONE — `ensure_collection` + drop+recreate guard |
| `SaleorClient` | `src/app/services/saleor_client.py` | DONE — cursor-paginated `fetch_all_products` |
| `S3Service` | `src/app/services/s3_service.py` | PARTIAL — needs `delete`, `ensure_bucket`, `build_key` |
| `ValkeyService` | `src/app/services/valkey_service.py` | PARTIAL — needs `increment_quota`, `delete_pattern` |
| JWT auth | `src/app/auth/jwt_verifier.py` + `dependencies.py` | DONE — RS256 + JWKS + `CurrentUserDep` + `AdminDep` |
| HMAC verifier | `src/app/auth/hmac_verifier.py` | DONE |
| Logging | `src/app/observability/logging.py` | DONE — structlog JSON |
| Tracing | `src/app/observability/tracing.py` | DONE — LangSmith env passthrough + OpenInference LlamaIndex |
| Prometheus | `src/app/main.py` | DONE — `Instrumentator().expose()` at `/metrics` |
| Lifespan | `src/app/main.py:lifespan()` | DONE — logging → tracing → DB → checkpointer → store → graph → Qdrant → Valkey |
| Celery factory | `src/app/tasks/celery_app.py` | DONE — 4 queues + beat schedule + RabbitMQ 4.x compat |
| Health API | `src/app/api/health.py` | DONE |
| LangGraph graph | `src/app/agent/graph.py` | DONE — conditional edges wired |
| Profiler node | `src/app/agent/nodes/profiler.py` | DONE — LLM merge + AsyncPostgresStore |
| Summarize node | `src/app/agent/nodes/summarize.py` | DONE — threshold + `RemoveMessage` |
| Orchestrate node | `src/app/agent/nodes/orchestrate.py` | DONE — tool binding + fallback guard |
| ProductRAG subagent | `src/app/agent/subagents/product_rag/` | DONE (Phase 4) |
| Prompts | `src/app/agent/prompts/` | DONE — 12 files (orchestrator, profiler, summarize, prepare_query, rerank, title, trend_scout, 4× synthesize variants) |
| Thread API | `src/app/api/threads.py` | STUB — 5 endpoints returning 501 |
| Chat SSE API | `src/app/api/chat.py` | STUB — 1 endpoint returning 501 |
| Profile API | `src/app/api/profile.py` | DONE — admin reads long-term style profile from `AsyncPostgresStore` (Phase 9) |
| Admin API | `src/app/api/admin.py` | DONE — reindex trigger (Phase 6) + reindex list + threads list (Phase 9) |
| Webhook API | `src/app/api/webhooks.py` | PARTIAL — HMAC verify done, Celery dispatch stubbed |
| `synthesize` node | `src/app/agent/nodes/synthesize.py` | DONE (Phase 12) — 4-prompt intent dispatch, LLM streaming, products / done SSE events |
| `generate_title` node | `src/app/agent/nodes/generate_title.py` | DONE (Phase 12) — LLM with truncation fallback, thread_title SSE, Valkey cache invalidation |
| `generate_image` node | `src/app/agent/nodes/generate_image.py` | DONE (Phase 13) — DALL-E + S3 + Valkey quota + image_ready / image_failed SSE |
| TrendScout subagent | `src/app/agent/subagents/trend_scout/agent.py` | DONE (Phase 11) — `create_agent` + Tavily (private DDG fallback) + `SummarizationMiddleware` |
| `ProductIndexer` | `src/app/rag/indexer.py` | STUB |
| `ProductRetriever` | `src/app/rag/retriever.py` | DELETE — deprecated by Phase 4 product_rag subagent |
| `process_webhook` task | `src/app/tasks/process_webhook.py` | STUB |
| `reindex_products` task | `src/app/tasks/reindex_products.py` | STUB |
| `cleanup_expired_threads` task | `src/app/tasks/cleanup_expired_threads.py` | DONE (Phase 10) — NFR-015 chain, sequential sweep, `set_status('deleting')` per thread |
| `delete_thread` task | `src/app/tasks/delete_thread.py` | DONE (Phase 10) — NFR-015 chain, transient-error retry, structured return value |
| slowapi rate limit | `src/app/main.py` | NOT WIRED — dep + env present |
| fastapi-cache2 | `src/app/main.py` | NOT WIRED — dep + env present |
| `tests/unit/services/` | — | EMPTY directory |
| `tests/unit/tasks/` | — | EMPTY directory |
| HTTP integration tests | `tests/integration/` | MISSING — thread/chat/profile/admin/webhook/rate-limit |

---

## Phases Overview

| Phase | Name | Scope | Status |
|---|---|---|---|
| 1 | Foundation Fixes | Config, AgentState, graph topology, prompt scaffolding | DONE |
| 2 | Profile + Memory Management | ProfilerNode, SummarizeNode | DONE |
| 3 | Orchestration | OrchestratorNode, conditional routing | DONE |
| 4 | Product RAG | ProductRAGAgent subgraph + hybrid search | DONE |
| 5 | Cross-cutting wiring + Repo/Service enhancements | slowapi, fastapi-cache2, S3Service/ValkeyService/Repository extensions | DONE |
| 6 | RAG Ingestion | ProductIndexer real, reindex_products Celery task, delete `rag/retriever.py` | DONE |
| 7 | Webhook handling | `api/webhooks.py` Celery dispatch, `process_webhook` task real | DONE |
| 8 | Thread Management API | 5 thread endpoints + history + status guards + cache invalidation | DONE |
| 9 | Profile + Admin API | 4 admin endpoints (profile, reindex trigger, reindex list, all threads) | DONE |
| 10 | Cleanup Celery tasks | `delete_thread` + `cleanup_expired_threads` real | DONE |
| 11 | Trend Scout | TrendScoutNode via `create_agent` + Tavily (private DDG fallback) + `SummarizationMiddleware` | DONE |
| 12 | Synthesize + Title Generation | `synthesize.py` + `generate_title.py` real (4-prompt dispatch) | DONE |
| 13 | Image Generation | `generate_image.py` real — DALL-E + S3 + Valkey quota | DONE |
| 14 | Chat SSE endpoint | `api/chat.py` real — 7 SSE event types + shared-resource injection | DONE |

---

## Phase 1 — Foundation Fixes — DONE

Status: 119 tests passing (4-warning baseline), 0 lint errors,
0 pyright errors.  See `history/1_*_*.md` for the full decision record
and `temp/phase-1-foundation.md` for the implementation log.

---

## Phase 2 — Profile + Memory Management — DONE

Status:  ProfilerNode + SummarizeNode real implementations with
`AsyncPostgresStore` for profile persistence and threshold-based
`RemoveMessage` summarization.  All tests passing.

---

## Phase 3 — Orchestration — DONE

Status:  OrchestratorNode real with `update_intent` tool binding,
`remaining_steps` fallback guard, and product-first routing rule.
`route_orchestrate` conditional edge wired in the graph.

---

## Phase 4 — Product RAG — DONE

Status:  3-stage subgraph (`prepare_query → hybrid_search → llm_postprocess`)
with LangGraph `RetryPolicy` + `TimeoutPolicy` + per-node
`error_handler`, dual-path `AsyncQdrantClient` injection, and all
3 `qdrant_*_top_k` settings correctly mapped to the corresponding
`VectorStoreQuery` fields.  119 tests passing across 6 test files.
See `temp/phase-4-product-rag.md`.

---

## Phase 5 — Cross-cutting wiring + Repo/Service enhancements + Path A foundation

Status:  SlowAPI (per-user `get_jwt_user_id_or_ip` key, exempt on
health/ready/webhooks), FastAPI cache2 (`ValkeyBackend`, custom
`thread_list_key_builder`), S3Service (`build_key`, `aupload_image`,
`delete`, `ensure_bucket` — never `create_bucket`), ValkeyService
(`increment_quota`, `delete_pattern` via SCAN), ThreadRepository
(`find_expired`), ImageRepository (`delete_by_thread`,
`list_by_message_id`, `count_by_user_date`), Path A shared-resource
injection (`app.state.s3`, `app.state.openai`, shutdown order locked
in `history/5_0_0_SHARED_RESOURCE_INJECTION.md`).  206 tests passing
(Docker stack running), 77% coverage — gap is in Phase 6+ code
(`rag/`, `tasks/reindex_*`, `saleor_client`, `schemas/api`,
`schemas/webhook`, `app/api/threads`, `app/api/webhooks`,
`models/product`).  See `temp/phase-5-cross-cutting-wiring.md` for
the full implementation log.

### Objective

Make the application production-ready at the boundaries: enforce
per-user rate limits, cache thread-list responses, add the small
service / repository methods that downstream phases need (S3 delete +
bucket ensure, Valkey quota + pattern delete, thread/image repos
with cleanup and history-attachment queries), and lay the
**shared-resource foundation** for Path A injection.

Phase 5 wires the long-lived clients (`S3Service`, `AsyncOpenAI`)
onto `app.state` so that Phase 14 (chat SSE) can inject them into
`config["configurable"]` and eliminate per-request client
construction. The convention is locked in a Phase-5 ADR
(`history/5_0_0_SHARED_RESOURCE_INJECTION.md`).

Scope of Path A in Phase 5:

- `app.state.qdrant` (existing) — keep
- `app.state.valkey` (existing) — keep
- `app.state.s3` (**NEW**) — `S3Service` singleton, `ensure_bucket` at startup
- `app.state.openai` (**NEW**) — `AsyncOpenAI` singleton for DALL-E (Phase 13)

Resources that do **NOT** go on `app.state` (rationale in ADR):

- `asyncpg.Pool` / `psycopg_pool` — already module-level singletons in `app/db/session.py`
- `ThreadRepository` / `ImageRepository` — thin wrappers, free to construct inline
- `ChatOpenAI` (LangChain wrapper) — `langchain-openai==1.2.2` does not expose a
  pre-constructed `AsyncOpenAI` through `ChatOpenAI.__init__` in a clean way;
  the OpenAI SDK lazy-initialises `httpx.AsyncClient`, so per-call cost is low.
  Documented as a known limitation in the ADR; revisit only if profiling shows
  it is a bottleneck.

Phase 14 (not Phase 5) is when these resources are actually threaded into
`config["configurable"]` and consumed by nodes.

This phase touches no agent nodes — it only extends the
infrastructure they consume.

### Tasks

#### 5.1 Wire slowapi in `src/app/main.py`

- Initialise a `Limiter(key_func=get_jwt_user_id, storage_uri=settings.valkey_rate_limit_url)`.
- Add `SlowAPIMiddleware` to the app.
- Apply per-route `@limiter.limit(...)` decorators using the values
  from `RATE_LIMIT_CHAT`, `RATE_LIMIT_THREAD_CREATE`, `RATE_LIMIT_READ`,
  `RATE_LIMIT_WRITE`, `RATE_LIMIT_REINDEX` (Phase 8 + 9 will attach
  these to the actual endpoints).
- Exempt `/webhooks/*`, `/health`, `/ready`, `/metrics` (FR-094).

The `key_func` is a thin wrapper around `CurrentUserDep` that
extracts `user_id` from the verified JWT — never fall back to IP,
since the service runs behind a load balancer (FR-091).

#### 5.2 Wire fastapi-cache2 in `src/app/main.py`

- `FastAPICache.init(RedisBackend(settings.valkey_cache_url))`.
- `Cache.clear()` on shutdown.
- Decorator `@cache(expire=settings.thread_list_cache_ttl)` will be
  applied to `GET /api/v1/threads` in Phase 8 (FR-095 to FR-098).
  Cache key pattern: `threads:{user_id}:{before}:{limit}`.

#### 5.3 Extend `S3Service` (`src/app/services/s3_service.py`)

Add three methods to align with the spec key pattern
`images/{user_id}/{thread_id}/{timestamp}.png` and to support
cleanup:

```python
def build_key(self, user_id: str, thread_id: uuid.UUID, timestamp: int) -> str
def upload_image(self, user_id: str, thread_id: uuid.UUID, timestamp: int, image_bytes: bytes, content_type: str = "image/png") -> str
def delete(self, key: str) -> None
async def ensure_bucket(self) -> None  # head_bucket only; raise BucketNotFound on missing
```

`ensure_bucket` is called once at startup from `lifespan`.  It
verifies that the bucket is reachable and **does not** create it —
AWS infrastructure (S3 bucket, IAM, etc.) is provisioned by
Terraform, not by application code.  If the bucket is missing, the
service must fail fast at startup so an operator notices and runs
the Terraform stack.

#### 5.4 Extend `ValkeyService` (`src/app/services/valkey_service.py`)

Add quota + cache-invalidation helpers:

```python
async def get(self, key: str) -> str | None
async def set(self, key: str, value: str, ttl: int | None = None) -> None
async def delete(self, key: str) -> None
async def delete_pattern(self, pattern: str) -> int         # SCAN + DEL, returns count
async def increment_quota(self, key: str, ttl: int = 86400) -> int  # INCR + EXPIRE on first hit
async def get_quota(self, key: str) -> int                  # GET, defaults to 0
```

`increment_quota` is used by the image generation node (FR-052) and
`delete_pattern` is used for thread-list cache invalidation
(FR-098).

#### 5.5 Extend `ThreadRepository` (`src/app/repositories/thread_repo.py`)

Add one method for the cleanup task:

```python
async def find_expired(self, cutoff: datetime) -> list[uuid.UUID]
```

Query: `SELECT id FROM threads WHERE last_activity_at < $1 AND status != 'deleting'`.

#### 5.6 Extend `ImageRepository` (`src/app/repositories/image_repo.py`)

Add three methods:

```python
async def delete_by_thread(self, thread_id: uuid.UUID) -> int
async def list_by_message_id(self, request_message_id: str) -> list[GeneratedImage]
async def count_by_user_date(self, user_id: str, date: datetime.date) -> int
```

`list_by_message_id` is used by the history endpoint to attach
images to the correct `AIMessage` turn (FR-020).

#### 5.7 Update `lifespan` in `src/app/main.py`

- Initialise an `S3Service` from settings and call `ensure_bucket`.
  If the bucket is missing the service must fail fast — operators
  provision AWS infra via Terraform, not the application.
- Initialise slowapi (5.1) and fastapi-cache2 (5.2) globals so that
  endpoint decorators in Phase 8/9 can pick them up.
- On shutdown, close the S3 client and clear the cache.

#### 5.8 Path A — Shared-resource injection foundation (NEW)

Establish the `app.state` convention that Phase 14 (chat SSE) will
consume. Only Qdrant had Path A wiring before this phase; this sub-task
extends the same pattern to S3 and OpenAI so all expensive-to-construct
clients are created once at startup and shared across requests.

- **5.8.1 `app.state.s3`** — instantiate `S3Service(settings)` once in
  `lifespan`. Call `await s3.ensure_bucket()` during startup (fail fast
  if bucket missing — Terraform owns it, application must not create
  it). Expose `s3.client` property so Phase 14 can inject it into
  `config["configurable"]["s3_service"]`.

- **5.8.2 `app.state.openai`** — instantiate `AsyncOpenAI(api_key=...)`
  once in `lifespan`. This client is consumed by Phase 13 (DALL-E
  image generation) via `OpenAIDep` in `dependencies.py`. **Not used
  by LangChain `ChatOpenAI` nodes** (see ADR for the LangChain 1.2.2
  limitation).

- **5.8.3 Shutdown order** — close `app.state.openai` first (no
  dependents), then `app.state.s3` (boto3 sync wrapped in
  `asyncio.to_thread`), then the existing `cache_redis`, `valkey`,
  `qdrant`, DB pools.

- **5.8.4 What does NOT go on `app.state`** (rationale in ADR):
  - `asyncpg.Pool` / `psycopg_pool` — already module-level singletons
    in `app/db/session.py`.
  - `ThreadRepository` / `ImageRepository` — thin asyncpg wrappers;
    per-request construction is essentially free.
  - `ChatOpenAI` instances — per-node pattern retained.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/services/test_s3_service.py` | `build_key` returns `images/{user_id}/{thread_id}/{timestamp}.png`; `delete` calls `s3.delete_object`; `ensure_bucket` returns silently when bucket exists, raises `BucketNotFound` (or wraps `ClientError` 404) when missing — never creates; `.client` and `.bucket` properties exposed |
| `tests/unit/services/test_valkey_service.py` | `increment_quota` first call returns 1 and sets TTL; subsequent calls increment; `delete_pattern` removes all matching keys via SCAN; `get`/`set`/`delete`/`get_quota` round-trip |
| `tests/unit/repositories/test_thread_repo.py` (extend) | `find_expired` returns only threads older than cutoff, excludes `deleting` |
| `tests/unit/repositories/test_image_repo.py` | `delete_by_thread` cascade; `list_by_message_id` filters correctly; `count_by_user_date` returns count for that UTC day |
| `tests/integration/test_lifespan.py` | Startup completes; `s3.ensure_bucket` called; `app.state.openai` initialised with correct API key; slowapi + fastapi-cache2 globals populated; shutdown closes all 5 clients in correct order (openai → s3 → cache_redis → valkey → qdrant → pools) |
| `tests/integration/test_rate_limit.py` | 21st `POST /runs/stream` returns 429 within 1 minute; webhook + health exempt; per-user isolation; `Retry-After` header present |
| `tests/integration/test_thread_list_cache.py` | First call hits the repo (cache miss), second returns from Valkey; `delete_pattern('threads:{user_id}:*')` invalidates; per-user key isolation; TTL expiry repopulates |

---

## Phase 6 — RAG Ingestion — DONE

Status:  `ProductIndexer` real implementation (hybrid Qdrant
upsert + fastembed BM25 + OpenAI dense embeddings, idempotent via
`TextNode.id_ = product.product_id`), two-task Celery design
(`run_ingestion_job` orchestrator on queue `reindex`,
`process_batch` worker on queue `reindex_batches`), PostgreSQL
job/batch tracking with full state machines, admin reindex
endpoints (`POST /admin/reindex` + `GET /admin/reindex/{job_id}`).
End-to-end smoke test: admin token → 202 → orchestrator fetches
32 products → worker dispatches → all 32 embedded and upserted
to Qdrant (`points_count=32, status=green`) with zero
`skipped_products`.  `src/app/rag/retriever.py` deleted.
`fastembed>=0.7,<1.0` pinned in `pyproject.toml`.  298 tests
passing, ruff + pyright clean, overall coverage 85%.  See
`temp/phase-6-rag-ingestion.md` for the full implementation log
and `history/6_0_0_RAG_INGESTION.md` for the decision record.

### Objective

Implement the full Saleor → LlamaIndex → Qdrant ingestion pipeline
and the corresponding Celery `reindex_products` task.  The
`ProductRetriever` stub in `src/app/rag/retriever.py` is **deleted**
because the Phase 4 `product_rag` subagent is the single source of
truth for retrieval (per decision locked during the Phase 4 review).

### Tasks

#### 6.1 `ProductIndexer.reindex_all` (`src/app/rag/indexer.py`)

- `SaleorClient.fetch_all_products()` (cursor-paginated) →
  `ProductPayload` list.
- For each product: build `TextNode` per the DRAFT 0.6 metadata
  schema, run `IngestionPipeline(transformations=[OpenAIEmbedding(...)], vector_store=...)`.
- Return `{"products_indexed": count, "duration_seconds": elapsed}`.

#### 6.2 `ProductIndexer.upsert_product` (single product)

- Build a single `TextNode` with `id_=product_id` for idempotent
  Qdrant upserts (FR-080).
- Run a one-shot `IngestionPipeline.arun(nodes=[text_node])` against
  the shared `QdrantVectorStore(enable_hybrid=True, ...)`.
- Log a structured `product_upserted` event.

#### 6.3 `ProductIndexer.delete_product`

- `QdrantService.client.delete(collection_name=..., points_selector=Filter(must=[FieldCondition(key="product_id", match=MatchValue(value=product_id))]))`.
- Log `product_deleted` with the `product_id`.

#### 6.4 `reindex_products` Celery task real (`src/app/tasks/reindex_products.py`)

- Replace stub with `asyncio.run(ProductIndexer(settings).reindex_all())`.
- Keep the existing retry / timeout configuration (2 retries, 60s
  backoff, 1-hour `time_limit`).
- Return the indexer's summary dict.

#### 6.5 Delete `src/app/rag/retriever.py`

- The class is unused (Phase 4 `product_rag/nodes.py::hybrid_search_node`
  is the only caller path).  Delete the file and any orphan
  `tests/unit/test_retriever.py`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/test_indexer.py` | `reindex_all` returns count; idempotent upsert on duplicate `product_id`; delete removes by `product_id`; description is summarised when over `description_max_chars` |
| `tests/unit/tasks/test_reindex_products.py` | Celery task delegates to `ProductIndexer.reindex_all`; result is the indexer summary dict |
| `tests/integration/test_qdrant.py` (extend) | Round-trip: index one product → search by name → product is in results |

---

## Phase 7 — Webhook handling — DONE

### Objective

Wire the Saleor webhook receiver to the Celery pipeline so that
`PRODUCT_CREATED` / `PRODUCT_UPDATED` / `PRODUCT_DELETED` events
correctly mutate the Qdrant collection asynchronously (FR-076 to
FR-080, NFR-013 idempotency).

### Tasks

#### 7.1 `api/webhooks.py` real handler

- HMAC verification already in place — keep it.
- Parse the JSON body to extract `event_type` and the product
  payload (top-level fields depend on Saleor — see
  `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md` for the
  expected shape).
- Enqueue: `process_webhook.delay(event_type, product_id, product_data)`.
- Return `{"status": "accepted", "event_type": event_type}` (still
  under 200 ms per NFR-003).

#### 7.2 `process_webhook` Celery task real

- Dispatch on `event_type`:
  - `PRODUCT_CREATED` / `PRODUCT_UPDATED` →
    `ProductIndexer.upsert_product(product_data)`.
    Idempotent: re-processing the same `product_id` overwrites
    (NFR-013).
  - `PRODUCT_DELETED` →
    `ProductIndexer.delete_product(product_id)`.
- Use `asyncio.run(...)` to bridge to the async indexer.
- Log at INFO with `product_id`, `event_type`, and processing
  duration (NFR-024).
- Return `{product_id, event_type, status}`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/tasks/test_process_webhook.py` | `PRODUCT_CREATED` calls `upsert_product`; `PRODUCT_UPDATED` calls `upsert_product`; `PRODUCT_DELETED` calls `delete_product`; unknown event type is safely ignored |
| `tests/integration/test_webhook_dispatch.py` | Valid HMAC → 200, Celery task enqueued with correct args; invalid HMAC → 401; missing header → 401; malformed body → 400 |

---

## Phase 8 — Thread Management API — DONE

**Result**: 379 tests passing, 88% coverage, full live-stack E2E.
See `temp/phase-8-thread-api.md` for the full report.

## Phase 8 — Thread Management API (spec)

### Objective

Replace the 5 stubs in `api/threads.py` with real implementations:
thread create, list, get, history (with image attachment), and
async delete.  All endpoints must respect the thread state machine
(idle ↔ busy ↔ deleting) and invalidate the thread-list cache on
any mutation.

### Tasks

#### 8.1 `POST /api/v1/threads`

- Build `ThreadRepository`, call `create(user_id)`.
- Invalidate cache pattern `threads:{user_id}:*` (FR-098).
- Return `201 ThreadResponse`.

#### 8.2 `GET /api/v1/threads` (cursor paginated, FR-015)

- Decorate with `@cache(expire=settings.thread_list_cache_ttl)` and
  `@limiter.limit(settings.rate_limit_read)`.
- Build cache key `threads:{user_id}:{before}:{limit}`.
- `ThreadRepository.list_by_user(user_id, before, limit)`.
- Return `ThreadListResponse` with `next_cursor` set to the last
  item's id when the page is full (else `None`).

#### 8.3 `GET /api/v1/threads/{thread_id}` (FR-016)

- `ThreadRepository.get(thread_id, user_id)`.
- `404` if not found, `410 Gone` if `status == "deleting"`.
- Return `ThreadResponse`.

#### 8.4 `GET /api/v1/threads/{thread_id}/history` (FR-019, FR-020)

- Read messages from `AsyncPostgresSaver` checkpoint (the same
  checkpointer used by the graph in `lifespan`, injected via
  `app.state.checkpointer`).
- Cursor pagination via `?before={message_id}&limit=N`.
- For each `HumanMessage`, look up images via
  `ImageRepository.list_by_message_id(human_msg.id)` and attach
  them to the next `AIMessage` in the sequence.
- Return the messages with attached images.

#### 8.5 `DELETE /api/v1/threads/{thread_id}` (FR-017)

- Verify ownership (`ThreadRepository.get(thread_id, user_id)`).
- `set_status(thread_id, "deleting")` (atomic transition).
- `delete_thread.delay(thread_id, user_id)`.
- Invalidate cache pattern.
- Return `202 Accepted` with `{"status": "deleting"}`.

#### 8.6 `POST /api/v1/threads/{thread_id}/runs/stream`

- Implemented in Phase 14 (DONE): full SSE endpoint with status
  guards, atomic busy/idle transition, Path A shared-resource
  injection, and 7 event types (`token`, `products`, `image_ready`,
  `image_failed`, `thread_title`, `done`, `error`).

#### 8.7 Cache invalidation helper

```python
async def invalidate_thread_list_cache(valkey: ValkeyService, user_id: str) -> int:
    """Delete all cached pages for this user's thread list (FR-098)."""
    # Pattern MUST include the namespace prefix used by
    # thread_list_key_builder — full key is
    # `{_THREAD_LIST_CACHE_NAMESPACE}:threads:{user_id}:{before}:{limit}`.
    pattern = f"{_THREAD_LIST_CACHE_NAMESPACE}:threads:{user_id}:*"
    return await valkey.delete_pattern(pattern)
```

Used by `POST /threads` (8.1) and `DELETE /threads/{id}` (8.5).

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/api/test_threads.py` | Each endpoint happy path; 404 on missing thread; 410 on `deleting`; cache invalidation called on mutation; cursor pagination returns `next_cursor` correctly; history attaches images by `request_message_id` |
| `tests/integration/test_threads.py` | End-to-end: create → list → get → history (mocked checkpointer) → delete → Celery enqueued; rate limit 429 after threshold |

---

## Phase 9 — Profile + Admin API — DONE

**Result**: 419 tests passing (+40 from Phase 8 baseline of 379, net of
-3 cleanup removals from `tests/integration/test_threads.py`),
89.21% coverage, ruff + pyright + pre-commit all clean.  See
`temp/phase-9-profile-admin-api.md` for the full report and
`history/9_0_0_PROFILE_AND_ADMIN_API.md` for the full ADR
(D9.1–D9.8, D9.5', D9.6').

### Divergences from the original Phase 9 spec

The original spec called for 3 endpoints.  Phase 9 ships 4 — the
extra one (`GET /api/v1/admin/reindex`) was added during
implementation to close a REST gap left by Phase 6: the
`POST /admin/reindex` + `GET /admin/reindex/{job_id}` pair shipped
together, but the collection endpoint (no `job_id`) was never built.
Operators had no way to list all jobs without hitting the DB
directly, so the listing endpoint was promoted from
"nice-to-have" to "required" during Phase 9 review and is now part
of the contract.

A `batches[]` array is **deliberately omitted** from the listing
response (D9.6') — drill into `GET /admin/reindex/{job_id}` for
batch-level detail.  Including it in the list payload would
quadruple the response size for no operational benefit.

### Bugs found and fixed (by writing the tests)

1. `ThreadResponse.model_validate(r)` failed against the raw
   asyncpg `Record` with `ValidationError: Input should be a valid
   dictionary or instance of ThreadResponse`.  Fix: pass
   `from_attributes=True` so Pydantic reads from the record's
   attribute access.
2. `IngestionJobSummary` field alias was reversed — it was declared
   as `job_id: uuid.UUID = Field(alias="id")`, so
   `model_dump(by_alias=True)` produced `id` in JSON, not `job_id`.
   Fix: flip to `id: uuid.UUID = Field(alias="job_id")` and add
   `response_model_by_alias=True` to the `/admin/reindex` route.

### Cleanup: 3 flaky integration tests removed

The shared dev DB / Valkey / LangGraph checkpointer is shared
across all test runs, which made these 3 contracts untestable in
the integration suite without dedicated infrastructure.  Each
contract is fully covered by an isolated unit test in
`tests/unit/api/test_threads.py`:

| Removed test | Root cause | Unit test covering the contract |
|---|---|---|
| `test_list_threads_includes_newly_created_thread` | 50+ leftover threads; `limit=20` paginated the new one off page 1; cache race | `test_create_thread_returns_201_and_invalidates_cache` |
| `test_double_delete_returns_410` | Celery `soft_delete_thread` worker raced with the 2nd DELETE | `test_delete_thread_returns_410_for_already_deleting` |
| `test_history_returns_empty_for_brand_new_thread` | `AsyncPostgresStore` shared across runs | `test_history_returns_empty_when_graph_has_no_state` |

Tracked follow-up: `docker-compose.test.yml` with dedicated
Postgres + Valkey + checkpointer so each run starts clean.

---

## Phase 9 — Profile + Admin API (spec)

### Objective

Replace the 3 stubs in `api/profile.py` and `api/admin.py` with
real implementations backed by `AsyncPostgresStore`,
`ThreadRepository`, and the Celery `reindex_products` task.  Add
the missing `GET /api/v1/admin/reindex` collection endpoint to
close the Phase 6 REST gap.

### Tasks

#### 9.1 `GET /api/v1/users/{user_id}/profile` (admin only, FR-032)

- `AdminDep` guard.
- `await store.aget(("profiles", user_id), "profile")`.
- `404` if the key does not exist, `500` if the stored payload
  fails Pydantic validation.
- Return the envelope `{profile, updated_at}` (D9.3) so operators
  can see how fresh the displayed data is — the store populates
  `Item.updated_at` on every `aput`.
- NOT cached (D9.7) — operator visibility demands fresh data.

#### 9.2 `POST /api/v1/admin/reindex` (FR-103, Phase 6)

- `AdminDep` guard.
- `reindex_products.delay()`.
- Return `202 Accepted` with `{"status": "queued"}`.

#### 9.3 `GET /api/v1/admin/threads` (FR-104)

- `AdminDep` guard.
- `ThreadRepository.list_all(before, limit)` (new method on the
  repo — same shape as `list_by_user` but no `user_id` filter).
- Return `ThreadListResponse`.

#### 9.4 `GET /api/v1/admin/reindex` (NEW — closes Phase 6 REST gap)

- `AdminDep` guard.
- `IngestionJobRepository.list_all(before, limit)` (new method on
  the repo — cursor-paginated, sorts by
  `COALESCE(started_at, 'infinity'::timestamptz) DESC` so pending
  jobs — with no `started_at` — sort to the top of the list
  (D9.5')).
- Return `IngestionJobListResponse` with strict `job_id` field
  rename in JSON (`Field(alias="job_id")` +
  `response_model_by_alias=True` on the route, D9.6') so the wire
  format is decoupled from the internal `IngestionJob.id` column.
- `batches[]` is **omitted** from the list payload (D9.6') — drill
  into `GET /admin/reindex/{job_id}` for batch-level detail.
- Query params: `limit` (1-100, default 20), `before` (cursor UUID).
- Rate limit: 60/min (D9.8).

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/api/test_profile.py` | Admin allowed reads profile from store; non-admin gets 403; missing profile returns 404; corrupt payload returns 500; partial profile validates with defaults; envelope shape `{profile, updated_at}`; namespace + key flow through to `store.aget` |
| `tests/unit/api/test_admin.py` | `POST /reindex` enqueues Celery task with no args; `GET /admin/threads` paginates across all users; `GET /admin/reindex` paginates with `job_id` alias and no `batches[]`; non-admin gets 403 on all three |
| `tests/unit/repositories/test_thread_repo.py` | `list_all` SQL contract — no `user_id` filter, `ORDER BY updated_at DESC, id DESC`, cursor resolution, unknown cursor short-circuits to `[]` |
| `tests/unit/repositories/test_ingestion_repo.py` | `list_all` SQL contract — `COALESCE(started_at, 'infinity'::timestamptz)` so pending jobs sort first, cursor resolution, unknown cursor short-circuits to `[]` |
| `tests/integration/test_admin.py` | End-to-end: admin token from JWT → 200 on threads list, 200 on reindex list (with `job_id` alias + no `batches[]`), 403 on non-admin, 401 on no JWT, unknown cursor returns empty |

---

## Phase 10 — Cleanup Celery tasks — DONE

**Result**: 441 tests passing (+18 from Phase 9 baseline of 423),
3 skipped when stack offline, 90.57% coverage (Phase 10 modules
`tasks/cleanup_expired_threads` and `tasks/delete_thread` at 100%).
See `temp/phase-10-cleanup-tasks.md` for the full write-up and
`history/10_0_0_CLEANUP_TASKS.md` for the decision record
(D10.1, D10.2, D10.3, D10.4, D10.5).

### Divergences from the original Phase 10 spec

- **Configurable expiry window** — the spec said "30 days"; we made
  it `Settings.thread_expiry_days` (default 30, FR-018) so dev
  environments can shorten the window via `THREAD_EXPIRY_DAYS` in
  `.env` / `.env.example`.  Two new unit tests cover both the
  default-cutoff and the custom-cutoff paths.
- **Sweep path uses `delete_by_id`, not `delete`** — the original
  spec only mentioned `ThreadRepository.delete(thread_id, user_id)`,
  but the sweep has no user context, so a new
  `ThreadRepository.delete_by_id(thread_id)` (no `user_id`
  predicate, D10.4) was added.  User-facing deletion still goes
  through `delete` and retains its owner check.
- **Integration tests call `_process` directly** — the Celery
  wrapper uses `asyncio.run()` which is forbidden inside pytest-
  asyncio's running loop.  The integration tests therefore call
  the inner `_process` coroutine directly and patch the pool/
  S3 dependencies.  The Celery wrapper itself (asyncio.run +
  try/except + retry) is fully covered by the unit tests.

### Objective

Implement the two cleanup Celery tasks that must run during
thread deletion and during the nightly 30-day expiry sweep.  Both
must follow the atomic order defined by NFR-015: S3 objects first,
then image records, then the thread row.

### Tasks

#### 10.1 `delete_thread` task real

- `ImageRepository.list_by_thread(thread_id)` → for each image,
  call `S3Service.delete(image.s3_key)`.
- `ImageRepository.delete_by_thread(thread_id)` (count returned).
- `ThreadRepository.delete(thread_id, user_id)`.
- Wrap the chain in a single `asyncio.run(...)` block.
- Return `{thread_id, images_deleted, status: "deleted"}`.

#### 10.2 `cleanup_expired_threads` task real

- `ThreadRepository.find_expired(now() - 30 days)`.
- For each expired id, run the same logic as 10.1.
- Skip threads already in `status="deleting"` (idempotency guard
  in case the periodic job races with an explicit DELETE).
- Return `{threads_deleted, images_deleted, duration_seconds}`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/tasks/test_delete_thread.py` | Order is S3 → images → thread; partial S3 failure rolls back via Celery retry; missing images does not abort |
| `tests/unit/tasks/test_cleanup_expired_threads.py` | Picks up only threads older than 30 days; skips `deleting`; aggregates counts correctly |
| `tests/integration/test_cleanup.py` | Insert expired thread with images → run task → S3 objects gone, image records gone, thread row gone |

---

## Phase 11 — Trend Scout — DONE

**Result**: 461 tests passing (+20 from Phase 10 baseline of 441),
3 skipped when stack offline, ruff + pyright clean across the new
TrendScout package and its tests. See `temp/phase-11-trend-scout.md`
for the full write-up and `history/11_0_0_TREND_SCOUT.md` for the
decision record (D11.1 - D11.13).

### Divergences from the original Phase 11 spec

- **`create_agent` replaces `create_react_agent`** (D11.1) — the
  original spec named the deprecated `langgraph.prebuilt.create_react_agent`
  factory. We use the new `langchain.agents.create_agent` (added in
  `langchain>=1.1`) which is the supported entry point going forward.
- **Single exposed tool + private DDG fallback** (D11.12) — the
  spec called for two `@tool`-decorated functions; we expose
  `tavily_search` only and keep `duckduckgo_search` as a plain
  module-level function. The LLM never picks the fallback — the
  tool body cascades internally — so the schema stays clean and
  the model never wastes a round-trip.
- **`SummarizationMiddleware` for long threads** (D11.13) — the
  original spec said "prepend the SystemMessage to the last 4
  messages"; we instead attach a `SummarizationMiddleware` whose
  trigger is `("tokens", int(0.8 * model.profile["max_input_tokens"]))`
  and `keep=("messages", 20)`. Bounded history is more robust than
  a fixed 4-message window.
- **No separate `trend_scout_model`** (D11.8) — the spec and an
  earlier draft of the D11 record both proposed a dedicated
  `trend_scout_model` setting; the user confirmed we reuse
  `settings.orchestrator_model`. The subagent is research-only
  (no structured output shaping that needs a different model), so
  one model name is enough.
- **Config default aligned to `gpt-5.4-mini`** (D11.11) — the
  pre-Phase-11 default of `gpt-4o-mini` for `orchestrator_model`
  did not match the project's `gpt-5.4-mini` default for the
  other LLM model settings (`rerank_model`, `summarize_model`).
  Phase 11 aligns the default.
- **`api_key` passed via callable, not `os.environ`** — early
  drafts mirrored `OPENAI_API_KEY` / `TAVILY_API_KEY` to
  `os.environ` at import time. That polluted the test process and
  broke `test_settings_missing_required_field`, so we pass the
  values explicitly: `ChatOpenAI(api_key=lambda: settings.openai_api_key)`
  and `TavilySearchResults(tavily_api_key=settings.tavily_api_key)`.
  `DuckDuckGoSearchRun` construction is wrapped in a defensive
  `try/except` because the project's pinned `duckduckgo-search==8.1.1`
  does not match `langchain_community==0.4.2`'s expected `ddgs`
  package; the follow-up is tracked as a `pyproject.toml` update.

### Objective

Implement `run_trend_scout` as a real LangChain `create_agent`
with a Tavily (primary) + DuckDuckGo (fallback) search stack and a
`SummarizationMiddleware` for bounded message history. Output is
a concise trend summary + an optional DALL-E prompt.

### Tasks

#### 11.1 State and schema

- `TrendScoutState` extends `langchain.agents.AgentState` (D11.4)
  with `generate_image: bool`.
- `TrendScoutOutput` Pydantic model (D11.5): `trend_summary: str | None`,
  `image_prompt: str | None` — both fields nullable so a single
  schema covers both a successful research report and the
  graceful-degradation "no synthesis" outcome (D11.6).

#### 11.2 Tools (`subagents/trend_scout/tools.py`)

- `TavilySearchResults(max_results=5, tavily_api_key=settings.tavily_api_key)`
  — primary backend (FR-042).
- Plain module-level `duckduckgo_search(query, max_results=5)` helper
  (D11.12) — not a `@tool`. Called only from inside `tavily_search`
  when Tavily raises (FR-043). The tool body cascades the fallback
  so the LLM never sees two tools to choose between.
- `tavily_search` is exposed as the only `@tool` to the LLM.
- `DuckDuckGoSearchRun` construction is defensive: when the `ddgs`
  package is missing the client is set to `None` and
  `duckduckgo_search` raises `RuntimeError` (D11.6 graceful
  degradation contract).

#### 11.3 `_build_trend_scout_system` helper

- Base prompt from `trend_scout_system.md`.
- Append `## Conversation history summary` (if `state["summary"]`
  non-empty), `## User preferences` (if profile non-empty),
  `## Products already recommended` (if `retrieved_products`
  non-empty), and `## Output note` (if `generate_image=True`).
- Sections are appended in that fixed order, separated by blank
  lines, and only when the corresponding field is non-empty.

#### 11.4 Agent + wrapper

- `_build_trend_scout_graph()` constructs a
  `create_agent(model=ChatOpenAI(model=settings.orchestrator_model, api_key=lambda: settings.openai_api_key), tools=[tavily_search], state_schema=TrendScoutState, response_format=TrendScoutOutput, middleware=[SummarizationMiddleware(model=ChatOpenAI(...), trigger=("tokens", int(0.8 * model.profile["max_input_tokens"])), keep=("messages", 20))])`
  subgraph (D11.1, D11.7, D11.8, D11.12, D11.13).
- `run_trend_scout(state, config)` (D11.3, D11.9) builds the dynamic
  `SystemMessage`, prepends it to the full `state["messages"]` (no
  last-N truncation -- summarization handles that), and forwards
  `correlation_id` via `config["metadata"]` for LangSmith traces.
- Subgraph is compiled once at import time as
  `_TREND_SCOUT_GRAPH` and reused across calls (D11.7). The
  parent's `AsyncPostgresSaver` handles thread-level checkpoints.
- Fallback (D11.6): if the subgraph raises OR returns no
  `structured_response`, the wrapper returns
  `{"trend_summary": None, "image_prompt": None}` so the
  orchestrator can route to `synthesize` with a clean partial
  state update.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/subagents/test_trend_scout_tools.py` | `tavily_search` returns parsed `list[dict]` on success; falls back to `duckduckgo_search` on Tavily exception; `duckduckgo_search` parses DDG string output to `list[dict]`; raises `RuntimeError` when DDG itself fails; `tavily_search` is a `@tool` with name+description; D11.12 contract: `duckduckgo_search` is a plain function (no `.name` attribute) |
| `tests/unit/agent/subagents/test_trend_scout_system.py` | All-empty inputs return just the base prompt; non-empty `summary` adds the summary section; non-None `user_profile` adds the JSON section; non-empty `retrieved_products` adds the products section with fallback to `product_id` when `name` is missing; `generate_image=True` adds the image-output note |
| `tests/unit/agent/subagents/test_trend_scout_wrapper.py` | Successful invocation projects `structured_response` to `trend_summary` and `image_prompt`; the SystemMessage is injected as `messages[0]`; `generate_image` flows into the sub-state; `correlation_id` is attached to `config['metadata']`; parent config is merged when provided; subgraph-raises -> `{None, None}` (D11.6); missing-or-wrong-type `structured_response` -> `{None, None}`; D11.12 + D11.13 build-time contract: only `tavily_search` in `tools` and a `SummarizationMiddleware` with `("tokens", N)` trigger |

---

## Phase 12 — Synthesize + Title Generation — DONE

### Objective

Implement the two terminal nodes of the main pipeline:
`ResponseGeneratorNode` (streams the user-facing reply) and
`TitleGenerationNode` (auto-names the thread on the first run).

Both nodes must follow the 4-prompt-variant design for synthesize
(intentional per design — `sufficient` / `clarification_needed` /
`out_of_scope` / `fallback`) and the LLM-with-truncation-fallback
design for title generation.

### Tasks

#### 12.1 `nodes/synthesize.py` real

- Read SSE queue from `config["configurable"]["sse_queue"]`.
- Dispatch to one of 4 prompts based on
  `state["intent"]`:
  - `sufficient` → `synthesize_sufficient_system.md`
  - `clarification_needed` → `synthesize_clarification_system.md`
  - `out_of_scope` → `synthesize_out_of_scope_system.md`
  - `fallback` (or default) → `synthesize_fallback_system.md`
- Inject the standard context sections (user profile, retrieved
  products, trend summary, summary) into the system message.
- Stream via `ChatOpenAI.astream`, emit `token` events.
- After streaming completes, emit a `products` event if
  `retrieved_products` is non-empty.
- Emit a `done` event with token usage and the resolved `intent`.

#### 12.2 `nodes/generate_title.py` real

- Guard: if `state["title_generated"] == True`, return `{}`.
- Read `state["first_user_message"]`.
- If `thread_repo.increment_title_attempts(thread_id) <
  settings.title_generation_max_attempts`:
  - Call `TITLE_MODEL` with `title_system.md` prompt.
  - On success: `thread_repo.update_title(...)`, invalidate
    `threads:{user_id}:*` cache, emit `thread_title` SSE.
  - On failure: return `{}` (retry next run).
- Else (attempts exhausted): use truncation fallback
  `first_user_message[:settings.title_truncation_length]`,
  persist, emit `thread_title` SSE.

#### 12.3 Wire nodes into the graph

- The current `graph.py` already imports both node functions; the
  conditional edges and parallel branches are already in place.
  This phase is purely about replacing the stub bodies with the
  real implementations above.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_synthesize.py` | Dispatches to the correct prompt for each of the 4 intents; streams tokens to the SSE queue; emits `products` after stream when products present; emits `done` with usage at end |
| `tests/unit/agent/nodes/test_generate_title.py` | Returns `{}` when `title_generated=True`; LLM success path persists + invalidates cache + emits SSE; LLM failure path increments attempts; truncation fallback used when attempts >= max |

---

## Phase 13 — Image Generation — DONE

### Objective

Implement `ImageGenerationNode` as a parallel branch from the
orchestrator (per DRAFT 0.6 Section 2.5 and
`docs/diagrams/05-agent-workflow.mermaid`).  The node generates an
image with DALL-E, uploads to S3, records quota + DB row, and
emits the corresponding SSE events.

### Tasks

#### 13.1 Trigger guards

- Skip if `state.get("generate_image") != True`.
- Skip if `state.get("image_prompt") is None`.
- Skip if Valkey daily quota exceeded:
  `count = valkey.get_quota(f"image_quota:{user_id}:{date}")` →
  emit `image_failed {reason: "rate_limit_exceeded"}` and return.

#### 13.2 DALL-E call

- `AsyncOpenAI(api_key=settings.openai_api_key).images.generate(prompt=..., n=1, size="1024x1024")`.
- Download the bytes from the returned URL.

#### 13.3 S3 upload + DB row

- `S3Service.upload_image(user_id, thread_id, timestamp, image_bytes)`
  → returns the public URL.
- `ImageRepository.create(thread_id, user_id, prompt, s3_key, s3_url, "dall-e-3", request_message_id=human_msg.id)`.

#### 13.4 Quota increment

- `valkey.increment_quota(f"image_quota:{user_id}:{date}", ttl=86400)`.

#### 13.5 SSE emission

- `image_ready {url: s3_url, prompt: prompt}` on success.
- `image_failed {reason: "generation_failed"}` on any DALL-E or
  S3 error.

#### 13.6 Return shape

- `{"image_url": s3_url, "image_prompt": prompt}` on success.
- `{}` on quota-exceeded or failure.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_generate_image.py` | Returns `{}` when `generate_image=False`; quota exceeded emits `image_failed` + returns `{}`; DALL-E success uploads to S3 with `build_key` pattern, inserts `generated_images` row, increments quota, emits `image_ready`; DALL-E failure emits `image_failed {reason: "generation_failed"}` |
| `tests/unit/services/test_s3_service.py` (extend) | `delete` calls `s3.delete_object`; `build_key` returns spec pattern |

---

## Phase 12 + 13 — combined (Synthesize + Title + Image)

The two terminal main-pipeline nodes (Phase 12) and the parallel
image-generation branch (Phase 13) ship together because they all
sit on the same `asyncio.Queue`-based SSE event-bus contract
defined in `app/agent/nodes/_sse.py`.  The shared
`emit_sse(queue, event_type, payload)` helper is the single
emission point; the typed payloads (`ProductsPayload`,
`ThreadTitlePayload`, `ImageReadyPayload`, `ImageFailedPayload`)
live in `app/schemas/chat.py` so the wire contract has one
source of truth.

Key contracts locked in these phases:

- **D12.1–D12.7** (synthesize): intent-to-prompt dispatch, free-form
  streaming, products / done events, no Pydantic output parser.
- **D12.8–D12.11** (generate_title): LLM with truncation fallback
  after `settings.title_generation_max_attempts` (default 3),
  best-effort Valkey cache invalidation, `thread_title` SSE event.
- **D13.1–D13.10** (generate_image): DALL-E `b64_json` format,
  S3 upload, `generated_images` DB row, Valkey daily quota
  (`image_quota:{user_id}:{date}`, 24h TTL), `image_ready` /
  `image_failed` events.
- **DI.X1–DI.X3** (resource injection): `sse_queue` plus
  `openai_client` / `s3_service` / `valkey_service` are threaded
  via `config["configurable"]`; in production the chat handler
  pulls from `app.state`; in tests the fixture injects mocks.
- **F8.1–F8.5** (history image-attach fix, Commit 0): the API
  handler walks the page in order, tracking the most recent
  HumanMessage id, and attaches its images to the following
  AIMessage.

The full decision record is at
[`history/12_13_0_SYNTHESIZE_TITLE_IMAGE.md`](../history/12_13_0_SYNTHESIZE_TITLE_IMAGE.md);
the implementation log is at
[`temp/phase-12-13-synthesize-title-image.md`](../temp/phase-12-13-synthesize-title-image.md).

---

## Phase 14 — Chat SSE endpoint [DONE]

### Objective

Replace the `api/chat.py` 501 stub with a real Server-Sent Events
endpoint that drives the LangGraph graph in the background, streams
7 event types to the client, and manages the thread busy/idle
state machine.

This phase **activates Phase 4 Path A** by injecting the shared
`AsyncQdrantClient` into the graph config, eliminating the
per-request handshake.

### Tasks

#### 14.1 Status guards

- `ThreadRepository.get(thread_id, user_id)` → 404 if missing,
  410 if `status == "deleting"`, 409 if `status == "busy"`.
- Set `status = "busy"` atomically (single UPDATE with WHERE
  clause `status = 'idle'`) before starting the graph task.
- In a `finally` block: set `status = "idle"` and `touch(thread_id)`
  so the 30-day expiry window resets.

#### 14.2 Shared-resource injection

Build `config["configurable"]` with:

```python
config = {
    "configurable": {
        "thread_id": str(thread_id),
        "user_id": user_id,
        "correlation_id": str(uuid.uuid4()),  # per-request trace ID
        "sse_queue": sse_queue,
        "qdrant_aclient": request.app.state.qdrant.client,  # Path A activation
    }
}
```

`qdrant_aclient` is the Phase 4 carry-over — without it, every
request would create and close a transient client.  Production
verification: zero `"transient AsyncQdrantClient created"` log
lines per request.

#### 14.3 Background task + SSE streaming

- `asyncio.create_task(graph.ainvoke(initial_state, config))`.
- Catch exceptions inside the task and push an `error` SSE event
  with `{code, message}` onto the queue.
- Always push a `None` sentinel on completion so the consumer can
  break out.
- The consumer generator reads from the queue, formats each item
  as `event: <name>\ndata: <json>\n\n`, and yields it.
- `StreamingResponse(event_generator(), media_type="text/event-stream")`.

#### 14.4 Initial state construction

```python
initial_state = {
    "messages": [HumanMessage(content=body.message)],
    "user_id": user_id,
    "thread_id": str(thread_id),
    "correlation_id": config["configurable"]["correlation_id"],
    "generate_image": body.generate_image,
    "first_user_message": body.message,
}
```

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/api/test_chat.py` | Returns 409 when thread is busy; 410 when deleting; 404 when missing; 200 on idle; graph exception → `error` SSE event; thread status reset to `idle` in `finally`; `qdrant_aclient` injected into `config["configurable"]`; `correlation_id` is a new UUID4 per request |
| `tests/integration/test_chat_sse.py` | End-to-end with a mocked graph: POST to `/runs/stream` → receive `token` events → receive `products` event → receive `done` event; response is `text/event-stream` |

---

## Cross-Cutting Concerns

### Observability (applies from Phase 5 onwards)

- Prometheus `/metrics` is already mounted (Phase 1) and
  `prometheus-fastapi-instrumentator` provides zero-config HTTP
  histograms, request counts, and error rates (FR-109).
- OpenInference LlamaIndex instrumentation is already active
  (Phase 1) — every retrieval/embedding call inside the
  `product_rag` subgraph emits OTel spans to LangSmith (FR-108).
- structlog JSON logs to stdout — Promtail scrape target is
  configured in `docker-compose.yml` (FR-112).
- Every agent node must call
  `structlog.contextvars.bind_contextvars(correlation_id=..., node=...)`
  at the top of its body (FR-067, FR-111).

### Error Handling

- LLM calls use LangGraph's native `RetryPolicy(max_attempts=3)`
  + `TimeoutPolicy(60s/30s)` on each node (NFR-010).
- Webhook task retries with exponential backoff (1s → 10s, max
  3 retries).
- All asyncpg / boto3 / httpx errors are caught at the repository
  / service layer; nodes never wrap them in broad `except Exception`.

### Prompt Safety

- User message content is sanitised (truncated to 4096 chars, null
  bytes stripped) before being injected into any LLM prompt
  (FR-088).  The `ChatRequest.message` Pydantic field enforces the
  4096-char cap at the API boundary.

### Infrastructure Ownership

**Hard rule: application code MUST NOT execute or orchestrate
Terraform, CloudFormation, or any other infrastructure-as-code
tool.**  Terraform is run **manually by operators** (or by the
CI/CD pipeline that deploys infra — but never from the FastAPI
process or any Celery worker) **before** the application starts.
The app's job at startup is purely to verify and connect.

Failure modes are loud and operator-visible:

- S3 bucket missing → `S3Service.ensure_bucket` raises a
  `BucketNotFound`-style error during `lifespan` startup.  The
  pod fails its readiness probe and never accepts traffic.  The
  operator runs `terraform apply` for the S3 stack, then
  re-deploys / restarts the pod.  **The app never runs
  `terraform apply` itself.**
- Qdrant cluster unreachable → readiness probe fails with the
  Qdrant ping error.  Operator checks network / credentials /
  Terraform state.
- PostgreSQL unreachable → same shape via the `psycopg_pool`
  connection error.

The repository of truth for cloud infrastructure state is
Terraform; for schema state, Alembic.  The application only ever
reads from and writes to resources those tools have already
provisioned.

Specifics per resource:

- For S3: `S3Service.ensure_bucket` performs `head_bucket` only
  and raises on missing.  No `create_bucket` call exists in
  application code.  The bucket, its policy, IAM role, and CORS
  configuration are all owned by Terraform.
- For Qdrant (the local Docker container only): the existing
  `QdrantService.ensure_collection` does create the collection on
  first startup because the Qdrant service in `docker-compose.yml`
  is itself created by `docker compose up` (i.e. infra-owned), and
  the collection schema is small, deterministic, and idempotent.
  This exception is explicit: it applies only to the local Qdrant
  container, not to any managed Qdrant cluster.  A managed
  deployment must use Terraform to pre-create the collection.
- For PostgreSQL: schema is owned by Alembic migrations, run via
  `alembic upgrade head` as a deploy step.  Application code must
  never run `CREATE TABLE` outside migrations.

### Dependencies

The following libraries are already pinned in `pyproject.toml` and
will be exercised by these phases:

- `slowapi==0.1.9` (Phase 5, 8, 9)
- `fastapi-cache2==0.2.2` (Phase 5, 8)
- `prometheus-fastapi-instrumentator==8.0.0` (Phase 1, mounted)
- `openinference-instrumentation-llama-index==4.4.2` (Phase 1, mounted)
- `tavily-python` (Phase 11, via `langchain-community.tavily_search`)
- `langchain-community` (Phase 11, for `DuckDuckGoSearchRun`)
- `fastembed` (Phase 4, 6, 7 — pulled by `qdrant-client[fastembed]`)
- **D11.10 follow-up**: add `ddgs` to `pyproject.toml` so the
  `DuckDuckGoSearchRun` defensive `try/except` can be removed.

---

## Test Coverage Requirements

- All source code in `src/` must maintain >= 80% coverage (NFR-029).
- Run after each phase: `uv run pytest --cov=src --cov-report=term-missing`.
- Each agent node must be testable in isolation with mocked LLM
  responses (NFR-030).
- Repository and service unit tests must use the in-memory
  `pytest-asyncio` pattern with mocked clients — no real PostgreSQL
  / Qdrant / Saleor / S3 / Valkey in unit tests.
- Integration tests may use real service containers via the
  `docker-compose.test.yml` profile.

---

## Definition of Done (per phase)

A phase is considered complete when:

1. All listed tasks are implemented.
2. All listed tests pass.
3. `uv run pytest` passes with no failures.
4. `uv run ruff check src/ tests/` passes with no errors.
5. `uv run pyright src/` reports no type errors.
6. `uv run pytest --cov=src --cov-report=term` shows >= 80% overall coverage.
7. A short phase log is appended to `temp/` (e.g. `temp/phase-8-thread-api.md`)
   and a decision record is added under `history/` if any non-trivial
   design decision was made.
