"""Saleor webhook receiver endpoint — stub implementation.

Full implementation is in Phase 8.  Receives HMAC-SHA256-signed product
lifecycle events from Saleor and enqueues Celery tasks for processing.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.auth.hmac_verifier import verify_webhook_signature
from app.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter()

_SIGNATURE_HEADER = "Saleor-Signature"


@router.post(
    "/saleor",
    status_code=status.HTTP_200_OK,
    summary="Receive Saleor product lifecycle webhook events",
)
async def receive_saleor_webhook(request: Request) -> dict:
    """Validate the HMAC-SHA256 signature and enqueue a Celery task.

    Handled event types: ``PRODUCT_CREATED``, ``PRODUCT_UPDATED``,
    ``PRODUCT_DELETED`` (FR-076, FR-077).

    Returns 200 immediately after validation; processing is asynchronous
    (FR-078, FR-079).  Returns 401 if the HMAC signature is invalid (FR-086).

    Stub — Phase 8 (signature validation is active; task dispatch is stubbed).
    """
    settings = get_settings()
    raw_body = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER, "")

    if not verify_webhook_signature(raw_body, signature, settings.saleor_webhook_secret):
        logger.warning("Webhook HMAC verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    logger.info("Webhook received and validated (stub — task dispatch pending Phase 8)")
    return {"status": "accepted"}
