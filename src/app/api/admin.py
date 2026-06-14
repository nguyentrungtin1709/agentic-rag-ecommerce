"""Admin endpoints — reindex job trigger/status (Phase 6) + threads + jobs lists (Phase 9).

Provides five endpoints:

- ``POST /admin/reindex`` — create a new ``IngestionJob`` and
  dispatch the ``run_ingestion_job`` orchestrator.  Returns 202
  Accepted with the new job_id (FR-103, Phase 6).
- ``GET /admin/reindex`` — list all reindex jobs with summary
  fields, cursor-paginated (Phase 9, D9.5' + D9.6').
- ``GET /admin/reindex/{job_id}`` — return the job + per-batch
  status for operator visibility (FR-104, Phase 6).
- ``GET /admin/threads`` — list all threads across all users,
  cursor-paginated (Phase 9, D9.4 + D9.5).

All endpoints require ``is_staff: true`` in the JWT claims
(``AdminDep``, FR-085).
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from app.dependencies import (
    AdminDep,
    IngestionBatchRepoDep,
    IngestionJobRepoDep,
    ThreadRepoDep,
)
from app.rate_limit import get_limiter
from app.schemas.ingestion import IngestionJobListResponse, IngestionJobSummary
from app.schemas.thread import ThreadListResponse, ThreadResponse
from app.tasks.run_ingestion_job import run_ingestion_job

logger = structlog.get_logger(__name__)

router = APIRouter()
_limiter = get_limiter()


# ---------------------------------------------------------------------------
# Phase 6: reindex job trigger and detail
# ---------------------------------------------------------------------------


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
# Phase 9: list endpoints (real implementations)
# ---------------------------------------------------------------------------


@_limiter.limit("60/minute")
@router.get(
    "/reindex",
    response_model=IngestionJobListResponse,
    response_model_by_alias=True,
    summary="List all reindex jobs (admin only)",
)
async def list_reindex_jobs(
    request: Request,  # noqa: ARG001  -- slowapi needs request in signature
    _admin: AdminDep,
    job_repo: IngestionJobRepoDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: uuid.UUID | None = None,
) -> IngestionJobListResponse:
    """Return cursor-paginated reindex jobs for operator visibility.

    Closes the REST gap: ``POST /admin/reindex`` creates a job,
    ``GET /admin/reindex/{job_id}`` fetches one with batch detail;
    this endpoint lists all jobs with summary fields only (D9.6').

    Pending jobs (no ``started_at`` yet) sort to the top of the
    first page thanks to the ``COALESCE(started_at, 'infinity')``
    sort key — see D9.5'.  Use cases:

        - "When did the last reindex complete?"
        - "Is the reindex I triggered an hour ago still running?"
        - "Which reindex jobs failed last week?"

    Args:
        _admin: JWT-validated admin claim.
        job_repo: Pool-scoped ingestion job repository.
        limit: Page size (1-100, default 20).
        before: Cursor — return jobs older than this job ID.
            ``None`` for the first page.

    Returns:
        :class:`IngestionJobListResponse` with ``items`` and
        ``next_cursor``.  ``batches[]`` is intentionally excluded
        (D9.6') — drill into ``GET /admin/reindex/{job_id}``.
    """
    rows = await job_repo.list_all(limit=limit, before=before)
    next_cursor = rows[-1].id if len(rows) == limit else None
    logger.info(
        "Admin listed reindex jobs",
        admin_id=_admin.get("sub"),
        count=len(rows),
        has_next=next_cursor is not None,
    )
    return IngestionJobListResponse(
        items=[IngestionJobSummary.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


@_limiter.limit("60/minute")
@router.get(
    "/threads",
    response_model=ThreadListResponse,
    summary="List all threads across all users (admin only)",
)
async def list_all_threads(
    request: Request,  # noqa: ARG001  -- slowapi needs request in signature
    _admin: AdminDep,
    thread_repo: ThreadRepoDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: uuid.UUID | None = None,
) -> ThreadListResponse:
    """Return cursor-paginated threads for all users (FR-104, D9.4).

    Same response shape as ``GET /api/v1/threads`` (the user-facing
    list) but without the ``user_id`` filter.  Operators use this
    to monitor activity, audit abuse, and respond to support
    tickets.  Rate limit: 60/min (D9.8).

    Args:
        _admin: JWT-validated admin claim.
        thread_repo: Pool-scoped thread repository.
        limit: Page size (1-100, default 20).
        before: Cursor — return threads older than this thread ID.
            ``None`` for the first page.

    Returns:
        :class:`ThreadListResponse` with ``items`` and
        ``next_cursor`` (UUID or ``None``).
    """
    rows = await thread_repo.list_all(limit=limit, before=before)
    next_cursor = rows[-1].id if len(rows) == limit else None
    logger.info(
        "Admin listed threads",
        admin_id=_admin.get("sub"),
        count=len(rows),
        has_next=next_cursor is not None,
    )
    return ThreadListResponse(
        items=[ThreadResponse.model_validate(r, from_attributes=True) for r in rows],
        next_cursor=next_cursor,
    )
