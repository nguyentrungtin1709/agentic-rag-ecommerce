"""HTTP request/response schemas for the chat and thread APIs.

These Pydantic models define the JSON contract at the HTTP boundary.
They are intentionally separate from ``app.models`` (domain entities).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ── Thread schemas ─────────────────────────────────────────────────────────


class ThreadResponse(BaseModel):
    """Response schema for a single thread."""

    id: uuid.UUID
    title: str | None
    status: str
    title_generated: bool
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class ThreadListResponse(BaseModel):
    """Cursor-based paginated list of threads (FR-015).

    The caller passes ``next_cursor`` as the ``?before=`` query parameter
    to fetch the next page.  ``None`` means there are no more pages.
    """

    items: list[ThreadResponse]
    next_cursor: uuid.UUID | None


# ── Chat message schemas ────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Request body for sending a chat message."""

    message: str = Field(..., min_length=1, max_length=4096)
    generate_image: bool = Field(
        default=False,
        description=(
            "When True the agent attempts inline DALL-E generation "
            "if design context is available (FR-047)."
        ),
    )


class ChatChunk(BaseModel):
    """A single SSE chunk streamed back to the client."""

    delta: str
    done: bool = False


# ── Webhook schemas ─────────────────────────────────────────────────────────


class WebhookEvent(BaseModel):
    """Minimal schema for a Saleor webhook event payload."""

    event: str
    payload: dict


# ── Health schemas ──────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Response schema for the health-check endpoint."""

    status: str
    checks: dict[str, bool]
