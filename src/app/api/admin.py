"""Admin endpoints — reindex job trigger and status (Phase 6).

Provides two endpoints:

- ``POST /admin/reindex`` — create a new ``IngestionJob`` and
  dispatch the ``run_ingestion_job`` orchestrator.  Returns 202
  Accepted with the new job_id (FR-103).
- ``GET /admin/reindex/{job_id}`` — return the job + per-batch
  status for operator visibility (FR-104).

Both endpoints require ``is_staff: true`` in the JWT claims
(``AdminDep``, FR-085).
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Path, status

from app.dependencies import (
    AdminDep,
    IngestionBatchRepoDep,
    IngestionJobRepoDep,
)
from app.tasks.run_ingestion_job import run_ingestion_job

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger full Saleor -> Qdrant product reindex (admin only)",
)
async def trigger_reindex(
    _admin: AdminDep,
    job_repo: IngestionJobRepoDep,
) -> dict:
    """Create a new ``IngestionJob`` and dispatch the orchestrator (FR-103).

    Returns 202 Accepted with the new job_id.  The orchestrator fetches
    the Saleor catalogue and dispatches one ``process_batch`` worker
    task per batch of 100 products.  Use ``GET /admin/reindex/{job_id}``
    to poll progress.

    The job row is created first with a placeholder ``celery_task_id``
    (we don't know the real task ID until ``.apply_async()`` returns).
    The endpoint then patches the real ID back into the row so the
    job/batch rows correlate 1:1 with their Celery tasks.

    Returns:
        Dict with ``job_id``, ``status`` (always ``processing`` after
        dispatch), and ``created_at``.
    """
    placeholder_task_id = f"pending-{uuid.uuid4()}"
    job = await job_repo.create(celery_task_id=placeholder_task_id)

    async_result = run_ingestion_job.apply_async(  # type: ignore[attr-defined]
        args=[str(job.id)],
        queue="reindex",
    )
    await job_repo.set_celery_task_id(job.id, async_result.id)

    logger.info(
        "reindex_dispatched",
        job_id=str(job.id),
        celery_task_id=async_result.id,
        admin_id=_admin.get("sub"),
    )
    return {
        "job_id": str(job.id),
        "status": "processing",
        "created_at": job.started_at.isoformat() if job.started_at else None,
    }


@router.get(
    "/reindex/{job_id}",
    summary="Get status of a reindex job (admin only)",
)
async def get_reindex_status(
    _admin: AdminDep,
    job_repo: IngestionJobRepoDep,
    batch_repo: IngestionBatchRepoDep,
    job_id: Annotated[uuid.UUID, Path(description="Ingestion job ID")],
) -> dict:
    """Return the job + per-batch status (FR-104).

    Response shape::

        {
            "job_id": "...",
            "status": "pending|processing|completed|partial_failed|failed",
            "total_products": int,
            "total_batches": int,
            "processed_count": int,
            "failed_count": int,
            "started_at": "..." | null,
            "completed_at": "..." | null,
            "error_message": "..." | null,
            "batches": [
                {
                    "batch_index": int,
                    "status": "pending|processing|done|failed",
                    "product_count": int,
                    "retry_count": int,
                    "skipped_count": int,
                    "error_type": "transient|permanent" | null,
                    "error_message": "..." | null,
                    "started_at": "..." | null,
                    "completed_at": "..." | null,
                },
                ...
            ]
        }

    Raises:
        HTTPException: 404 if the job_id is unknown.
    """
    job = await job_repo.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    batches = await batch_repo.list_by_job(job_id)
    batch_summaries = [
        {
            "batch_index": b.batch_index,
            "status": b.status,
            "product_count": len(b.product_ids),
            "retry_count": b.retry_count,
            "skipped_count": len(b.skipped_products),
            "error_type": b.error_type,
            "error_message": b.error_message,
            "started_at": b.started_at.isoformat() if b.started_at else None,
            "completed_at": (b.completed_at.isoformat() if b.completed_at else None),
        }
        for b in batches
    ]

    return {
        "job_id": str(job.id),
        "status": job.status,
        "total_products": job.total_products,
        "total_batches": job.total_batches,
        "processed_count": job.processed_count,
        "failed_count": job.failed_count,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "batches": batch_summaries,
    }


# ---------------------------------------------------------------------------
# Phase 9 endpoints (placeholders to keep the audit table accurate)
# ---------------------------------------------------------------------------


@router.get(
    "/threads",
    summary="List all threads across all users (admin only)",
)
async def list_all_threads(
    _admin: AdminDep,
    limit: int = 20,
    before: str | None = None,
) -> dict:
    """Return cursor-paginated threads for all users (FR-104).

    Stub — Phase 9.  Lives in this file to keep the admin mount
    point in one place; the real implementation will add a
    ``list_all`` method to ``ThreadRepository``.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 9.",
    )
