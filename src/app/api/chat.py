"""Chat SSE streaming endpoint — stub implementation.

Full implementation is in Phase 7.  This endpoint accepts a user
message and streams the agent response via Server-Sent Events.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.dependencies import CurrentUserDep
from app.schemas.chat import ChatRequest

router = APIRouter()


@router.post(
    "/{thread_id}/runs/stream",
    summary="Send a message and stream the agent response via SSE",
)
async def stream_run(
    thread_id: uuid.UUID,
    body: ChatRequest,
    current_user: CurrentUserDep,
) -> None:
    """Stream the agent response for a new user message (FR-001, FR-003).

    Accepts ``{message, generate_image}`` and streams 7 SSE event types:
    ``token``, ``products``, ``image_ready``, ``image_failed``,
    ``thread_title``, ``done``, ``error``.

    Returns 409 Conflict if the thread is currently ``busy`` (FR-014).

    Stub — Phase 7.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 7.",
    )
