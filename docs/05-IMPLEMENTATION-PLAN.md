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
> Phases 1–4 are `[DONE]`.  Phases 5–14 are `[PENDING]`.

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
| Profile API | `src/app/api/profile.py` | STUB — 1 endpoint returning 501 |
| Admin API | `src/app/api/admin.py` | STUB — 2 endpoints returning 501 |
| Webhook API | `src/app/api/webhooks.py` | PARTIAL — HMAC verify done, Celery dispatch stubbed |
| `synthesize` node | `src/app/agent/nodes/synthesize.py` | STUB |
| `generate_title` node | `src/app/agent/nodes/generate_title.py` | STUB |
| `generate_image` node | `src/app/agent/nodes/generate_image.py` | STUB |
| TrendScout subagent | `src/app/agent/subagents/trend_scout/agent.py` | STUB |
| `ProductIndexer` | `src/app/rag/indexer.py` | STUB |
| `ProductRetriever` | `src/app/rag/retriever.py` | DELETE — deprecated by Phase 4 product_rag subagent |
| `process_webhook` task | `src/app/tasks/process_webhook.py` | STUB |
| `reindex_products` task | `src/app/tasks/reindex_products.py` | STUB |
| `cleanup_expired_threads` task | `src/app/tasks/cleanup_expired_threads.py` | STUB |
| `delete_thread` task | `src/app/tasks/delete_thread.py` | STUB |
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
| 5 | Cross-cutting wiring + Repo/Service enhancements | slowapi, fastapi-cache2, S3Service/ValkeyService/Repository extensions | PENDING |
| 6 | RAG Ingestion | ProductIndexer real, reindex_products Celery task, delete `rag/retriever.py` | PENDING |
| 7 | Webhook handling | `api/webhooks.py` Celery dispatch, `process_webhook` task real | PENDING |
| 8 | Thread Management API | 5 thread endpoints + history + status guards + cache invalidation | PENDING |
| 9 | Profile + Admin API | 3 admin endpoints (profile, reindex, all threads) | PENDING |
| 10 | Cleanup Celery tasks | `delete_thread` + `cleanup_expired_threads` real | PENDING |
| 11 | Trend Scout | TrendScoutNode via `create_react_agent` + Tavily + DuckDuckGo | PENDING |
| 12 | Synthesize + Title Generation | `synthesize.py` + `generate_title.py` real (4-prompt dispatch) | PENDING |
| 13 | Image Generation | `generate_image.py` real — DALL-E + S3 + Valkey quota | PENDING |
| 14 | Chat SSE endpoint | `api/chat.py` real — 7 SSE event types + shared-resource injection | PENDING |

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

## Phase 5 — Cross-cutting wiring + Repo/Service enhancements

### Objective

Make the application production-ready at the boundaries: enforce
per-user rate limits, cache thread-list responses, and add the small
service / repository methods that downstream phases need (S3 delete +
bucket ensure, Valkey quota + pattern delete, thread/image repos
with cleanup and history-attachment queries).

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

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/services/test_s3_service.py` | `build_key` returns `images/{user_id}/{thread_id}/{timestamp}.png`; `delete` calls `s3.delete_object`; `ensure_bucket` returns silently when bucket exists, raises `BucketNotFound` (or wraps `ClientError` 404) when missing — never creates |
| `tests/unit/services/test_valkey_service.py` | `increment_quota` first call returns 1 and sets TTL; subsequent calls increment; `delete_pattern` removes all matching keys via SCAN |
| `tests/unit/repositories/test_thread_repo.py` (extend) | `find_expired` returns only threads older than cutoff, excludes `deleting` |
| `tests/unit/repositories/test_image_repo.py` | `delete_by_thread` cascade; `list_by_message_id` filters correctly; `count_by_user_date` returns count for that UTC day |
| `tests/integration/test_lifespan.py` | Startup completes; `s3.ensure_bucket` called; slowapi + fastapi-cache2 globals populated |
| `tests/integration/test_rate_limit.py` | 21st `POST /runs/stream` returns 429 within 1 minute; webhook + health exempt |
| `tests/integration/test_thread_list_cache.py` | First call hits the repo (cache miss), second returns from Valkey; `delete_pattern('threads:{user_id}:*')` invalidates |

---

## Phase 6 — RAG Ingestion

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

## Phase 7 — Webhook handling

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

## Phase 8 — Thread Management API

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

- 501 stub for now; full implementation in Phase 14.  Phase 14
  will add the busy/idle state transition and the
  shared-resource injection.

#### 8.7 Cache invalidation helper

```python
async def invalidate_thread_list_cache(valkey: ValkeyService, user_id: str) -> int:
    """Delete all cached pages for this user's thread list (FR-098)."""
    return await valkey.delete_pattern(f"threads:{user_id}:*")
```

Used by `POST /threads` (8.1) and `DELETE /threads/{id}` (8.5).

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/api/test_threads.py` | Each endpoint happy path; 404 on missing thread; 410 on `deleting`; cache invalidation called on mutation; cursor pagination returns `next_cursor` correctly; history attaches images by `request_message_id` |
| `tests/integration/test_threads.py` | End-to-end: create → list → get → history (mocked checkpointer) → delete → Celery enqueued; rate limit 429 after threshold |

---

## Phase 9 — Profile + Admin API

### Objective

Replace the 3 stubs in `api/profile.py` and `api/admin.py` with real
implementations backed by `AsyncPostgresStore`, `ThreadRepository`,
and the Celery `reindex_products` task.

### Tasks

#### 9.1 `GET /api/v1/users/{user_id}/profile` (admin only, FR-032)

- `AdminDep` guard.
- `await store.aget(("profiles", user_id), "profile")`.
- `404` if the key does not exist.
- Return the serialised profile dict.

#### 9.2 `POST /api/v1/admin/reindex` (FR-103)

- `AdminDep` guard.
- `reindex_products.delay()`.
- Return `202 Accepted` with `{"status": "queued"}`.

#### 9.3 `GET /api/v1/admin/threads` (FR-104)

- `AdminDep` guard.
- `ThreadRepository.list_all(before, limit)` (new method on the
  repo — same shape as `list_by_user` but no `user_id` filter).
- Return `ThreadListResponse`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/api/test_profile.py` | Admin allowed reads profile from store; non-admin gets 403; missing profile returns 404 |
| `tests/unit/api/test_admin.py` | `POST /reindex` enqueues Celery task with no args; `GET /admin/threads` paginates across all users; non-admin gets 403 on both |
| `tests/integration/test_admin.py` | End-to-end: admin token from JWT → 202 on reindex → 200 on threads list |

---

## Phase 10 — Cleanup Celery tasks

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

## Phase 11 — Trend Scout

### Objective

Implement `run_trend_scout` as a real LangChain `create_react_agent`
with two tools: Tavily (primary) and DuckDuckGo (fallback).  Output
is a concise trend summary + an optional DALL-E prompt.

### Tasks

#### 11.1 State and schema

- `TrendScoutState` extends `langchain.agents.AgentState` with
  `generate_image: bool`.
- `TrendScoutOutput` Pydantic model: `trend_summary: str`,
  `image_prompt: str | None`.

#### 11.2 Tools (`subagents/trend_scout/tools.py`)

- `TavilySearchResults(max_results=5)` (uses `TAVILY_API_KEY`).
- `@tool duckduckgo_search(query: str) -> str` wrapping
  `DuckDuckGoSearchRun()`.

#### 11.3 `_build_trend_scout_system` helper

- Base prompt from `trend_scout_system.md`.
- Append `## Conversation history summary` (if `state["summary"]`
  non-empty), `## User preferences` (if profile non-empty),
  `## Products already recommended` (if `retrieved_products` non-empty),
  and `## Output note` (if `generate_image=True`).

#### 11.4 Agent + wrapper

- `create_react_agent(model=ChatOpenAI(settings.orchestrator_model), tools=[...], state_schema=TrendScoutState)`.
- `run_trend_scout(state, config)` builds the dynamic
  `SystemMessage`, prepends it to the last 4 messages, calls
  `ainvoke`, and parses the output into `TrendScoutOutput`.
- Fallback: if both Tavily and DuckDuckGo raise, return
  `{"trend_summary": None, "image_prompt": None}`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/subagents/test_trend_scout_tools.py` | `duckduckgo_search` is a valid LangChain tool; `tavily_search` is configured with `max_results=5` |
| `tests/unit/agent/subagents/test_trend_scout_system.py` | Each context section appears when its data is non-empty; image-prompt instruction is present when `generate_image=True`; sections are omitted when empty |
| `tests/unit/agent/subagents/test_trend_scout_wrapper.py` | `SystemMessage` is prepended as `messages[0]`; `generate_image` flows into sub-state; `trend_summary` and `image_prompt` are mapped back to parent state; fallback returns `None` for both fields when both tools fail |

---

## Phase 12 — Synthesize + Title Generation

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

## Phase 13 — Image Generation

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

## Phase 14 — Chat SSE endpoint

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
- `tavily-python` (Phase 11)
- `langchain-community` (Phase 11, for `DuckDuckGoSearchRun`)
- `fastembed` (Phase 4, 6, 7 — pulled by `qdrant-client[fastembed]`)

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
