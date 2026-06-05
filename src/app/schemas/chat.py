"""Chat request and SSE event schemas (HTTP boundary only)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for sending a chat message (FR-001)."""

    message: str = Field(..., min_length=1, max_length=4096)
    generate_image: bool = Field(
        default=False,
        description=(
            "When True the agent attempts inline DALL-E generation "
            "if design context is available (FR-047)."
        ),
    )


class ChatChunk(BaseModel):
    """A single SSE text chunk streamed back to the client.

    Used for the ``token`` event type (FR-003).
    """

    delta: str
    done: bool = False


class UsagePayload(BaseModel):
    """Token usage and cost breakdown included in the ``done`` SSE event."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class DonePayload(BaseModel):
    """Payload of the ``done`` SSE event emitted at end of stream (FR-003).

    Fields map directly to the SSE schema in FR-003:
    ``{"run_id", "thread_id", "intent", "usage": {...}}``.
    """

    run_id: str
    thread_id: str
    intent: str | None
    usage: UsagePayload
