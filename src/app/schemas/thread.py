"""Thread-related request and response schemas (HTTP boundary only)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


# ---------------------------------------------------------------------------
# History endpoint — Phase 8 (FR-019, FR-020)
# ---------------------------------------------------------------------------


class HistoryImageItem(BaseModel):
    """An image generated in response to a single ``HumanMessage``.

    Attached to the ``AIMessage`` that follows the originating human
    message.  ``url`` is a presigned S3 URL (or, in tests, a placeholder
    ``https://...`` string); ``prompt`` is the DALL-E prompt that
    produced it.
    """

    url: str
    prompt: str


class HistoryMessage(BaseModel):
    """A single message in a thread's history.

    ``type`` is the literal LangChain message class name in lowercase
    (``"human"`` or ``"ai"``).  ``SystemMessage`` and ``ToolMessage``
    are filtered out at the endpoint boundary and never reach the
    client.

    ``images`` is empty for ``"human"`` messages and may contain zero
    or more entries for ``"ai"`` messages — the count is the number of
    images produced by the assistant turn that immediately follows the
    originating ``HumanMessage``.

    ``created_at`` is the timestamp LangGraph assigned to the message
    inside the agent state.  May be ``None`` for older message objects
    that pre-date the field's introduction.
    """

    id: str
    type: Literal["human", "ai"]
    content: str
    created_at: datetime | None = None
    images: list[HistoryImageItem] = Field(default_factory=list)


class ThreadHistoryResponse(BaseModel):
    """Cursor-paginated message history for a thread (FR-019, FR-020).

    Response size semantics (D8.8 / Option C — see ADR
    ``history/8_0_0_THREAD_MANAGEMENT_API.md``):

    - ``len(messages)`` MAY be LESS than ``limit`` when the requested
      page is the last one (no older messages exist before the cursor).
    - ``len(messages)`` MAY be GREATER than ``limit`` when the page
      boundary falls on an ``AIMessage``.  The handler extends the
      page backward to include the ``HumanMessage`` that opens the
      turn, so every page starts on a human message.  The extension
      is at most a few messages.
    - The frontend should treat ``limit`` as a hint, not a strict cap.
      Use ``next_cursor`` to drive the load-more trigger: a value of
      ``None`` means the history is exhausted.
    """

    messages: list[HistoryMessage]
    next_cursor: str | None
