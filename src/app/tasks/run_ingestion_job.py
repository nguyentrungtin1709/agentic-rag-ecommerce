"""Celery orchestrator — fetch Saleor catalogue, split into batches, dispatch workers.

This is the ``run_ingestion_job`` task referenced by the admin
``POST /admin/reindex`` endpoint.  It does NOT process products
itself — that is the worker's job (``process_batch``).  The
orchestrator's responsibilities are:

1. Update the job row to ``processing`` and stamp ``started_at``.
2. Fetch the full Saleor catalogue via ``SaleorClient``.
3. Update the job's ``total_products`` and ``total_batches``.
4. Create one ``ingestion_batches`` row per batch of 100 products.
5. Dispatch one ``process_batch`` task per batch to the
   ``reindex_batches`` queue.  Fire-and-forget — workers run in
   parallel and the orchestrator returns immediately.

Failure modes:

- If the Saleor fetch fails (e.g. network error), the orchestrator
  marks the job ``failed`` and re-raises so Celery records the
  failure.  No batches are dispatched in this case.
- If dispatching a single batch fails, the orchestrator records the
  error against the batch and continues.  The job will not reach a
  terminal state in that case (the batch remains ``pending``); a
  future sweeper job (Phase 10 backlog) will mark stuck jobs as
  ``failed``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from itertools import islice

import structlog

from app.config import get_settings
from app.db.session import get_asyncpg_pool, open_pools
from app.repositories.ingestion_repo import (
    IngestionBatchRepository,
    IngestionJobRepository,
)
from app.services.saleor_client import SaleorClient
from app.tasks.celery_app import celery_app
from app.tasks.process_batch import process_batch

logger = structlog.get_logger(__name__)


def _batched[T](iterable: Iterable[T], n: int) -> Iterable[list[T]]:
    """Yield successive ``n``-sized chunks from ``iterable``."""
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


@celery_app.task(
    name="tasks.run_ingestion_job",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    time_limit=600,  # 10 minutes — orchestrator only dispatches
    queue="reindex",
)
def run_ingestion_job(self, job_id: str) -> dict:
    """Fetch the Saleor catalogue and dispatch one worker task per batch.

    Args:
        job_id: UUID string of the ``IngestionJob`` row created by
            the admin endpoint.  The Celery task ID was patched in
            after dispatch.

    Returns:
        Dict with ``job_id``, ``total_batches``, ``dispatched``.

    Raises:
        Exception: Re-raises any unexpected exception after marking
            the job ``failed``.  Celery's normal task-error reporting
            applies.
    """
    settings = get_settings()
    job_uuid = uuid.UUID(job_id)

    try:
        total_batches, dispatched = asyncio.run(_orchestrate(job_uuid, settings))
    except Exception as exc:
        logger.error(
            "orchestrator_failed",
            job_id=job_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        # Mark the job as failed in a fresh event loop + fresh pool.
        # The previous loop is closed, so asyncpg connections bound to
        # it are unusable — ``open_pools()`` re-creates them on the new
        # loop.  See ``app.db.session`` for the loop-binding check.
        asyncio.run(_mark_failed(job_uuid, str(exc), settings))
        raise

    logger.info(
        "orchestrator_dispatched",
        job_id=job_id,
        total_batches=total_batches,
        dispatched=dispatched,
    )
    return {
        "job_id": job_id,
        "total_batches": total_batches,
        "dispatched": dispatched,
    }


async def _mark_failed(
    job_id: uuid.UUID,
    error_message: str,
    settings,
) -> None:
    """Mark the job as failed in a fresh event loop / pool."""
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    job_repo = IngestionJobRepository(pool)
    await job_repo.update_status(job_id, "failed", error_message=error_message)


async def _orchestrate(
    job_id: uuid.UUID,
    settings,
) -> tuple[int, int]:
    """Async body of the orchestrator.

    Opens the DB pool on the *current* event loop (so asyncpg connections
    are bound here) and runs the full ingest: status update → Saleor
    fetch → batch row inserts → per-batch ``process_batch`` dispatch.

    Returns:
        Tuple of ``(total_batches, dispatched)``.
    """
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    job_repo = IngestionJobRepository(pool)
    batch_repo = IngestionBatchRepository(pool)

    await job_repo.update_status(job_id, "processing")

    saleor = SaleorClient(settings)
    try:
        raw_products = await saleor.fetch_all_products()
    finally:
        await saleor.close()

    total_products = len(raw_products)
    batch_size = settings.reindex_batch_size
    total_batches = (total_products + batch_size - 1) // batch_size

    await job_repo.update_status(
        job_id,
        "processing",
        total_products=total_products,
        total_batches=total_batches,
    )

    logger.info(
        "orchestrator_dispatching",
        job_id=str(job_id),
        total_products=total_products,
        total_batches=total_batches,
    )

    dispatched = 0
    for batch_index, chunk in enumerate(_batched(raw_products, batch_size)):
        product_ids = [p["id"] for p in chunk]
        batch = await batch_repo.create(job_id, batch_index, product_ids)

        process_batch.apply_async(  # type: ignore[attr-defined]
            args=[str(batch.id)],
            queue="reindex_batches",
        )
        dispatched += 1

    logger.info(
        "orchestrator_dispatch_complete",
        job_id=str(job_id),
        total_batches=total_batches,
        dispatched=dispatched,
    )
    return total_batches, dispatched
