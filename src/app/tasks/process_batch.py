"""Celery worker — process one ingestion batch (embed + upsert).

Dispatched by ``run_ingestion_job`` to the ``reindex_batches``
queue (one task per batch of 100 products).

Worker flow:

1. Load the batch row to recover ``job_id`` and ``product_ids``.
2. Mark the batch ``processing`` and stamp ``started_at``.
3. Fetch the products from Saleor by ID.
4. Build ``ProductPayload`` objects via ``SaleorClient.node_to_product_payload``.
5. Call ``ProductIndexer.index_batch(payloads)`` — returns
   ``(succeeded_count, skipped_products)``.
6. Mark the batch ``done`` with the skipped products.
7. Increment the job's ``processed_count``.
8. If this is the last batch to finish, mark the job
   ``completed`` (no failures) or ``partial_failed`` (>=1 failure).

Error handling:

- Transient errors (``openai.RateLimitError``, ``httpx.ConnectError``,
  ``qdrant_client.UnexpectedResponse``, etc.) trigger a Celery
  auto-retry (``max_retries=2``, exponential backoff with jitter).
  ``batch_repo.increment_retry`` is called before the retry.
- Permanent errors (schema, malformed data) skip the offending
  product, record it in ``skipped_products``, and continue.
- If every product in the batch fails, ``index_batch`` raises a
  ``PermanentProductError``; the worker marks the batch ``failed``
  and increments the job's ``failed_count``.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog

from app.config import get_settings
from app.db.session import get_asyncpg_pool, open_pools
from app.rag.indexer import PermanentProductError, ProductIndexer
from app.repositories.ingestion_repo import (
    IngestionBatchRepository,
    IngestionJobRepository,
)
from app.services.saleor_client import SaleorClient
from app.tasks.celery_app import celery_app
from app.utils.transient import is_transient

logger = structlog.get_logger(__name__)


async def _retry_with_retry_count(batch_id: uuid.UUID, settings) -> None:
    """Bump retry counter on the batch row (fresh event loop)."""
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    batch_repo = IngestionBatchRepository(pool)
    await batch_repo.increment_retry(batch_id)


async def _mark_batch_failed(
    batch_id: uuid.UUID,
    error_type: str,
    error_message: str,
    retry_count: int,
    settings,
) -> None:
    """Mark a batch permanently failed (fresh event loop)."""
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    batch_repo = IngestionBatchRepository(pool)
    await batch_repo.mark_failed(
        batch_id,
        error_type=error_type,
        error_message=error_message,
        retry_count=retry_count,
    )


@celery_app.task(
    name="tasks.process_batch",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    retry_backoff=True,  # exponential: 60s, 120s
    retry_backoff_max=300,  # cap at 5 min
    retry_jitter=True,  # avoid thundering herd
    time_limit=600,  # 10 min per batch attempt
    queue="reindex_batches",
    acks_late=True,
)
def process_batch(self, batch_id: str) -> dict:
    """Process one ingestion batch end-to-end.

    Args:
        batch_id: UUID string of the ``IngestionBatch`` row.

    Returns:
        Dict with ``batch_id``, ``status``, ``succeeded``,
        ``skipped`` (or ``error`` on permanent failure).
    """
    settings = get_settings()
    batch_uuid = uuid.UUID(batch_id)

    try:
        return asyncio.run(_process(self, batch_uuid, settings))
    except Exception as exc:
        if is_transient(exc):
            logger.warning(
                "batch_transient_error_will_retry",
                batch_id=batch_id,
                error_type=type(exc).__name__,
                error=str(exc),
                retries=self.request.retries,
            )
            asyncio.run(_retry_with_retry_count(batch_uuid, settings))
            raise self.retry(exc=exc) from exc

        # Permanent: mark failed, do not re-raise.
        logger.error(
            "batch_failed_permanent",
            batch_id=batch_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        asyncio.run(
            _mark_batch_failed(
                batch_uuid,
                "permanent",
                str(exc),
                self.request.retries,
                settings,
            )
        )
        return {
            "batch_id": batch_id,
            "status": "failed",
            "error_type": "permanent",
            "error": str(exc),
        }


async def _process(
    task,
    batch_id: uuid.UUID,
    settings,
) -> dict:
    """Async body of the worker — fetch, embed, upsert, persist.

    Opens the DB pool on the *current* event loop so asyncpg connections
    are bound here (not a closed loop from a previous ``asyncio.run()``).
    """
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    batch_repo = IngestionBatchRepository(pool)
    job_repo = IngestionJobRepository(pool)

    batch = await batch_repo.get(batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} not found")
    job_id = batch.job_id
    product_ids = batch.product_ids

    await batch_repo.mark_processing(batch_id)

    saleor = SaleorClient(settings)
    try:
        raw_products = await saleor.fetch_products_by_ids(product_ids)
    finally:
        await saleor.close()

    payloads = [
        SaleorClient.node_to_product_payload(p, settings.saleor_storefront_url)
        for p in raw_products
    ]

    indexer = ProductIndexer(settings)
    try:
        succeeded, skipped = await indexer.index_batch(payloads)
    except PermanentProductError:
        # Whole batch was un-indexable.  Mark failed and increment
        # the job's failed_count, then return a failure dict.  We
        # do NOT re-raise because the sync wrapper would otherwise
        # re-mark the batch as failed (double write) and treat it
        # as a generic permanent error.
        await job_repo.increment_failed(job_id)
        await batch_repo.mark_failed(
            batch_id,
            error_type="permanent",
            error_message="all products in batch failed",
            retry_count=task.request.retries,
        )
        logger.error(
            "batch_whole_batch_failed_permanent",
            batch_id=str(batch_id),
            job_id=str(job_id),
        )
        return {
            "batch_id": str(batch_id),
            "status": "failed",
            "error_type": "permanent",
            "error": "all products in batch failed",
        }

    await batch_repo.mark_done(batch_id, skipped_products=skipped)
    await job_repo.increment_processed(job_id)

    # If this is the last batch to finish, mark the job terminal.
    job = await job_repo.get(job_id)
    if job is not None and job.total_batches > 0:
        finished = job.processed_count + job.failed_count
        if finished >= job.total_batches:
            final_status = "completed" if job.failed_count == 0 else "partial_failed"
            await job_repo.update_status(job_id, final_status)
            logger.info(
                "ingestion_job_terminal",
                job_id=str(job_id),
                status=final_status,
                processed=job.processed_count,
                failed=job.failed_count,
            )

    logger.info(
        "batch_done",
        batch_id=str(batch_id),
        job_id=str(job_id),
        succeeded=succeeded,
        skipped=len(skipped),
    )
    return {
        "batch_id": str(batch_id),
        "status": "done",
        "succeeded": succeeded,
        "skipped": len(skipped),
    }
