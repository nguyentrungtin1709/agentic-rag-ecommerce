# Thread Management API (Real Endpoints)

**Version**: 8.0.0
**Date**: 2026-06-14
**Status**: Approved

## What
Replace the five stub endpoints in `src/app/api/threads.py`
(`POST /api/v1/threads`, `GET /api/v1/threads`,
`GET /api/v1/threads/{id}`, `DELETE /api/v1/threads/{id}`,
`GET /api/v1/threads/{id}/history`) with real implementations backed
by `ThreadRepository`, `ImageRepository`, the lifespan-managed
LangGraph `app.state.graph`, and `ValkeyService.delete_pattern` for
cache invalidation. Add a new `image_repo.list_by_message_ids`
batch method, a `request.state.current_user` shim in
`get_current_user`, a `GraphStateDep` dependency, and a private
`_invalidate_thread_list_cache` helper.

## Why
- Phases 1–7 left `ThreadRepository`, `ImageRepository.list_by_message_id`,
  the `app.state.graph` singleton, the `thread_list_key_builder`,
  the `delete_pattern` Valkey helper, and the `delete_thread` Celery
  task (still a stub) all wired and tested. None of the HTTP endpoints
  in `api/threads.py` actually call them — every request returns
  `501 Not Implemented`. Phase 8 closes that loop.
- The thread-list endpoint needs both rate limiting (slowapi) AND
  response caching (fastapi-cache2) on a per-user basis. The key
  builder for the cache was tested in isolation in Phase 5
  (`tests/integration/test_thread_list_cache.py`); the route has
  never been wired to the decorator stack.
- The history endpoint is the first production code path that reads
  from the LangGraph checkpointer at request time
  (`graph.aget_state(config)`). The shape of the response
  (flat message list, images attached to AI messages) drives the
  Phase 14 chat SSE shape, so it must be locked in now.
- `_is_transient` was the inflection point for a shared util in
  Phase 7; `image_repo.list_by_message_id` is the inflection point
  for a batch method now. Page-100 history fetches would otherwise
  issue 50 round-trips for a 50-turn thread; one batch query is
  10-30x faster and the method is reusable in Phase 14.

## How
- **Schemas (`schemas/thread.py`)** — add three Pydantic models:
  `HistoryImageItem(url, prompt)`, `HistoryMessage(id, type, content,
  created_at, images)`, `ThreadHistoryResponse(messages, next_cursor)`.
  Re-export them from `schemas/api.py` for back-compat.
- **`image_repo.list_by_message_ids` (D8.9)** — one batch method that
  issues `SELECT ... WHERE request_message_id = ANY($1::text[]) ORDER BY
  request_message_id, created_at ASC` and groups rows into
  `dict[str, list[GeneratedImage]]`. Empty input short-circuits to
  `{}` (no DB call). Output shape is identical to per-id
  `list_by_message_id` so existing tests still cover the wrapping.
- **`request.state.current_user` (D8.7/Q1)** — set
  `request.state.current_user = claims` in `get_current_user` so the
  `thread_list_key_builder` (which reads
  `request.state.current_user["sub"]`) gets a populated value.
- **Helper `_invalidate_thread_list_cache(valkey, user_id)`** —
  private module function wrapping `valkey.delete_pattern(f"threads:{user_id}:*")`,
  called by every endpoint that mutates the thread list (create, delete).
- **`GraphStateDep` (`dependencies.py`)** — typed alias that resolves
  to `request.app.state.graph`. Keeps the history endpoint free of
  `app.state` plumbing.
- **`POST /api/v1/threads`** — rate-limit (10/min) + create row +
  invalidate list cache. Returns 201 + `ThreadResponse`.
- **`GET /api/v1/threads`** — rate-limit (60/min) + per-user
  fastapi-cache2 (TTL = `settings.thread_list_cache_ttl`,
  default 120 s) + cursor pagination via
  `ThreadRepository.list_by_user`. Decorator stack is
  `@router.get` → `@limiter.limit` → `@cache` (outermost runs first
  so rate-limit short-circuits before the cache lookup).
- **`GET /api/v1/threads/{id}`** — rate-limit (60/min). 404 if not
  found or not owned (no existence leak), 410 if `status='deleting'`.
- **`GET /api/v1/threads/{id}/history`** — rate-limit (60/min) +
  Option C hybrid cursor (D8.8) + batch image attachment. 404 / 410
  same as the metadata endpoint.
- **`DELETE /api/v1/threads/{id}`** — rate-limit (10/min) + set
  status to `deleting` (atomic, single UPDATE) + enqueue the
  `delete_thread` Celery task (still a stub; real impl is Phase 10)
  + invalidate the list cache. Returns 202.
- **Docstring + OpenAPI `description=` on history endpoint** —
  document the Option C response-size behaviour (may be less OR
  greater than `limit`; `next_cursor` is the source of truth for
  "is there more?").

## Key Decisions
- **D8.1**: Plan written to `TEMP.md` at the project root (user request).
- **D8.2**: History response is a flat message list with images
  embedded on `AIMessage`. `next_cursor: str | None` rides alongside
  so the FE can paginate by hand.
- **D8.3**: History uses `graph.aget_state(config)` (not
  `aget_state_history`) via `app.state.graph`. We only need the
  latest state snapshot, then paginate by message id at the app
  layer.
- **D8.4**: `DELETE` returns 404 (not 403) when the thread is not
  owned. Same response for "not found" and "not yours" so we do
  not leak existence.
- **D8.5**: `POST /threads/{id}/runs/stream` stays a 501 stub —
  Phase 14 work, not in scope here.
- **D8.6**: This ADR captures every D8.x decision; future phases
  link to it rather than re-deriving.
- **D8.7 (Q1)**: `get_current_user` sets
  `request.state.current_user = claims`. One-line fix; the
  key builder depends on it.
- **D8.8 (Q2, Option C)**: History cursor is "any message id",
  but the response always extends the page backward to start on a
  `HumanMessage`. `len(response.messages)` may therefore be
  **greater** than `limit` (by a few messages in the typical case).
  This is documented in the docstring and the OpenAPI `description=`
  so the FE can treat `limit` as a hint, not a strict cap.
- **D8.9 (Q3, Option B)**: New `image_repo.list_by_message_ids`
  batch method — 1 query with `ANY($1::text[])` instead of N+1.
  Page-100 latency drops from ~150 ms to ~5–10 ms; method is
  reusable in Phase 14 for `image_ready` SSE events.
- **D8.10 (Q4)**: No per-file coverage target for `api/threads.py`
  — the project-wide `--cov-fail-under=80` gate is enough. Past
  phases do not set per-file targets either.
- **Decorator order on list endpoint**:
  `@router.get` (outermost) → `@limiter.limit` → `@cache`
  (innermost). The rate-limit decorator must wrap the cache so
  over-budget requests are rejected before the cache lookup
  (otherwise an attacker could spam cache MISSes with a forged user
  identity and drive the cache layer to its connection limit).
- **`@limiter.limit` requires `response: Response` in the handler
  signature** so slowapi can attach `X-RateLimit-*` headers. The
  handler is otherwise free to return a Pydantic model; FastAPI
  serialises it.

## Impact
- **New files**:
  - `history/8_0_0_THREAD_MANAGEMENT_API.md` — this ADR.
  - `tests/unit/api/__init__.py` — package marker.
  - `tests/unit/api/test_threads.py` — 20+ unit cases.
  - `tests/integration/test_threads.py` — 9+ in-process cases.
  - `temp/phase-8-thread-api.md` — phase log.
- **Modified files**:
  - `src/app/api/threads.py` — five stub bodies replaced with real
    implementations; new `_invalidate_thread_list_cache` helper.
  - `src/app/schemas/thread.py` — three new Pydantic models.
  - `src/app/schemas/api.py` — re-export the three new models.
  - `src/app/repositories/image_repo.py` — add
    `list_by_message_ids`.
  - `src/app/dependencies.py` — set
    `request.state.current_user` in `get_current_user`; add
    `GraphStateDep`.
  - `tests/unit/repositories/test_image_repo.py` — 4 new cases
    for `list_by_message_ids`.
  - `docs/05-IMPLEMENTATION-PLAN.md` — Phase 8 row `[PENDING]`
    to `[DONE]`.
- **No breaking API changes** to the existing thread contract — the
  schemas are additive, the DELETE 410 is the same as the stub (the
  stub was 501 so there is no real consumer of the 410 yet).
- **No new external dependencies** — `slowapi`, `fastapi-cache2`,
  `asyncpg`, and `langgraph` are already in `pyproject.toml`.
- **No database migrations** — the `threads` and `generated_images`
  tables from `alembic/versions/0001_initial_schema.py` are enough.

## Validation
- `docker compose up -d` brings up the full stack.
- Unit tests: 20+ in `tests/unit/api/test_threads.py`; 4 new in
  `tests/unit/repositories/test_image_repo.py`.
- Integration tests: 9+ in `tests/integration/test_threads.py`
  (in-process ASGI, no Docker required for the bulk; the 3
  E2E cases against the live stack skip cleanly when the
  stack is down, matching the Phase 7 webhook pattern).
- `ruff check .` and `ruff format --check .` clean.
- `pyright src/ tests/` — 0 errors.
- `pytest --cov=src/app --cov-fail-under=80` — green at 80%+ overall.
- Manual smoke test:
  - `POST /api/v1/threads` with a valid JWT returns 201 + body.
  - `GET /api/v1/threads` returns 200, second call is a cache HIT.
  - `GET /api/v1/threads/{id}/history` after seeding a few messages
    returns the expected flat list with images attached to the
    right `AIMessage`.
  - `DELETE /api/v1/threads/{id}` returns 202; subsequent `GET`
    returns 410.

## References
- FR-011, FR-012, FR-013, FR-015, FR-016, FR-017, FR-018, FR-019,
  FR-020.
- NFR-024 (log with `thread_id`, `user_id`, `event_type` for
  thread lifecycle entries).
- `docs/05-IMPLEMENTATION-PLAN.md` lines covering Phase 8.
- `docs/analysis/01-USE-CASE-ANALYSIS.md` UC-001 / UC-002 / UC-003
  (CRUD threads) and UC-004 (paginated history).
- LangGraph public API:
  `langgraph.pregel.Pregel.aget_state(config, *, subgraphs=False)`
  — returns a `StateSnapshot` with `.values`, `.next`, `.config`,
  `.metadata`, `.created_at`, `.parent_config`, `.tasks`,
  `.interrupts`.
- LangChain `BaseMessage` subclasses:
  `HumanMessage`, `AIMessage`, `SystemMessage`, `ToolMessage`,
  `FunctionMessage`. `type` is a string property that already
  lower-cases the class name, so `"human"` and `"ai"` are the
  expected values (Pydantic `Literal` matches).
- `history/5_0_0_SHARED_RESOURCE_INJECTION.md` (Phase 5 ADR; source
  of `app.state.graph`, `app.state.valkey`, `app.state.s3`, and
  the `request.app.state` access pattern in dependencies).
- `history/7_0_0_WEBHOOK_HANDLING.md` (the template for this
  ADR and the rate-limit + cache decorator-order pattern).
- `TEMP.md` — full Phase 8 plan with checklist and Q&A log.
