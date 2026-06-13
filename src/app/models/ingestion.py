"""Domain models for ingestion job and batch tracking.

Maps 1:1 to the ``ingestion_jobs`` and ``ingestion_batches`` tables
created in alembic migration ``0002_ingestion_tracking.py``.

These models are returned by the repository layer (Phase 6) and
serialised into the ``GET /admin/reindex/{job_id}`` response payload.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class IngestionJob(BaseModel):
    """A reindex run — one row per Celery orchestrator task.

    Attributes:
        id: UUID primary key.
        celery_task_id: Celery task ID of the orchestrator task that
            owns this job.  Unique across all jobs.
        status: Lifecycle state — ``pending``, ``processing``,
            ``completed``, ``partial_failed``, or ``failed``.
        total_products: Total Saleor products in this reindex.
        total_batches: Total batches dispatched (= ceil(total_products / 100)).
        processed_count: Number of batches that finished successfully.
        failed_count: Number of batches that ended in ``failed``.
        started_at: Timestamp when the orchestrator moved the job to
            ``processing``.  ``None`` if the job is still ``pending``.
        completed_at: Timestamp when the job reached a terminal state
            (``completed`` / ``partial_failed`` / ``failed``).  ``None``
            while the job is still in flight.
        error_message: Orchestrator-level error message.  Set only when
            ``status == 'failed'`` (e.g. Saleor unreachable).
    """

    id: uuid.UUID
    celery_task_id: str
    status: str = "pending"
    total_products: int = 0
    total_batches: int = 0
    processed_count: int = 0
    failed_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class IngestionBatch(BaseModel):
    """One batch within a job — one row per Celery worker task.

    Attributes:
        id: UUID primary key.
        job_id: Parent ``IngestionJob.id`` (CASCADE on delete).
        batch_index: Zero-based ordinal of this batch in the job.
        status: Lifecycle state — ``pending``, ``processing``, ``done``,
            or ``failed``.
        product_ids: List of Saleor product IDs in this batch.
        skipped_products: Per-product skip records.  Each entry is a
            ``{product_id, stage, error}`` dict.  ``stage`` is either
            ``"cleaning"`` or ``"summarization"``.
        retry_count: Number of Celery auto-retries consumed for this
            batch (transient errors only).
        error_type: ``"transient"`` or ``"permanent"`` for failed batches.
            ``None`` for batches that have not failed.
        error_message: Final error message on permanent failure.
        started_at: Timestamp when the worker first picked the batch up.
        completed_at: Timestamp when the batch reached a terminal state.
    """

    id: uuid.UUID
    job_id: uuid.UUID
    batch_index: int
    status: str = "pending"
    product_ids: list[str] = Field(default_factory=list)
    skipped_products: list[dict] = Field(default_factory=list)
    retry_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
