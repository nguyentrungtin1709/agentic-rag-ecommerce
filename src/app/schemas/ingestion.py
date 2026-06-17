"""Pydantic schemas for the admin reindex-job list endpoint (Phase 9).

Provides :class:`IngestionJobSummary` (the list view, with no
``batches[]`` detail — see ADR D9.6') and :class:`IngestionJobListResponse`
(the cursor-paginated wrapper, shape-matched to
:class:`app.schemas.thread.ThreadListResponse`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IngestionJobSummary(BaseModel):
    """Summary view of a reindex job for the list endpoint (D9.6').

    Excludes per-batch detail (``batches[]``) — operators drill
    into ``GET /admin/reindex/{job_id}`` (Phase 6) to see batch
    progress.  The summary is small enough to fit 20-50 items in
    a single response without pagination bloat.

    The ``job_id`` JSON field is the wire-format name for the
    underlying :attr:`app.models.ingestion.IngestionJob.id` UUID.
    The field's Python name is ``id`` (matching the model); the
    ``alias="job_id"`` causes Pydantic to emit ``job_id`` in JSON
    when ``by_alias=True`` is passed at dump time.  The
    ``populate_by_name=True`` flag lets the model be validated
    from either the field name or the alias — useful for tests
    that build dicts with either key.

    Attributes:
        id: UUID of the reindex job (Python name).  Serialised to
            ``job_id`` in JSON via the Pydantic alias.
        status: Lifecycle state — ``pending``, ``processing``,
            ``completed``, ``partial_failed``, or ``failed``.
        total_products: Total Saleor products in this reindex.
        total_batches: Total batches dispatched
            (``= ceil(total_products / 100)``).
        processed_count: Batches finished successfully.
        failed_count: Batches ended in ``failed``.
        started_at: When the job first moved to ``processing``.
            ``None`` while still ``pending``.
        completed_at: When the job reached a terminal state.
        error_message: Orchestrator-level error message if any.
        celery_task_id: Celery task ID of the orchestrator.
            Useful for cross-referencing with the worker's logs.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(alias="job_id")
    status: str
    total_products: int
    total_batches: int
    processed_count: int
    failed_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    celery_task_id: str


class IngestionJobListResponse(BaseModel):
    """Cursor-paginated list of reindex jobs.

    Shape matches :class:`app.schemas.thread.ThreadListResponse`
    so the admin UI can render both list endpoints with the same
    pagination component.

    Attributes:
        items: List of job summaries in newest-first order.
        next_cursor: UUID of the last item on this page if a next
            page exists (``None`` when the page was partial or
            empty).
    """

    items: list[IngestionJobSummary]
    next_cursor: uuid.UUID | None = None


__all__ = ["IngestionJobListResponse", "IngestionJobSummary"]
