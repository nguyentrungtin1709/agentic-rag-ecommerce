# 14.0.0 — Chat SSE Endpoint (Phase 14)

**Status**: IMPLEMENTED
**Date**: 2026-06-17
**Owner**: Implementer
**Scope**: Replace the `stream_run` 501 stub in `src/app/api/chat.py` with a
production-ready `text/event-stream` endpoint that drives the LangGraph agent
graph, injects every long-lived Path A service into `config["configurable"]`,
and atomically transitions the thread between `idle` and `busy`.

---

## Context

Phases 1–13 produced a LangGraph agent graph that emits seven typed SSE events
to a per-request `asyncio.Queue` (see `app/agent/nodes/_sse.py:emit_sse`). The
chat HTTP boundary was the only stub remaining: `src/app/api/chat.py:stream_run`
raised `501 Not Implemented`. Phase 14 closes that gap.

The endpoint is the connective tissue between the agent graph (Phases 8, 12,
13) and the HTTP boundary. All seven SSE event payloads are already defined in
`src/app/schemas/chat.py`; all `app.state` resources (Qdrant, Valkey, S3,
OpenAI, the compiled graph) are populated by the `lifespan` in
`src/app/main.py`. The implementation work is the endpoint itself, an atomic
`set_status_if_idle` repository method, an `ErrorPayload` schema, and the
graph-timeout configuration.

---

## Decisions

### D14.1 — StreamingResponse (not EventSourceResponse)

Use `StreamingResponse` from `fastapi.responses`, not `EventSourceResponse`
from `sse-starlette`. Rationale: minimal dependency surface, exact control of
the `event: <name>\ndata: <json>\n\n` frame format, and zero changes to the
spec's SSE schema. The `sse-starlette` package would be dead weight.

### D14.2 — Unbounded `asyncio.Queue()`

The per-request queue is constructed with no `maxsize`. The `emit_sse` helper
(`_sse.py:39`) calls `put_nowait` and silently no-ops on `QueueFull`, so a
bounded queue could drop events. With an unbounded queue, a slow client
causes memory to grow linearly with `(events_per_run * concurrent_runs)`, but
the slowapi rate limit (20/min per user) bounds that growth to a few hundred
events per minute per user — negligible.

### D14.3 — Atomic `set_status_if_idle`

A new `ThreadRepository.set_status_if_idle(thread_id, user_id, new_status)`
method transitions a thread from `'idle'` to `new_status` in a single SQL
statement::

    UPDATE threads
    SET status = $3, updated_at = now()
    WHERE id = $1 AND user_id = $2 AND status = 'idle'
    RETURNING id

The presence of a returned row means the flip happened; absence means the
thread was missing, owned by another user, or already in a non-idle status.
The caller maps `False` to 409 Conflict. This is the race-free replacement
for the `get` then `set_status` sequence that would otherwise allow two
concurrent requests to both observe `idle` and both claim the thread.

### D14.4 — Idle reset lives inside the background task

The `set_status(thread_id, 'idle')` + `touch(thread_id)` reset lives inside
the background `_run_graph` coroutine's `finally` block — not in a
`try/finally` on `stream_run` itself. The HTTP coroutine returns immediately
after scheduling `_run_graph()` via `asyncio.create_task`; Starlette's
`StreamingResponse` flushes the body as soon as the consumer generator
returns. The background task continues to drain events from the queue and,
on completion, flips the thread back to `idle` — even if the HTTP client
disconnected mid-stream. This is the only place the state-machine
transition can run reliably across client disconnects.

### D14.5 — `correlation_id` is a fresh `uuid4()` per request

Each request gets a brand-new `uuid4()` for `correlation_id`, populated on
both `config["configurable"]` (for the agent nodes) and `config["metadata"]`
(for LangSmith trace linkage). The thread id and the user id are NOT used
as correlation ids — they collide across turns of the same thread and would
make it impossible to trace a single request end-to-end.

### D14.6 — Graph exceptions translate to a single `error` SSE event

The background task catches every exception raised by `graph.ainvoke(...)`
and emits exactly one `error` SSE event with `code` and `message` fields.
The full traceback is logged at `ERROR` with `exc_info=True` (FR-111); the
client-facing message is sanitised (no PII, no traceback, no host names).
The stream always terminates with the implicit end of the response — no
`done` event is emitted on the error path.

### D14.7 — Full Path A injection (Qdrant + OpenAI + S3 + Valkey)

`config["configurable"]` carries every long-lived service the agent nodes
consume, all four sourced from `app.state` (Path A, D5.1). The original
plan only listed `qdrant_aclient`; that was incomplete. `generate_image`
reads `openai_client` (D13.4), `s3_service` (D13.5), and `valkey_service`
(D13.3, D13.7); `generate_title` reads `valkey_service` (D12.11 cache
invalidation). If any of these four is `None`, the corresponding node
fails at runtime. See `history/5_0_0_SHARED_RESOURCE_INJECTION.md` for
the original Path A rationale.

### D14.8 — Rate limit via `settings.rate_limit_chat` (20/minute)

The `@_limiter.limit(settings.rate_limit_chat)` decorator uses the
existing `Limiter` singleton from `app.rate_limit`, keyed on the JWT `sub`
claim via `get_jwt_user_id_or_ip`. The default is `"20/minute"`, defined
in `src/app/config.py:125`. The chat endpoint shares the same limiter
instance with the threads router, so a user who floods the threads CRUD
endpoints also gets throttled on the chat stream (and vice versa).

### D14.9 — Unify the `valkey` configurable key to `valkey_service`

The configurable key for the `ValkeyService` instance is unified to
**`valkey_service`** (matching the `*_service` convention used for
`s3_service` and the class name `ValkeyService`).

Phase 12's `generate_title.py:169` originally used the bare key `"valkey"`;
Phase 13's `generate_image.py:186` used `"valkey_service"`. The two commits
landed 56 seconds apart (`2ebabedb` then `ad8f545e` on 2026-06-16 22:56) —
no consistency review. Phase 14 fixes the discrepancy by changing
`generate_title.py` (and its test helper) to read `"valkey_service"`,
keeping `generate_image.py` unchanged.

**Why not defer:** the inconsistency would be re-imported on every chat
endpoint code review forever; the fix touches 2 production lines + 1 test
helper line, has no API impact, no data migration, and no public contract
change — the cost of doing it in a follow-up phase is identical to the
cost of doing it now. **Risk:** low — the only consumer of `"valkey"` was
`generate_title`; the only consumer of `"valkey_service"` was
`generate_image`; the test suites for both nodes pass after the rename.

### D14.10 — `chat_run_timeout_seconds` (default 120s)

The background task wraps `graph.ainvoke(...)` in
`asyncio.timeout(settings.chat_run_timeout_seconds)`. On `TimeoutError`
the task emits an `error` SSE event with `code='graph_timeout'` and
`message=<sanitised budget summary>`, then runs the same idle-reset path
as other failures (D14.4).

The default is **120 seconds**, env-overridable via
`CHAT_RUN_TIMEOUT_SECONDS` in `.env` / `.env.example`. 120s is conservative:
the LLM-call nodes already carry 10s/30s/60s internal timeouts (NFR-010),
and the response generator can take 30–90s for long product descriptions,
so 120s leaves headroom for one extra node hop without making a stuck run
feel unbounded to the client. The field is constrained `int` with
`ge=10` so a misconfiguration cannot accidentally lower the budget to
zero.

---

## Configurable key contract

The `config["configurable"]` keys each node reads from MUST be populated
by `stream_run`:

| Key                  | Read by                                               | Source on `app.state`     |
|----------------------|-------------------------------------------------------|---------------------------|
| `sse_queue`          | all terminal nodes                                    | per-request asyncio.Queue |
| `qdrant_aclient`     | ProductRAGAgent (via LlamaIndex)                      | `app_state.qdrant.client` |
| `openai_client`      | `generate_image.py:184`                               | `app_state.openai`        |
| `s3_service`         | `generate_image.py:185`                               | `app_state.s3`            |
| `valkey_service`     | `generate_title.py:169`, `generate_image.py:186`      | `app_state.valkey`        |
| `thread_id`          | not read by nodes; carried on `metadata`              | per-request value         |
| `user_id`            | not read by nodes; carried on `metadata`              | per-request value         |
| `correlation_id`     | not read by nodes; carried on `metadata`              | per-request value         |

`valkey_service` is the only key (D14.9).

---

## Stream termination — three layers

There are three distinct signals; do not conflate them.

1. **Wire-level (`done` SSE event).** Emitted by the `synthesize` node
   ([synthesize.py:343-352](src/app/agent/nodes/synthesize.py#L343-L352))
   with `usage` and `intent` payload. The client uses this as a UX signal
   ("render the answer now"). It is NOT a stream-end signal — the graph
   still has work to do (the `generate_image` parallel branch is still
   running, and `generate_title` may emit a `thread_title` event moments
   later).

2. **Stream-level (`None` sentinel).** Pushed by `_run_graph` in its
   `finally` block, AFTER `graph.ainvoke(...)` returns (or raises). The
   `event_generator` reads it via `await sse_queue.get()` and `return`s —
   at which point Starlette's `StreamingResponse` flushes the remaining
   bytes and closes the connection. This is the actual "stream is over"
   signal.

3. **Why `None` and not `done`.** If we used the `done` event as the
   stream-end signal, clients would close the connection BEFORE
   `image_ready`/`image_failed` arrived. The two signals must remain
   independent: `done` is a protocol event, `None` is a transport signal.

Typical happy-path event order on the wire:

```
1. synthesize   → token*           (LLM streaming chunks)
2. synthesize   → products         (if retrieved_products non-empty)
3. synthesize   → done             ← wire-level "answer ready"
4. generate_image → image_ready   (or image_failed)
5. generate_title  → thread_title (may arrive anywhere after start of turn)
6. _run_graph     → (no SSE event, just the None sentinel)
7. event_generator returns → HTTP body closes
```

---

## Edge cases

| Situation                                | Behaviour                                                                  |
|------------------------------------------|----------------------------------------------------------------------------|
| Client disconnect early                  | `_run_graph` keeps running; `sse_queue` is unbounded so `put_nowait` no-ops; cleanup still runs (D14.4) |
| Graph `TimeoutError` after budget        | `error {code: "graph_timeout"}` SSE event, idle reset runs                 |
| Graph raises any other exception         | `error {code: "internal_error"}` SSE event, idle reset runs                |
| Server shutdown                          | `asyncio.create_task` is cancelled mid-graph; cleanup may not run. Mitigation: graceful shutdown can `await asyncio.gather(*pending)` — defer to ops hardening |
| Slow consumer                            | `sse_queue` is unbounded, so the graph never blocks. Memory grows linearly with `(events_per_run * concurrent_runs)` which is bounded by the slowapi rate limit (20/min per user) |
| Two concurrent requests to same thread   | `set_status_if_idle` returns `True` for the first; the second observes the now-`busy` row and is rejected with 409 (the `WHERE status='idle'` clause prevents both from succeeding) |

---

## Files changed

| Layer       | File                                                | Change                                                                                  |
|-------------|-----------------------------------------------------|-----------------------------------------------------------------------------------------|
| Schemas     | `src/app/schemas/chat.py`                           | Add `ErrorPayload` (code, message)                                                      |
| Repository  | `src/app/repositories/thread_repo.py`               | Add `set_status_if_idle(thread_id, user_id, new_status)`                                |
| Endpoint    | `src/app/api/chat.py`                               | Replace the 501 stub with `stream_run` + `_run_graph` + `event_generator`               |
| Refactor    | `src/app/agent/nodes/generate_title.py`             | Line 169: rename configurable key `"valkey"` → `"valkey_service"` (D14.9)               |
| Refactor    | `tests/unit/agent/nodes/test_generate_title.py`     | Line 71: rename key in `_make_config` helper (D14.9)                                    |
| Settings    | `src/app/config.py`                                 | Add `chat_run_timeout_seconds: int = Field(default=120, ge=10)`                         |
| Env         | `.env`, `.env.example`                              | Add `CHAT_RUN_TIMEOUT_SECONDS=120` under "Agent Behavior"                               |
| Tests       | `tests/unit/api/test_chat.py`                       | New: 13 unit tests covering status guards, atomic busy, full Path A injection, error event, finally reset, timeout, regression for D14.9, concurrent-race |
| Tests       | `tests/integration/test_chat_sse.py`                | New: 3 integration tests (token/products/done ordering, content-type, stream termination) |

---

## Verification

- `ruff check .` returns 0 errors.
- `ruff format --check .` returns 0 reformatting needed.
- `pyright` returns 0 errors.
- `uv run pytest tests/unit -q` shows **418 passing** (405 baseline + 13 new
  test_chat tests).
- `uv run pytest tests/integration/test_chat_sse.py -q` shows 3 passing.
- `pytest --cov=src/app` stays at or above the 80% threshold.
- Decision record committed before any source file.
