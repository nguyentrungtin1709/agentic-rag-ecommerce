"""Ingestion repositories — raw SQL CRUD for jobs and batches.

Uses ``asyncpg`` directly (no ORM), matching the convention in
``thread_repo.py`` and ``image_repo.py``.

The two classes in this module (``IngestionJobRepository`` and
``IngestionBatchRepository``) are the only data-access points for the
``ingestion_jobs`` and ``ingestion_batches`` tables.  All Celery tasks
construct these directly via the ``asyncpg.Pool`` from
``app.db.session.get_asyncpg_pool()``; admin endpoints receive them via
the ``IngestionJobRepoDep`` / ``IngestionBatchRepoDep`` FastAPI
dependencies (see ``app.dependencies``).
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import structlog

from app.models.ingestion import IngestionBatch, IngestionJob

logger = structlog.get_logger(__name__)


def _coerce_batch_row(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an ``asyncpg.Record`` to a dict suitable for ``IngestionBatch``.

    asyncpg decodes JSON columns to Python ``list`` / ``dict`` natively, so
    no JSON parsing is needed.  This helper just normalises the row into a
    ``dict`` and ensures the list fields have sensible defaults.
    """
    data = dict(row)
    data.setdefault("product_ids", [])
    data.setdefault("skipped_products", [])
    return data


class IngestionJobRepository:
    """Data-access layer for the ``ingestion_jobs`` table.

    Args:
        pool: An active ``asyncpg.Pool`` instance.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, celery_task_id: str) -> IngestionJob:
        """Insert a new job row with status=pending and return it.

        Args:
            celery_task_id: The Celery task ID of the orchestrator task
                that will own this job.  Must be unique.

        Returns:
            The newly created ``IngestionJob`` (status=pending,
            counters all zero).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ingestion_jobs (celery_task_id)
                VALUES ($1)
                RETURNING id, celery_task_id, status, total_products,
                          total_batches, processed_count, failed_count,
                          started_at, completed_at, error_message
                """,
                celery_task_id,
            )
        job = IngestionJob(**dict(row))
        logger.info("ingestion_job_created", job_id=str(job.id))
        return job

    async def get(self, job_id: uuid.UUID) -> IngestionJob | None:
        """Fetch a single job by ID.

        Args:
            job_id: UUID of the job.

        Returns:
            The ``IngestionJob`` if found, else ``None``.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, celery_task_id, status, total_products,
                       total_batches, processed_count, failed_count,
                       started_at, completed_at, error_message
                FROM ingestion_jobs
                WHERE id = $1
                """,
                job_id,
            )
        return IngestionJob(**dict(row)) if row else None

    async def list_all(
        self,
        limit: int = 20,
        before: uuid.UUID | None = None,
    ) -> list[IngestionJob]:
        """Return all reindex jobs across the system, newest first.

        Admin-only counterpart for the ``GET /api/v1/admin/reindex``
        endpoint (Phase 9, decision D9.5').  No user-scoped filter —
        operators see every job across the entire deployment.

        Ordering note (D9.5'):
            ``ingestion_jobs`` has no ``created_at`` column — only
            ``started_at`` (nullable) and ``completed_at``.  We use
            ``COALESCE(started_at, 'infinity'::timestamptz)`` as the
            sort key so pending jobs (no ``started_at`` yet) sort to
            the top of the list — admin typically wants the most
            recently dispatched job (which is in ``pending`` for a
            brief moment before the worker picks it up) to appear at
            the top.  Using ``'infinity'`` means "newer than any real
            timestamp" so pending jobs get the top slot, then real
            timestamps in descending order.

        Note:
            If a cursor points at a pending job, the next page's
            ``WHERE COALESCE(...) < 'infinity'`` predicate skips
            every other pending job.  This is a documented edge case
            in D9.5' — non-disastrous because operators typically
            only need the most recent N jobs, not exact pagination
            through pending state.

        Args:
            limit: Maximum rows to return (default 20).
            before: Cursor — return jobs older than the job with this
                ID.  Pass ``None`` for the first page.

        Returns:
            List of ``IngestionJob`` domain models in newest-first order.
        """
        async with self._pool.acquire() as conn:
            if before is None:
                rows = await conn.fetch(
                    """
                    SELECT id, celery_task_id, status, total_products,
                           total_batches, processed_count, failed_count,
                           started_at, completed_at, error_message
                    FROM ingestion_jobs
                    ORDER BY COALESCE(started_at, 'infinity'::timestamptz) DESC,
                             id::text DESC
                    LIMIT $1
                    """,
                    limit,
                )
            else:
                cursor_row = await conn.fetchrow(
                    """
                    SELECT COALESCE(started_at, 'infinity'::timestamptz) AS sort_key
                    FROM ingestion_jobs
                    WHERE id = $1
                    """,
                    before,
                )
                if cursor_row is None:
                    return []
                rows = await conn.fetch(
                    """
                    SELECT id, celery_task_id, status, total_products,
                           total_batches, processed_count, failed_count,
                           started_at, completed_at, error_message
                    FROM ingestion_jobs
                    WHERE COALESCE(started_at, 'infinity'::timestamptz) < $1
                       OR (COALESCE(started_at, 'infinity'::timestamptz) = $1
                           AND id::text < $2::text)
                    ORDER BY COALESCE(started_at, 'infinity'::timestamptz) DESC,
                             id::text DESC
                    LIMIT $3
                    """,
                    cursor_row["sort_key"],
                    str(before),
                    limit,
                )
        return [IngestionJob(**dict(row)) for row in rows]

    async def set_celery_task_id(
        self,
        job_id: uuid.UUID,
        celery_task_id: str,
    ) -> None:
        """Overwrite the ``celery_task_id`` for an existing job row.

        Used by the admin endpoint which creates the job with a
        placeholder ID (since the real task ID is not known until
        ``.apply_async()`` returns) and then patches it in.

        Args:
            job_id: UUID of the job.
            celery_task_id: The real Celery task ID of the orchestrator.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE ingestion_jobs SET celery_task_id = $2 WHERE id = $1",
                job_id,
                celery_task_id,
            )

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        total_products: int | None = None,
        total_batches: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update the job's status and optional fields.

        ``started_at`` is set automatically on the ``processing``
        transition; ``completed_at`` is set on any terminal
        transition.  Each call only modifies the columns the caller
        cares about.  The query template is selected from a fixed
        dispatch table (no string concatenation of user input) so
        that the S608 linter rule stays happy.

        Args:
            job_id: UUID of the job.
            status: New status string.
            total_products: Optional new total product count.
            total_batches: Optional new total batch count.
            error_message: Optional error message (typically for the
                ``failed`` terminal state).
        """
        # Bitmask of which optional fields are set:
        #  bit 0 (1) = total_products,  bit 1 (2) = total_batches,
        #  bit 2 (4) = error_message.
        has_p = total_products is not None
        has_b = total_batches is not None
        has_e = error_message is not None
        mask = (1 if has_p else 0) | (2 if has_b else 0) | (4 if has_e else 0)

        # Timestamps:  bit 0 (1) = started,  bit 1 (2) = completed.
        started = status == "processing"
        terminal = status in ("completed", "partial_failed", "failed")
        ts_mask = (1 if started else 0) | (2 if terminal else 0)

        key = (mask, ts_mask)
        sql = _UPDATE_STATUS_SQL[key]
        args: list[Any] = [job_id, status]
        if has_p:
            args.append(total_products)
        if has_b:
            args.append(total_batches)
        if has_e:
            args.append(error_message)

        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def increment_processed(self, job_id: uuid.UUID) -> None:
        """Atomically add 1 to ``processed_count``.

        Called by workers after they finish a batch successfully.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET processed_count = processed_count + 1
                WHERE id = $1
                """,
                job_id,
            )

    async def increment_failed(self, job_id: uuid.UUID) -> None:
        """Atomically add 1 to ``failed_count``.

        Called by workers when a batch is marked permanently failed.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET failed_count = failed_count + 1
                WHERE id = $1
                """,
                job_id,
            )


# Dispatch table for ``IngestionJobRepository.update_status``.  Keyed by
# ``(optional_fields_mask, timestamps_mask)``; each value is a constant
# SQL string.  Built up-front so the per-call hot path is a single
# dict lookup with no string concatenation.
_UPDATE_STATUS_SQL: dict[tuple[int, int], str] = {
    (0, 0): ("UPDATE ingestion_jobs SET status = $2 WHERE id = $1"),
    (0, 1): ("UPDATE ingestion_jobs SET status = $2, started_at = now() WHERE id = $1"),
    (0, 2): ("UPDATE ingestion_jobs SET status = $2, completed_at = now() WHERE id = $1"),
    (0, 3): (
        "UPDATE ingestion_jobs SET status = $2, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (1, 0): ("UPDATE ingestion_jobs SET status = $2, total_products = $3 WHERE id = $1"),
    (1, 1): (
        "UPDATE ingestion_jobs SET status = $2, total_products = $3, "
        "started_at = now() "
        "WHERE id = $1"
    ),
    (1, 2): (
        "UPDATE ingestion_jobs SET status = $2, total_products = $3, "
        "completed_at = now() "
        "WHERE id = $1"
    ),
    (1, 3): (
        "UPDATE ingestion_jobs SET status = $2, total_products = $3, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (2, 0): ("UPDATE ingestion_jobs SET status = $2, total_batches = $3 WHERE id = $1"),
    (2, 1): (
        "UPDATE ingestion_jobs SET status = $2, total_batches = $3, "
        "started_at = now() "
        "WHERE id = $1"
    ),
    (2, 2): (
        "UPDATE ingestion_jobs SET status = $2, total_batches = $3, "
        "completed_at = now() "
        "WHERE id = $1"
    ),
    (2, 3): (
        "UPDATE ingestion_jobs SET status = $2, total_batches = $3, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (3, 0): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4 "
        "WHERE id = $1"
    ),
    (3, 1): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, started_at = now() "
        "WHERE id = $1"
    ),
    (3, 2): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, completed_at = now() "
        "WHERE id = $1"
    ),
    (3, 3): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (4, 0): ("UPDATE ingestion_jobs SET status = $2, error_message = $3 WHERE id = $1"),
    (4, 1): (
        "UPDATE ingestion_jobs SET status = $2, error_message = $3, "
        "started_at = now() "
        "WHERE id = $1"
    ),
    (4, 2): (
        "UPDATE ingestion_jobs SET status = $2, error_message = $3, "
        "completed_at = now() "
        "WHERE id = $1"
    ),
    (4, 3): (
        "UPDATE ingestion_jobs SET status = $2, error_message = $3, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (5, 0): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, error_message = $4 "
        "WHERE id = $1"
    ),
    (5, 1): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, error_message = $4, started_at = now() "
        "WHERE id = $1"
    ),
    (5, 2): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, error_message = $4, completed_at = now() "
        "WHERE id = $1"
    ),
    (5, 3): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, error_message = $4, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (6, 0): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_batches = $3, error_message = $4 "
        "WHERE id = $1"
    ),
    (6, 1): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_batches = $3, error_message = $4, started_at = now() "
        "WHERE id = $1"
    ),
    (6, 2): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_batches = $3, error_message = $4, completed_at = now() "
        "WHERE id = $1"
    ),
    (6, 3): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_batches = $3, error_message = $4, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
    (7, 0): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, error_message = $5 "
        "WHERE id = $1"
    ),
    (7, 1): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, error_message = $5, "
        "started_at = now() "
        "WHERE id = $1"
    ),
    (7, 2): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, error_message = $5, "
        "completed_at = now() "
        "WHERE id = $1"
    ),
    (7, 3): (
        "UPDATE ingestion_jobs SET status = $2, "
        "total_products = $3, total_batches = $4, error_message = $5, "
        "started_at = now(), completed_at = now() "
        "WHERE id = $1"
    ),
}


class IngestionBatchRepository:
    """Data-access layer for the ``ingestion_batches`` table.

    Args:
        pool: An active ``asyncpg.Pool`` instance.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        job_id: uuid.UUID,
        batch_index: int,
        product_ids: list[str],
    ) -> IngestionBatch:
        """Insert a new batch row with status=pending and return it.

        Args:
            job_id: Parent ``IngestionJob.id``.
            batch_index: Zero-based ordinal of this batch.
            product_ids: Saleor product IDs assigned to this batch.

        Returns:
            The newly created ``IngestionBatch``.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ingestion_batches
                    (job_id, batch_index, product_ids)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id, job_id, batch_index, status, product_ids,
                          skipped_products, retry_count, error_type,
                          error_message, started_at, completed_at
                """,
                job_id,
                batch_index,
                product_ids,
            )
        return IngestionBatch(**_coerce_batch_row(row))

    async def get(self, batch_id: uuid.UUID) -> IngestionBatch | None:
        """Fetch a single batch by ID.

        Args:
            batch_id: UUID of the batch.

        Returns:
            The ``IngestionBatch`` if found, else ``None``.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, job_id, batch_index, status, product_ids,
                       skipped_products, retry_count, error_type,
                       error_message, started_at, completed_at
                FROM ingestion_batches
                WHERE id = $1
                """,
                batch_id,
            )
        return IngestionBatch(**_coerce_batch_row(row)) if row else None

    async def list_by_job(self, job_id: uuid.UUID) -> list[IngestionBatch]:
        """Return all batches for a job, ordered by ``batch_index`` ASC.

        Args:
            job_id: Parent job UUID.

        Returns:
            List of ``IngestionBatch`` models in batch order.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, job_id, batch_index, status, product_ids,
                       skipped_products, retry_count, error_type,
                       error_message, started_at, completed_at
                FROM ingestion_batches
                WHERE job_id = $1
                ORDER BY batch_index ASC
                """,
                job_id,
            )
        return [IngestionBatch(**_coerce_batch_row(r)) for r in rows]

    async def mark_processing(self, batch_id: uuid.UUID) -> None:
        """Transition a batch to ``processing`` and stamp ``started_at``.

        Called by the worker at the top of ``process_batch``.

        Args:
            batch_id: UUID of the batch.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_batches
                SET status = 'processing', started_at = now()
                WHERE id = $1
                """,
                batch_id,
            )

    async def mark_done(
        self,
        batch_id: uuid.UUID,
        skipped_products: list[dict],
    ) -> None:
        """Transition a batch to ``done`` and persist skipped products.

        Args:
            batch_id: UUID of the batch.
            skipped_products: List of ``{product_id, stage, error}``
                dicts for products that failed permanently within the
                batch.  Empty list when the batch was clean.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_batches
                SET status = 'done',
                    completed_at = now(),
                    skipped_products = $2::jsonb
                WHERE id = $1
                """,
                batch_id,
                skipped_products,
            )

    async def mark_failed(
        self,
        batch_id: uuid.UUID,
        error_type: str,
        error_message: str,
        retry_count: int,
    ) -> None:
        """Transition a batch to ``failed`` and record error details.

        Args:
            batch_id: UUID of the batch.
            error_type: ``"transient"`` or ``"permanent"``.
            error_message: Final error message string.
            retry_count: Total retries consumed (0 if the batch was
                marked failed without exhausting retries).
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ingestion_batches
                SET status = 'failed',
                    completed_at = now(),
                    error_type = $2,
                    error_message = $3,
                    retry_count = $4
                WHERE id = $1
                """,
                batch_id,
                error_type,
                error_message,
                retry_count,
            )

    async def increment_retry(self, batch_id: uuid.UUID) -> int:
        """Atomically add 1 to ``retry_count`` and return the new value.

        Called each time the worker detects a transient error and
        triggers a Celery auto-retry.

        Args:
            batch_id: UUID of the batch.

        Returns:
            The new value of ``retry_count`` after the increment.
        """
        async with self._pool.acquire() as conn:
            new_count: int = await conn.fetchval(
                """
                UPDATE ingestion_batches
                SET retry_count = retry_count + 1
                WHERE id = $1
                RETURNING retry_count
                """,
                batch_id,
            )
        return new_count


__all__ = [
    "IngestionJobRepository",
    "IngestionBatchRepository",
]
