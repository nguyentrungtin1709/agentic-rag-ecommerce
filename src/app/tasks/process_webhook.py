"""Celery task — process a Saleor product lifecycle webhook event.

This task is enqueued by ``POST /webhooks/saleor`` after HMAC signature
validation.  It handles three event types:

- ``PRODUCT_CREATED``: generate embedding and upsert vector into Qdrant.
- ``PRODUCT_UPDATED``: regenerate embedding and upsert (idempotent).
- ``PRODUCT_DELETED``: delete the corresponding vector from Qdrant.

Processing is idempotent: re-processing the same event must not result
in duplicate vectors (upsert semantics for create/update).
"""

from __future__ import annotations

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="tasks.process_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    queue="webhook",
)
def process_webhook(
    self,
    event_type: str,
    product_id: str,
    product_data: dict,
) -> dict:
    """Process a single Saleor product lifecycle webhook event.

    This is a stub.  Full implementation will:
    1. Dispatch on ``event_type``:
       - ``PRODUCT_CREATED`` / ``PRODUCT_UPDATED``:
         a. Build embed text from ``product_data["name"] + product_data["description"]``.
         b. Call OpenAI embeddings API (``EMBEDDING_MODEL``) to get dense vector.
         c. Build BM25 sparse vector via FastEmbed.
         d. Upsert point into Qdrant collection (``QDRANT_COLLECTION_NAME``).
       - ``PRODUCT_DELETED``:
         a. Delete point from Qdrant by ``product_id``.
    2. Return a summary dict with ``product_id``, ``event_type``, and ``status``.

    Args:
        self: Celery task instance (for retry support).
        event_type: One of ``PRODUCT_CREATED``, ``PRODUCT_UPDATED``, ``PRODUCT_DELETED``.
        product_id: Saleor product ID string.
        product_data: Raw product payload from the webhook body.

    Returns:
        Dict with ``product_id``, ``event_type``, and ``status`` keys.
    """
    logger.info(
        "process_webhook task started (stub)",
        event_type=event_type,
        product_id=product_id,
    )
    return {"product_id": product_id, "event_type": event_type, "status": "stub"}
