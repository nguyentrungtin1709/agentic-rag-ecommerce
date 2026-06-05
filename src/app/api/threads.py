"""Thread management endpoints — stub implementation.

Full implementation is in Phase 6.  All endpoints require a valid
Saleor JWT (Bearer token) via ``CurrentUserDep``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import CurrentUserDep
from app.schemas.thread import CreateThreadRequest, ThreadListResponse, ThreadResponse

router = APIRouter()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadResponse,
    summary="Create a new conversation thread",
)
async def create_thread(
    _body: CreateThreadRequest,
    current_user: CurrentUserDep,
) -> ThreadResponse:
    """Create a new conversation thread for the authenticated user (FR-011).

    Threads are not auto-created; clients must call this endpoint first
    before sending messages.

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )


@router.get(
    "",
    response_model=ThreadListResponse,
    summary="List threads for the current user",
)
async def list_threads(
    current_user: CurrentUserDep,
    limit: int = Query(20, ge=1, le=100),
    before: uuid.UUID | None = None,
) -> ThreadListResponse:
    """Return cursor-paginated threads belonging to the caller (FR-015).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )


@router.get(
    "/{thread_id}",
    response_model=ThreadResponse,
    summary="Get thread metadata",
)
async def get_thread(
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
) -> ThreadResponse:
    """Retrieve metadata for a single thread (FR-016).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete a thread (async)",
)
async def delete_thread(
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
) -> dict:
    """Set thread status to ``deleting`` and enqueue a Celery cleanup task.

    Returns 202 Accepted immediately; cleanup runs asynchronously (FR-017).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )


@router.get(
    "/{thread_id}/history",
    summary="Get paginated message history",
)
async def get_thread_history(
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None),
) -> dict:
    """Return cursor-paginated message history from the LangGraph checkpointer.

    Messages are read from the checkpointer tables and paginated at the
    application layer (FR-019).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )
