# 10.0.0 — Cleanup Celery Tasks

## Status
Accepted — 2026-06-15

## Context

Phase 10 replaces two Celery task stubs that were scaffolded during
Phase 1 but never given real bodies:

1. `tasks.delete_thread(thread_id, user_id)` — enqueued by
   `DELETE /api/v1/threads/{thread_id}` after the row has been
   marked `status='deleting'` (Phase 8). Runs on the `cleanup` queue.
2. `tasks.cleanup_expired_threads()` — scheduled by Celery Beat at
   02:00 UTC every night (configured in `celery_app.py:78`). Sweeps
   threads whose `last_activity_at < now() - 30 days` and runs the
   same per-thread cleanup. Runs on the `cleanup` queue.

Both must enforce the atomic ordering mandated by NFR-015:
**S3 objects first, then `generated_images` rows, then the `threads`
row.** A failure in step 1 must abort the chain so no orphan DB rows
reference a live S3 key.

All required infrastructure already exists from prior phases:

- `ImageRepository.list_by_thread(thread_id)` and
  `delete_by_thread(thread_id)` (Phase 5).
- `ThreadRepository.delete(thread_id, user_id)` (Phase 8) and
  `find_expired(cutoff)` (Phase 8, excludes `status='deleting'`).
- `S3Service.delete(key)` (Phase 5.8).
- Celery Beat schedule `cleanup-expired-threads-nightly` (Phase 1).
- `celery_app.include` lists both task modules.
- `DELETE /api/v1/threads/{id}` already calls
  `delete_thread_task.delay(str(thread_id), user_id)`.

Phase 10 only fills in the task bodies and adds a single new
repository method (`ThreadRepository.delete_by_id`).

## Decisions

### D10.1 — S3 failure aborts the chain and triggers a Celery auto-retry of the whole task

The NFR-015 ordering rule is **strict**: never delete a
`generated_images` row whose `s3_key` is still live in S3 (that would
leave a dangling URL in the table). The chosen recovery is the same
pattern `process_batch` and `process_webhook` already use: the
`asyncio.run(_process(...))` wrapper catches the exception, calls
`is_transient(exc)` to classify it, and either:

- **Transient** (rate limit, network blip, 5xx): log a warning,
  increment nothing, `raise self.retry(exc=exc)`. Celery re-runs
  the whole chain after `default_retry_delay` (30s for
  `delete_thread`, 300s for `cleanup_expired_threads`), with
  exponential backoff + jitter.
- **Permanent** (schema bug, malformed data, non-recoverable AWS
  error): log an `ERROR`, return a structured
  `{"status": "failed", "error_type": ..., "error": ...}` dict
  without re-raising.

**Idempotency argument** that justifies whole-task retry:

- S3 `delete_object` is idempotent: calling it on a key that does
  not exist returns 204 No Content. A retry after a partial-success
  first attempt is safe and cheap.
- `ImageRepository.delete_by_thread` is a single
  `DELETE FROM generated_images WHERE thread_id = $1`. After the
  retry succeeds, the row is gone — there is no state to re-clean.
- `ThreadRepository.delete_by_id` (new in this phase) is a
  `DELETE FROM threads WHERE id = $1` that returns `False` on
  "row not found". A second call is a clean no-op.

**Why not continue-on-error and log partial failures**: the
attractive alternative is "delete as many S3 objects as possible,
return success if at least one row was deleted, return failure
otherwise". The cost is a new failure mode where image_records
reference deleted S3 keys (the opposite of NFR-015) — recoverable
only via a manual operator sweep of `generated_images` joined
against S3. Whole-task retry is simpler, the retry budget is
generous (`max_retries=3`), and the user-facing DELETE endpoint
already returns 202 immediately so the user never sees the retry
latency.

### D10.2 — LangGraph checkpointer tables are NOT cleaned up

The `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes`
Postgres tables (managed by `AsyncPostgresSaver.setup()`) hold
per-thread state that references a now-deleted `thread_id`. They
will accumulate as dead rows.

**Why not delete them in the worker**: cleaning them up requires
opening a second `AsyncPostgresSaver` (or the underlying
`psycopg` pool) in the Celery worker process. The Celery worker
is a separate process with no `app.state`; opening a second
checkpointer means:

- A second `psycopg_pool.ConnectionPool` with its own TCP
  connections to Postgres.
- A second `await checkpointer.setup()` invocation
  (idempotent, but still cost).
- Brittle coupling to LangGraph's internal table layout
  (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
  `checkpoint_migrations`) — these are not part of the public
  LangGraph API and may change between versions.

**Why leaving them is acceptable**:

- Checkpoint rows are **cheap to leave**: a single thread
  generates a few KB of state across the lifetime of the
  conversation. Even with 10k deleted threads per month, the
  checkpoint table grows by ~50MB/month, well within the
  30-day nightly cleanup window.
- They are **never queried after the thread is deleted**: the
  only read path is `GET /api/v1/threads/{id}/history`, which
  requires the thread row to exist (it returns 404 otherwise).
- They **do not surface to operators** unless they look at the
  raw DB.

**Compensating control** (future phase, not in scope here):
a separate `vacuum_checkpoints` maintenance job that deletes
rows where `thread_id` no longer exists in `threads`. Tracked
in `temp/phase-10-cleanup-tasks.md` as a follow-up.

### D10.3 — `cleanup_expired_threads` processes all expired threads sequentially in one task

The alternative shapes considered:

- **Fan-out via Celery `chord` / `group`**: one sub-task per
  thread. Parallelises the work, but adds partial-failure
  aggregation overhead and a chord-callback task to sum the
  counts. With 1 Celery worker process and
  `concurrency=4`, the wall-clock improvement is at most 4x
  (and degraded by boto3's thread-safety in the worker).
- **Batched chunks (e.g. 50 threads per loop) with periodic
  progress logs**: adds progress visibility but does not
  improve wall-clock or failure containment.

The chosen shape is "one task, all threads, sequential". The
nightly 30-day window produces a bounded number of expired
threads (low hundreds even at full adoption). `time_limit=3600s`
(60 minutes) is 10x what a sequential sweep needs in the worst
case (10s per thread * 100 threads = ~17 minutes).

This keeps the failure model simple: one task = one summary dict.
A stuck task is easier to diagnose than a chord where 50 sub-tasks
silently died.

### D10.4 — `ThreadRepository.delete_by_id(thread_id)` is a new method

The existing `delete(thread_id, user_id)` enforces ownership via
`WHERE id = $1 AND user_id = $2` and is the right call for the
user-facing DELETE endpoint.

The nightly sweep has **no user context** (it is a system job).
The natural counterpart is a method that deletes by `id` only,
with no `user_id` filter. The method is deliberately named
`delete_by_id` to make the ownership difference loud at the call
site — anyone reading the sweep code will immediately see that
ownership is not checked.

The SQL is symmetric with `find_expired(cutoff)` (which also
ignores the user_id filter), so the two non-user-scoped read/write
paths in the repo are paired in intent.

### D10.5 — `set_status(thread_id, "deleting")` is called inside the sweep loop

The `DELETE /api/v1/threads/{id}` endpoint already calls
`set_status(thread_id, "deleting")` before enqueueing the task.
The sweep does the same before its per-thread chain so that:

- A subsequent sweep iteration sees the row in `status='deleting'`
  and skips it (the `find_expired` SQL filter is `status !=
  'deleting'`). This makes the sweep resilient to partial
  failures: a thread that failed partway through its chain
  (S3 succeeded, DB delete failed after retry budget) is left
  in `status='deleting'` and is **not** re-picked by the next
  night's sweep — manual operator intervention is required to
  unstick it.
- A racing explicit DELETE on the same thread (between sweep
  pick-up and sweep execution) is a no-op on the `set_status`
  side because the row is already in `status='deleting'`.

## Consequences

**Positive**

- Strict NFR-015 ordering is enforced by the
  `asyncio.run(_process(...))` structure: a failure in step 1
  raises and the chain halts.
- Whole-task retry is safe (idempotency argument in D10.1) and
  the retry budget (`max_retries=3`) is generous for the
  failure modes we expect (S3 rate limits, transient 5xx).
- Operator visibility: every step logs a structured event with
  `thread_id` and a per-phase count. Failure logs include the
  exception type and message.
- Adding `delete_by_id` keeps the ownership semantics loud at
  the call site (D10.4) and pairs with the existing
  non-user-scoped `find_expired` method.

**Negative / trade-offs**

- Partial-failure recovery is **manual** for the nightly sweep.
  A thread stuck in `status='deleting'` requires an operator
  query. This is acceptable for a Phase 10 scope but should
  be promoted to a self-healing retry or an alert in a
  follow-up.
- LangGraph checkpointer rows accumulate as dead data (D10.2).
  Bounded by the 30-day cleanup cadence but never purged.
  Tracked as a follow-up.
- The `delete_by_id` SQL bypasses the ownership check. The
  only caller is `cleanup_expired_threads` (no user context
  by design). Any future use of this method from a
  user-facing code path is a security regression — the
  `delete_by_id` naming convention is the only signal that
  this is an internal-cleanup-only method.

## Implementation notes

- Both tasks use the same `asyncio.run(_process(...))`
  pattern as `process_batch` and `process_webhook`. The
  sync Celery function is a thin wrapper; the async
  coroutine does the real work and is independently
  testable.
- `open_pools(settings.database_url)` is called at the
  top of every `_process` coroutine. The function is
  idempotent on the current event loop — it reuses the
  existing pool if one is already open on this loop, and
  opens a new one otherwise. This is the pattern that
  keeps asyncpg connections bound to the correct loop
  across the multiple `asyncio.run()` calls a single
  Celery task body might make (see
  `celery_app.py:36-49` for the long-form comment).
- `S3Service` is constructed inside the worker from
  `get_settings()` (no `app.state` in a Celery process).
  `await s3.close()` is called in a `finally` block to
  release boto3 connections.
- The expiry window is configurable via
  `Settings.thread_expiry_days` (default 30, FR-018) and
  is documented in `.env.example`.  Dev environments can
  lower it to exercise the sweep without waiting weeks.

## Test coverage

- 7 unit tests in `tests/unit/tasks/test_delete_thread.py`
  (happy path, zero images, S3 failure aborts, transient
  error triggers retry, permanent error returns failed,
  `s3.close()` in finally, structured log shape).
- 7 unit tests in `tests/unit/tasks/test_cleanup_expired_threads.py`
  (no expired threads, sequential processing, mark
  deleting first, 30-day cutoff, unexpected exception
  returns failed, image count aggregation, `s3.close()` in
  finally).
- 1 new unit test in `tests/unit/repositories/test_thread_repo.py`
  for `delete_by_id` SQL contract (no `user_id` filter).
- 4 integration tests in `tests/integration/test_cleanup.py`
  (E2E delete of one thread, E2E sweep of expired threads,
  sweep skips `status='deleting'`, idempotent re-run).
