# Shared Resource Injection (Path A) — Phase 5 Foundation

**Version**: 5.0.0
**Date**: 2026-06-11
**Status**: Planned

## What
Extend the Path A shared-client pattern (originally designed for
Qdrant in Phase 4) to S3 and OpenAI clients. Wire long-lived
singletons onto `app.state` so Phase 14 (chat SSE) can inject them
into `config["configurable"]` and eliminate per-request client
construction. DB pool, repositories, and `ChatOpenAI` instances are
explicitly excluded from the pattern.

## Why
Phase 4 introduced Path A for `AsyncQdrantClient` to avoid the
TCP/TLS handshake cost of creating a client per request. The same
cost applies to boto3 (S3), `AsyncOpenAI` (DALL-E in Phase 13), and
`ValkeyService` (Redis pool), so the same pattern should be applied
uniformly. Establishing the convention in Phase 5 unblocks Phase 14
without further refactoring.

## How
- Add `app.state.s3 = S3Service(settings)` and call
  `await s3.ensure_bucket()` in `lifespan` (fail fast on missing
  bucket per the Infrastructure Ownership rule).
- Add `app.state.openai = AsyncOpenAI(api_key=...)` for DALL-E
  consumers (Phase 13). Stored as a bare `AsyncOpenAI` instance; no
  wrapper class.
- Close all clients in the reverse order in `lifespan` shutdown.
- Phase 14 (chat SSE) consumes these via `request.app.state` and
  injects them into `config["configurable"]` for nodes to read.

## Key Decisions
- **Decision D5.1**: Path A extended to Qdrant, S3, Valkey, OpenAI
  (DALL-E) — Avoids per-request client construction; reuses
  long-lived connection pools.
- **Decision D5.2**: `ChatOpenAI` keeps per-node pattern — In
  `langchain-openai==1.2.2`, `ChatOpenAI.__init__` does not expose a
  pre-constructed `AsyncOpenAI` through a clean parameter. The OpenAI
  SDK lazy-initialises `httpx.AsyncClient` on first use, so the
  per-call cost is bounded. Revisit only if profiling proves it is a
  bottleneck.
- **Decision D5.3**: DB pool NOT on `app.state` — Already a
  module-level singleton in `app/db/session.py` via `_asyncpg_pool`
  and `_psycopg_pool` globals. Adding to `app.state` is redundant.
- **Decision D5.4**: Repositories NOT injected via
  `config["configurable"]` — Thin asyncpg wrappers; per-request
  construction is essentially free (just `self._pool = pool`).
- **Decision D5.5**: Phase 5 only wires `app.state`; Phase 14
  injects into `config["configurable"]` — Clean separation between
  the wiring phase and the consumption phase.
- **Decision D5.6**: S3 uses boto3 sync, wrapped in
  `asyncio.to_thread` for async callers — Matches existing Celery
  pattern; `aioboto3` deferred to a later phase.
- **Decision D5.7**: `app.state.openai` is a bare `AsyncOpenAI`,
  no wrapper class — SDK already provides `aclose()`; no need for an
  extra abstraction layer.
- **Decision D5.8**: `S3Service.ensure_bucket` is `head_bucket`
  only, raises `BucketNotFoundError` — Per the Infrastructure
  Ownership rule, Terraform owns the bucket. The application must
  verify and fail fast; it must never call `create_bucket`.

## Impact
- `src/app/main.py` — `lifespan` adds S3 and OpenAI init + close
- `src/app/services/s3_service.py` — extended with `build_key`,
  `upload_image` (revised signature), `delete`, `ensure_bucket`,
  `close`, `.client` and `.bucket` properties
- `src/app/services/valkey_service.py` — extended with quota and
  pattern helpers (Path A consumers in Phase 8/13)
- `src/app/services/errors.py` — NEW; defines `BucketNotFoundError`
- `src/app/dependencies.py` — adds `OpenAIDep` and `S3Dep` for
  Phase 13/14 consumers
- `src/app/repositories/thread_repo.py` — adds `find_expired`
- `src/app/repositories/image_repo.py` — adds `delete_by_thread`,
  `list_by_message_id`, `count_by_user_date`
- `src/app/cache/keys.py` — NEW; defines `thread_list_key_builder`
- `tests/unit/services/test_s3_service.py` — NEW
- `tests/unit/services/test_valkey_service.py` — NEW
- `tests/unit/repositories/test_thread_repo.py` — extend
- `tests/unit/repositories/test_image_repo.py` — NEW
- `tests/integration/test_lifespan.py` — NEW
- `tests/integration/test_rate_limit.py` — NEW
- `tests/integration/test_thread_list_cache.py` — NEW
- `TEMP.md` — full Phase 5 implementation plan with checklist
- `docs/05-IMPLEMENTATION-PLAN.md` — Phase 5 section updated to
  reflect Path A scope

No breaking changes. All new symbols are additive.
