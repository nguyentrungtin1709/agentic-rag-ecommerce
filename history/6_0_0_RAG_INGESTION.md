# RAG Ingestion (Orchestrator + Worker)

**Version**: 6.0.0
**Date**: 2026-06-12
**Status**: Completed

## What
Implement the full Saleor -> Qdrant ingestion pipeline using a two-task design
(orchestrator + per-batch worker) backed by PostgreSQL for job/batch tracking.
Replace the `ProductIndexer` stub with a real implementation, expose two admin
endpoints to trigger and inspect reindex jobs, and delete the deprecated
`ProductRetriever` (Phase 4 product_rag subagent is the single retrieval path).

## Why
- Phases 1-5 left a stub `ProductIndexer`; the Qdrant collection is empty so the
  product_rag subagent has nothing to retrieve against. The application is not
  functional end-to-end until this lands.
- Operator visibility: a single Celery task that fetches the entire Saleor
  catalogue and embeds it is opaque. Two-task design + DB tracking gives the
  operator real-time progress and a recoverable audit trail.
- One bad product must not fail an entire batch. Per-product skip on permanent
  errors + Celery auto-retry on transient errors gives operational resilience.

## How
- **Two-task design** — `run_ingestion_job` (orchestrator, queue=`reindex`)
  fetches the catalogue, splits into batches of 100, and dispatches one
  `process_batch` task per batch (queue=`reindex_batches`). Workers run in
  parallel; orchestrator returns immediately after dispatch.
- **PostgreSQL tracking** — two new tables: `ingestion_jobs` (one row per
  orchestrator run) and `ingestion_batches` (N rows per job, one per batch).
  Status state machines: job = `pending` -> `processing` -> `completed` /
  `partial_failed` / `failed`; batch = `pending` -> `processing` -> `done` /
  `failed`.
- **Two-track description rule** — full cleaned description stored in
  `metadata['description']`; only the bounded summary (or the short original)
  is embedded (FR-035 + FR-035a).
- **Parallel LLM summarization** — `asyncio.gather` + `asyncio.Semaphore`
  bound the concurrency to `DESCRIPTION_SUMMARIZE_CONCURRENCY` (default 10)
  to respect OpenAI RPM/TPM. Short descriptions skip the LLM entirely.
- **Transient vs permanent classification** — a whitelist of library
  exception classes (`openai.RateLimitError`, `httpx.ConnectError`, etc.)
  drives Celery auto-retry (max 2 attempts, exponential backoff with jitter).
  All other exceptions are treated as permanent: mark batch failed, continue.
- **Idempotency** — `TextNode.id_ = product.product_id` makes Qdrant upserts
  idempotent (FR-080). `process_batch` after partial success redelivers the
  same node IDs.
- **Path A** — admin endpoint uses `IngestionJobRepoDep` /
  `IngestionBatchRepoDep` (FastAPI Depends); Celery tasks use
  `get_asyncpg_pool()` directly (run outside the FastAPI app context).

## Key Decisions
- Decision 1: Two-task design (orchestrator + worker) over a single monolithic
  task — better parallelism, real-time progress visibility, scalable throughput
  with worker count.
- Decision 2: PostgreSQL tracking over Redis TTL — persistent state survives
  worker crash; errors are queryable.
- Decision 3: Per-product skip on permanent error over batch-fail — one bad
  product must not block 99 valid ones.
- Decision 4: Library exception whitelist over custom exception classes — less
  indirection, no extra layer to maintain.
- Decision 5: Parallel LLM summarization via `asyncio.gather` + bounded
  `asyncio.Semaphore` (no caching) — sequential LLM calls would bottleneck
  a 100-product batch. Caching deferred because product descriptions are
  mutable in Saleor and a stale cache would feed wrong summaries to Qdrant.
- Decision 6: `ProductRetriever` deleted — Phase 4 product_rag subagent is
  the single source of truth for retrieval.
- Decision 7: `SaleorClient.fetch_products_by_ids` added — the worker
  previously would re-fetch the full catalogue and filter; wasteful. The
  new helper fetches a single GraphQL page filtered by ID set.

## Impact
- **New files**: 1 alembic migration, 1 Pydantic model file, 1 repo file
  (2 classes), 1 text cleaning module, 1 prompt file, 2 Celery task files,
  6 test files, 1 integration test file.
- **Modified files**: `src/app/dependencies.py` (2 new factories),
  `src/app/config.py` (2 new settings), `src/app/tasks/celery_app.py`
  (2 new tasks + 1 new queue), `src/app/api/admin.py` (real endpoints),
  `src/app/rag/indexer.py` (real implementation), `src/app/services/saleor_client.py`
  (1 new helper), `.env` + `.env.example` (2 new vars), `tests/integration/test_qdrant.py`
  (vector name fixup + 1 new test).
- **Deleted files**: `src/app/rag/retriever.py`.
- **No breaking API changes** to client-visible endpoints.
- **No new external dependencies** (outside `fastembed`, which was
  already a transitive dep of `qdrant-client[fastembed]` and is now
  pinned in `pyproject.toml`).

## Validation

End-to-end smoke test against the running Docker stack on 2026-06-12/13:

- Admin token obtained via `mutation { tokenCreate(...) }` from
  `http://localhost:8000/graphql/` with the seeded admin credentials
  (`admin@gmail.com / Admin##123`).
- `POST /api/v1/admin/reindex` returned `202 Accepted` with a new
  `job_id`.
- Orchestrator fetched 32 products from Saleor, created 1 batch row,
  dispatched 1 `process_batch` worker task.
- Worker fetched the 32 products by ID, summarised descriptions
  where needed, embedded with OpenAI (1536-d dense) + FastEmbed BM25
  (sparse), and upserted to the Qdrant `products` collection.
- Final state: `ingestion_jobs.status=completed`,
  `processed_count=1, failed_count=0`,
  `ingestion_batches[0].status=done, skipped_products=[]`,
  Qdrant `points_count=32, indexed_vectors_count=32, status=green`.
- Second smoke test confirmed idempotency: a fresh `job_id`
  re-fetched and re-indexed 32 products, all upserts of existing
  IDs.
- 298 unit + integration tests pass; `ruff check` clean; `pyright`
  reports 0 errors.  Phase 6 modules collectively at 98% coverage
  (see `temp/phase-6-rag-ingestion.md` for the per-module breakdown).

## Issues Resolved During Validation

Captured in detail in `ERROR.md`:

1. **JWT `iss` mismatch + missing `sub`** -- Saleor tokens use a
   user-facing `iss` URL and a custom `user_id` field instead of
   the standard `sub`.  Fixed via a new `saleor_jwt_issuer` setting
   and a `user_id` -> `sub` shim in the verifier.
2. **Saleor `DisallowedHost` for `host.docker.internal`** -- the
   app container reaches Saleor via `host.docker.internal:8000`,
   which was not in Saleor's `ALLOWED_HOSTS`.  Fixed in
   `saleor-platform/docker-compose.yml:25`.
3. **`asyncpg pool is not initialised` in the Celery worker** --
   the worker is a separate process and never ran the FastAPI
   lifespan.  Fixed by having tasks open the pool on their own
   loop in `app.db.session.open_pools`.
4. **`got Future ... attached to a different loop`** -- asyncpg
   connections are bound to the loop they were created on, but
   `asyncio.run()` creates a fresh loop on every call.  Fixed by
   making `open_pools` / `close_pools` loop-aware.
5. **`IngestionBatch.product_ids` validation error** -- the
   repository was calling `json.dumps(list)` before passing to
   asyncpg, and the codec re-serialised.  Double-encoded JSON.
   Fixed by removing the `json.dumps()` calls in
   `IngestionBatchRepository.create` and `mark_done`; the asyncpg
   codec handles serialisation directly.
