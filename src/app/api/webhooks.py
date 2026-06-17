"""Saleor webhook receiver endpoint.

Receives HMAC-SHA256-signed product lifecycle events from Saleor and
enqueues a Celery task for processing.  The endpoint returns 200 within
the 200 ms budget (NFR-003) — the only blocking work is HMAC verify
(constant-time ``hmac.compare_digest``) plus a fire-and-forget
``process_webhook.delay()``.

Handled event types (FR-076, FR-077):

- ``PRODUCT_CREATED`` — enqueue a Celery task that generates an
  embedding and upserts the product vector into Qdrant.
- ``PRODUCT_UPDATED`` — same path as ``PRODUCT_CREATED`` (idempotent
  upsert overwrites the existing point).
- ``PRODUCT_DELETED`` — enqueue a Celery task that deletes the
  product vector from Qdrant.

Authentication: every request must carry a valid ``Saleor-Signature``
header.  The HMAC-SHA256 digest of the raw body is compared to the
header value in constant time (FR-086).  A mismatch returns 401 and
the request is **not** enqueued.

Event-type discovery: with a ``subscription { event { ... } }`` root,
Saleor does **not** include the event name in the body.  It is sent
in the ``Saleor-Event`` HTTP header (the deprecated
``X-Saleor-Event`` is read as a fallback for the v3 → v4
transition).  See the *Webhook Overview* doc:
https://docs.saleor.io/developer/extending/webhooks/overview

Rate limiting: this endpoint is exempt from the global rate limiter
(FR-094 — see ``app.rate_limit._limiter.exempt``).
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from app.auth.hmac_verifier import verify_webhook_signature
from app.config import get_settings
from app.rate_limit import get_limiter
from app.schemas.webhook import SaleorProductEvent, SaleorWebhookPayload
from app.tasks.process_webhook import process_webhook

logger = structlog.get_logger(__name__)

router = APIRouter()

# Resolved at import time (see ``app/api/health.py`` for rationale).
_limiter = get_limiter()

_SIGNATURE_HEADER = "Saleor-Signature"
_EVENT_HEADER = "Saleor-Event"
_EVENT_HEADER_DEPRECATED = "X-Saleor-Event"

# Event types this endpoint knows how to dispatch.  Anything outside
# this set is rejected (400) at the boundary so we never accidentally
# dispatch an unknown event to the Celery task.  ``frozenset`` keeps
# lookups O(1) and signals immutability to readers.
_KNOWN_EVENT_TYPES: frozenset[SaleorProductEvent] = frozenset(
    {
        "PRODUCT_CREATED",
        "PRODUCT_UPDATED",
        "PRODUCT_DELETED",
    },
)


def _read_event_type(request: Request) -> str | None:
    """Read the Saleor event type from the request headers.

    Prefers the modern ``Saleor-Event`` header; falls back to the
    deprecated ``X-Saleor-Event`` for the Saleor 3.x to 4.x
    transition window.  Returns ``None`` if neither header is
    present or is empty.
    """
    return (
        request.headers.get(_EVENT_HEADER) or request.headers.get(_EVENT_HEADER_DEPRECATED) or None
    )


@router.post(
    "/saleor",
    status_code=status.HTTP_200_OK,
    summary="Receive Saleor product lifecycle webhook events",
)
@_limiter.exempt
async def receive_saleor_webhook(request: Request) -> dict:
    """Validate HMAC, parse the body, and enqueue a Celery task.

    The body must match :class:`SaleorWebhookPayload` (Saleor's real
    format ``{product: {...}}`` when the subscription uses the
    ``event { ... }`` root).  The event type is read from the
    ``Saleor-Event`` header (or its deprecated ``X-Saleor-Event``
    variant).  Any Pydantic validation failure or missing/unknown
    event header returns 400 with a generic message — Saleor does
    not read the error body but the 4xx class signals a real
    failure upstream.

    On success returns ``{"status": "accepted", "event_type": ...}``
    after dispatching ``process_webhook.delay(event_type, product_id,
    product_data)``.  Processing happens asynchronously on the
    ``webhook`` Celery queue (FR-100).

    Args:
        request: The inbound FastAPI request.  Body is read once as
            raw bytes (HMAC is computed over the literal byte stream)
            and re-parsed as JSON after verification.

    Returns:
        ``{"status": "accepted", "event_type": <event>}`` on
        success.  The status code is always 200 for a verified
        webhook — even unknown event types are accepted and ignored
        downstream by the task (it logs a warning and returns
        ``{"status": "ignored"}``).

    Raises:
        HTTPException: 401 if the ``Saleor-Signature`` header is
            missing or does not match the HMAC-SHA256 digest of the
            raw body (FR-086).
        HTTPException: 400 if the ``Saleor-Event`` header is missing,
            the body fails Pydantic validation, or the event type is
            outside the known lifecycle set.
    """
    settings = get_settings()
    start = time.monotonic()

    raw_body = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER, "")

    if not verify_webhook_signature(raw_body, signature, settings.saleor_webhook_secret):
        logger.warning("webhook_hmac_verification_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    event_type = _read_event_type(request)
    if not event_type:
        logger.warning("webhook_event_header_missing")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Saleor-Event header.",
        )

    # Canonicalise the wire value.  Saleor's ``WebhookEventAsyncType``
    # enum stores event names as lowercase ("product_updated"); the
    # rest of the codebase (task dispatch, log keys, tests) uses the
    # conventional uppercase form.  Upper-casing at the boundary
    # keeps the two decoupled — if Saleor later switches case we only
    # touch this one place.
    canonical_event_type = event_type.upper()
    if canonical_event_type not in _KNOWN_EVENT_TYPES:
        logger.warning(
            "webhook_event_header_unknown",
            event_type=event_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported event type.",
        )

    try:
        payload = SaleorWebhookPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        logger.warning(
            "webhook_payload_validation_failed",
            error=str(exc),
            payload_size_bytes=len(raw_body),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload.",
        ) from exc

    # ``product`` is Optional defensively; a missing product on a
    # create/update event is a malformed payload and we reject it.
    # On a delete event the product may be empty — we still try to
    # dispatch and let the task record an "unknown product" warning.
    product_id = payload.product.id if payload.product is not None else "unknown"
    product_data: dict = (
        payload.product.model_dump(exclude_none=True) if payload.product is not None else {}
    )

    process_webhook.delay(canonical_event_type, product_id, product_data)  # type: ignore[attr-defined]

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "webhook_received_and_dispatched",
        event_type=canonical_event_type,
        product_id=product_id,
        payload_size_bytes=len(raw_body),
        endpoint_duration_ms=duration_ms,
    )

    return {"status": "accepted", "event_type": canonical_event_type}
