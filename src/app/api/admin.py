"""Admin endpoints — stub implementation.

Full implementation is in Phase 5/6.  All endpoints require
``is_staff: true`` in the JWT claims (FR-085).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import AdminDep

router = APIRouter()


@router.post(
    "/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger full Saleor → Qdrant product reindex (admin only)",
)
async def trigger_reindex(_admin: AdminDep) -> dict:
    """Enqueue a ``reindex_products`` Celery task (FR-103).

    Returns 202 Accepted immediately; the full catalog sync runs
    asynchronously in the ``reindex`` queue.

    Stub — Phase 5.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 5.",
    )


@router.get(
    "/threads",
    summary="List all threads across all users (admin only)",
)
async def list_all_threads(
    _admin: AdminDep,
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None),
) -> dict:
    """Return cursor-paginated threads for all users (FR-104).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )
