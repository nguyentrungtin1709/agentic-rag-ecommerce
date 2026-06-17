"""Chat request and SSE event schemas (HTTP boundary only)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProductItem(BaseModel):
    """Product entry emitted in the ``products`` SSE event (FR-003, FR-070).

    Attributes:
        id: Saleor product ID.
        name: Product display name.
        description: Contextualized product description rewritten by
            ``ResponseGeneratorNode`` at synthesis time to fit the user's
            style context and occasion.
        price_range: Human-readable price range string, e.g. ``"100k - 250k VND"``.
        saleor_url: Storefront product URL.
        thumbnail_url: WEBP product thumbnail URL.
    """

    id: str
    name: str
    description: str = ""
    price_range: str
    saleor_url: str
    thumbnail_url: str = ""


class ChatRequest(BaseModel):
    """Request body for sending a chat message (FR-001)."""

    message: str = Field(..., min_length=1, max_length=4096)
    generate_image: bool = Field(
        default=False,
        description=(
            "When True the agent attempts inline image generation "
            "if design context is available (FR-047).  The model "
            "used is ``settings.image_generation_model`` "
            "(default ``gpt-image-2``, 16.1.0)."
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


class ProductsPayload(BaseModel):
    """Payload of the ``products`` SSE event (FR-070).

    Sent exactly once per turn, after the text stream completes, when
    ``state["retrieved_products"]`` is non-empty.  The frontend uses
    ``items`` to render the product carousel.  Emitted by the
    ``synthesize`` node (Phase 12, D12.5).
    """

    items: list[ProductItem]


class ThreadTitlePayload(BaseModel):
    """Payload of the ``thread_title`` SSE event (FR-022, FR-024).

    Emitted by the ``generate_title`` node (Phase 12, D12.8) the
    first time a thread gets a title.  Carries the final, persisted
    title — the frontend updates the sidebar / browser tab without
    a follow-up HTTP round-trip.
    """

    title: str


class ImageReadyPayload(BaseModel):
    """Payload of the ``image_ready`` SSE event (FR-053).

    Emitted by the ``generate_image`` node (Phase 13, D13.7) once
    the generated image is uploaded to S3 and the
    ``generated_images`` row is committed.  Carries the public S3
    URL and the prompt that was used to produce the image (for
    tooltips / re-roll affordances in the UI).  The model is
    ``settings.image_generation_model`` (default ``gpt-image-2``,
    16.1.0).
    """

    url: str
    prompt: str


class ImageFailedPayload(BaseModel):
    """Payload of the ``image_failed`` SSE event (FR-049).

    Emitted by the ``generate_image`` node when generation is
    requested but cannot be completed.  The frontend should clear
    any pending image placeholder and optionally surface a toast.

    Attributes:
        reason: Short machine-readable code, one of:

            - ``"generation_failed"`` — model call, base64
              decode, or S3 upload raised an exception (D13.9).
            - ``"rate_limit_exceeded"`` — Valkey daily quota
              exhausted; no LLM call is made (D13.4).
    """

    reason: str


class ErrorPayload(BaseModel):
    """Payload of the ``error`` SSE event (Phase 14, D14.6).

    Emitted by the chat endpoint when the graph task raises
    an unhandled exception.  The stream always terminates
    with one ``error`` event followed by the implicit end of
    the response (no ``done`` event is emitted on error).

    Attributes:
        code: Short machine-readable code — e.g.
            ``"internal_error"``, ``"graph_timeout"``.
        message: Human-readable, sanitised error summary.
            Never includes raw exception tracebacks, PII, or
            internal hostnames.
    """

    code: str
    message: str
