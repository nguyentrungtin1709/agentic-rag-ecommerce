"""Thread-related request and response schemas (HTTP boundary only)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateThreadRequest(BaseModel):
    """Request body for creating a new conversation thread (FR-011).

    Thread metadata (title, status, timestamps) is managed server-side;
    no fields are required from the caller.
    """


class ThreadResponse(BaseModel):
    """Response schema for a single thread (FR-016)."""

    id: uuid.UUID
    title: str | None
    status: str
    title_generated: bool
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class ThreadListResponse(BaseModel):
    """Cursor-based paginated list of threads (FR-015).

    Pass ``next_cursor`` as the ``?before=`` query parameter to fetch
    the next page.  ``None`` means there are no more pages.
    """

    items: list[ThreadResponse]
    next_cursor: uuid.UUID | None
