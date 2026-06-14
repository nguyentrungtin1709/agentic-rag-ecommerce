# 9.0.0 — Profile + Admin API

## Status
Accepted — 2026-06-14

## Context

Phase 9 ships three admin-facing endpoints that were either stubbed (501) or
missing entirely from the Phase 6 / Phase 8 baseline:

1. `GET /api/v1/users/{user_id}/profile` — admin reads the long-term style
   profile for any user (FR-032). Profile data is written by the `profiler`
   LangGraph node under namespace `("profiles", user_id)`, key `"profile"`,
   in `AsyncPostgresStore`.
2. `GET /api/v1/admin/threads` — admin lists all threads across all users
   (FR-104). Mirrors the user-facing `GET /api/v1/threads` shape.
3. `GET /api/v1/admin/reindex` (NEW) — admin lists reindex jobs. Closes a
   REST gap: the `POST /admin/reindex` + `GET /admin/reindex/{job_id}` pair
   shipped in Phase 6 left the collection endpoint (no `job_id`) unbuilt.

All three endpoints sit behind `AdminDep` (`is_staff: true` in the JWT, FR-085).
None performs LLM calls or graph execution — they are thin reads, just like
the Phase 8 thread endpoints.

## Decisions

### D9.1 — `app.state.store` is the single source of truth for the store handle

`main.py:81` already builds the `AsyncPostgresStore` and passes it to
`build_graph(checkpointer, store)`, but the reference is dropped after that
call. Phase 9 needs the same store instance inside the profile handler, so
the fix is a one-line addition: `app.state.store = store` immediately after
`app.state.graph = build_graph(...)`. We deliberately do **not** re-create
the store — `build_graph` may keep internal references to it (LangGraph
Store protocol), and `AsyncPostgresStore.setup()` is idempotent so an extra
instance would only waste connections.

### D9.2 — `StoreDep` joins the `Annotated` dependency family

The Phase 8 codebase already uses `Annotated[T, Depends(get_x)]` aliases for
every cross-cutting resource (`CurrentUserDep`, `ValkeyDep`, `GraphDep`,
`ThreadRepoDep`, `IngestionJobRepoDep`). Phase 9 follows the same pattern
with `StoreDep = Annotated[BaseStore, Depends(get_store)]`. Two benefits:
handler signatures stay short, and unit tests can swap the implementation
through `app.dependency_overrides[get_store] = ...` without re-wiring
`Request` injection.

### D9.3 — Profile response uses an envelope `{profile, updated_at}`

The `profiler` node writes a fresh row on every agent turn. Exposing
`updated_at` next to the profile fields lets an operator answer "is this
profile current?" without comparing against chat timestamps. The store's
`Item.updated_at` field is populated by `AsyncPostgresStore` on every
`aput`, so the timestamp is always meaningful. The envelope adds one
indirection but keeps the response shape self-describing.

### D9.4 — Admin threads endpoint has no filter

The user-facing `GET /api/v1/threads` accepts no filter either (Phase 8
chose pure cursor pagination, D8.3). Phase 9 mirrors that decision for the
admin counterpart — `?before=` and `?limit=` are the only knobs. Adding
`?user_id=` / `?status=` is a Phase 9.x concern and was explicitly rejected
for this phase to keep the surface small.

### D9.5 — `list_all` mirrors `list_by_user` ordering and cursor logic

`ThreadRepository.list_all(before, limit)` is a near-copy of `list_by_user`
with the `user_id` predicate removed. Same `(updated_at DESC, id DESC)`
ordering, same `before` cursor resolution, same "fetch limit+1" overflow
not needed because `len(rows) == limit` is sufficient as a "has next page"
signal. The admin context means there is no ownership check on the cursor
UUID — operators can pass a thread ID from any user and the query resolves
correctly (or returns an empty page if the ID is unknown).

### D9.5' — Reindex jobs cursor uses `COALESCE(started_at, 'infinity'::timestamptz)`

The `ingestion_jobs` table has no `created_at` column. The only timestamp
columns are `started_at` (nullable while the job is `pending`) and
`completed_at` (nullable until terminal). Sorting by `started_at DESC`
would push pending jobs to the end of the list — counter-intuitive for the
common "what's the most recent reindex?" query, where the answer is often a
job the operator just dispatched (still in `pending` for the few hundred ms
before the worker picks it up).

`COALESCE(started_at, 'infinity'::timestamptz)` treats NULL as "newer than
any real timestamp" so pending jobs sort to the top. The trade-off:

- **Pro**: `GET /admin/reindex` first page always shows the freshest activity
  including jobs that have not been picked up yet.
- **Con**: If a cursor points at a pending job, the `WHERE COALESCE(...) <
  'infinity'` predicate excludes every other pending job. The next page
  jumps straight to started jobs. Documented as known edge case in the ADR;
  the operator experience is "the most recent page may skip pending jobs you
  have already seen" — non-disastrous.

Within a run of pending jobs (multiple concurrent dispatches), the secondary
sort `id::text DESC` provides a deterministic but non-temporal ordering. In
practice the `pending` state lasts hundreds of milliseconds, so collisions
are extremely rare.

### D9.6 — `404` for missing profile (not an empty `UserProfile()`)

`GET /api/v1/users/{user_id}/profile` returns 404 when `store.aget`
returns `None`. The alternative (return `UserProfile()` with all default
empty values) would conflate "user has never chatted" with "user has chatted
but the profiler produced an empty profile" — operators cannot distinguish
the two. A 404 makes the contract explicit: this resource does not exist.
Same shape as `GET /api/v1/threads/{id}` returning 404 for an unknown ID
(D8.4).

Corrupt payloads (the stored JSON fails `UserProfile.model_validate`) raise
500 — silently returning an empty profile would hide a data-integrity bug.

### D9.6' — `IngestionJobSummary` is the list shape; `batches[]` stays on the detail endpoint

`GET /admin/reindex` returns 10 summary fields per item (job_id, status,
counters, timestamps, error_message, celery_task_id). The
`batches[]` array is omitted — a single reindex can dispatch 100+ batches
each carrying ~100 product_ids, so the per-item payload would otherwise
balloon to several hundred KB and the page would be unreadable.

Drill-down is via `GET /admin/reindex/{job_id}` (Phase 6, unchanged), which
returns the full per-batch status array. The contract is: **list is a
catalog, detail is a dashboard**.

`celery_task_id` is included in the summary so operators can grep worker
logs without round-tripping to the detail endpoint. This was added on
operator request (June 14 design call).

### D9.7 — No caching on the profile endpoint

The profile endpoint is admin-only with a 60/min rate limit. The Valkey
fastapi-cache2 backend is shared with the user-facing thread list, and
introducing a second per-user cache key for admin traffic would add
operational complexity (invalidation when the profile changes) for almost
no latency win (< 5 ms per request from the store). If admin traffic
surges, a TTL'd cache is a future Phase 9.x optimization.

### D9.8 — Rate limit 60/min on all three new endpoints

Match the Phase 8 thread endpoints' rate limit (D8.5). Admin traffic is low
but the limit guards against runaway scripts and is essentially free to
configure.

## Consequences

- `app.state.store` becomes a process-wide singleton (lifespan-owned).
  Shutdown is implicit — the underlying psycopg pool closes in
  `close_pools()`.
- `StoreDep` joins the existing `Annotated` dependency family. Test
  fixtures follow the Phase 8 pattern (`_make_store_override` returns a
  closure that mimics `get_store(request)`).
- `ThreadRepository.list_all` has no ownership check on the cursor —
  intended, but the comment in the method docstring calls it out for
  future maintainers.
- `IngestionJobRepository.list_all` adds two SQL calls per request
  (one for cursor resolve, one for the page) — equivalent to
  `ThreadRepository.list_by_user`. No batched-CURSOR optimisation
  attempted because the endpoint is admin-only with low traffic.
- `IngestionJobSummary` uses Pydantic v2 `Field(alias="id")` to expose
  the `IngestionJob.id` UUID as `job_id` in JSON. The alias works
  transparently with `model_validate(job_instance)` thanks to
  `from_attributes=True`.
- Three ADRs from Phase 8 (D8.5 — rate limit defaults, D8.4 — 404 shape,
  D8.3 — cursor-only pagination) are reused verbatim.

## Test Plan (rolled up)

- 8 unit cases for `GET /users/{user_id}/profile` (auth 401/403, happy
  path, missing 404, corrupt 500, namespace/key assertion, field pass-through)
- 18 unit cases for admin endpoints (10 threads list + 8 reindex list)
- 4 unit cases for `ThreadRepository.list_all` (first page, cursor resolve,
  unknown cursor, ordering)
- 4 unit cases for `IngestionJobRepository.list_all` (first page, cursor
  resolve, unknown cursor, NULL started_at handling)
- 6 integration cases against the live Docker stack (admin JWT, two users,
  cursor pagination, 403 for non-admin)

Total: 40 new test cases. Cumulative suite: 382 → 422.

## Open Follow-ups (intentionally out of scope)

- `GET /admin/reindex?status=failed` — operator-only filter, defer.
- `POST /admin/users/{user_id}/profile` (manual profile override) — needs
  authorization model, defer.
- `DELETE /admin/users/{user_id}/profile` (GDPR right-to-erasure for
  profile data) — needs legal review, defer.
