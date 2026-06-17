"""Celery task — process a Saleor product lifecycle webhook event.

This task is enqueued by ``POST /webhooks/saleor`` after HMAC signature
validation.  It handles three event types (FR-077):

- ``PRODUCT_CREATED``: generate embedding and upsert vector into Qdrant
  (FR-078).
- ``PRODUCT_UPDATED``: regenerate embedding and upsert (idempotent —
  re-firing the same event does not duplicate the point, FR-080).
- ``PRODUCT_DELETED``: delete the corresponding vector from Qdrant
  (FR-079).

Unknown event types are logged at WARNING level and return
``{"status": "ignored"}`` without touching Qdrant — Saleor may add new
event types in minor versions and we do not want to fail the request
queue for them.

The async indexer methods are bridged into the Celery worker via
``asyncio.run()`` — the same pattern used by ``process_batch``.  The
DB pool is **not** needed here: webhook events carry the full
product data in the request body, so the task does not need to
re-fetch from PostgreSQL or Saleor.

Error handling
--------------

- Transient errors (``openai.RateLimitError``, ``httpx.ConnectError``,
  ``qdrant_client.UnexpectedResponse``, etc.) trigger a Celery
  auto-retry with exponential backoff and jitter (max 3 attempts,
  capped at 60 s).
- Permanent errors (bug, malformed data) log at ERROR and return
  ``{"status": "failed", "error_type", "error"}`` without re-raising.
  Re-raising a permanent error would consume the retry budget and
  bury the real cause in a worker log.

Idempotency
-----------

The task relies on :func:`app.rag.indexer.to_qdrant_point_id` (UUID v5
over the Saleor product ID) to make upserts deterministic.  Re-firing
``PRODUCT_UPDATED`` for the same product id upserts the same Qdrant
point in place rather than creating a duplicate (FR-080, NFR-013).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from app.config import Settings, get_settings
from app.rag.indexer import PermanentProductError, ProductIndexer, to_qdrant_point_id
from app.services.saleor_client import SaleorClient
from app.tasks.celery_app import celery_app
from app.utils.transient import is_transient

logger = structlog.get_logger(__name__)


# Recognised event types.  Anything else is logged and ignored.
_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {"PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"},
)


async def _run_upsert(
    product_data: dict[str, Any],
    product_id: str,
    settings: Settings,
) -> str:
    """Embed and upsert a single product via the indexer.

    Strategy is **A2 (payload-first with Saleor fallback)**: try to
    build a :class:`ProductPayload` directly from the webhook body.
    If the payload is missing pricing (e.g. the Saleor subscription
    query was trimmed, or the field is ``null`` for the specific
    product), fall back to a canonical
    :meth:`SaleorClient.fetch_product_by_id` call before upserting.
    The fallback path is the defensive net — in normal operation the
    subscription query in
    ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` (Step 3) provides full data
    and no extra HTTP call is made.

    Args:
        product_data: Raw ``data.object`` dict from the webhook body.
        product_id: Saleor product id (used for the fallback fetch
            and for logging).
        settings: Application settings — Saleor URL, OpenAI key,
            Qdrant config.

    Returns:
        The Saleor product id of the upserted product.

    Raises:
        PermanentProductError: When the product is not found in
            Saleor after the fallback fetch.  This is treated as a
            permanent failure by the task wrapper (no retry).
    """
    payload = SaleorClient.webhook_object_to_product_payload(
        product_data,
        settings.saleor_storefront_url,
    )
    if payload is None:
        # Payload lacks pricing — fall back to a canonical Saleor
        # fetch.  This is a safety net for subscription query
        # changes; in steady state the payload already has full
        # data and this branch is never taken.
        saleor = SaleorClient(settings)
        try:
            node = await saleor.fetch_product_by_id(product_id)
        finally:
            await saleor.close()
        if node is None:
            raise PermanentProductError(
                f"Product {product_id} not found in Saleor after webhook "
                "(no data in payload and no record in Saleor API)",
            )
        payload = SaleorClient.node_to_product_payload(
            node,
            settings.saleor_storefront_url,
        )
        logger.info(
            "webhook_payload_incomplete_fell_back_to_saleor",
            product_id=product_id,
        )

    indexer = ProductIndexer(settings)
    await indexer.index_batch([payload])
    logger.info("product_upserted", product_id=payload.product_id)
    return payload.product_id


async def _run_delete(product_id: str, settings: Settings) -> None:
    """Delete the Qdrant point for a product id (idempotent no-op).

    If the point does not exist, the underlying Qdrant client returns
    a 4xx that is treated as a permanent error and logged — it does
    not block the webhook queue.

    Args:
        product_id: Saleor product id (the ``id`` field from the
            webhook body).
        settings: Application settings.
    """
    indexer = ProductIndexer(settings)
    await indexer.delete_product(product_id)


@celery_app.task(
    name="tasks.process_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    retry_backoff=True,  # exponential: 5s, 10s, 20s
    retry_backoff_max=60,  # cap at 60s
    retry_jitter=True,  # avoid thundering herd
    time_limit=60,  # 60s per attempt — see ADR decision 4
    acks_late=True,  # redeliver if worker is killed mid-task
    queue="webhook",
)
def process_webhook(
    self,
    event_type: str,
    product_id: str,
    product_data: dict[str, Any],
) -> dict[str, Any]:
    """Process a single Saleor product lifecycle webhook event.

    Args:
        self: Celery task instance (bound for ``self.retry``).
        event_type: One of ``PRODUCT_CREATED``, ``PRODUCT_UPDATED``,
            ``PRODUCT_DELETED``.  Any other value is logged and
            ignored.
        product_id: Saleor product id extracted from the webhook
            body (``data.object.id``).  For defensive parsing,
            ``"unknown"`` if the object was missing entirely.
        product_data: The full product dict (``data.object``).  Only
            used for upsert events; ignored for delete / unknown.

    Returns:
        A dict with at least ``product_id``, ``event_type``, and
        ``status`` (one of ``upserted``, ``deleted``, ``ignored``,
        ``failed``).  Successful upserts also include
        ``qdrant_point_id``; failures include ``error_type`` and
        ``error``.
    """
    settings = get_settings()
    start = time.monotonic()

    logger.info(
        "webhook_task_started",
        event_type=event_type,
        product_id=product_id,
        attempt=self.request.retries + 1,
    )

    # ── Dispatch ────────────────────────────────────────────────────────
    try:
        if event_type in ("PRODUCT_CREATED", "PRODUCT_UPDATED"):
            asyncio.run(_run_upsert(product_data, product_id, settings))
            status_value = "upserted"
        elif event_type == "PRODUCT_DELETED":
            asyncio.run(_run_delete(product_id, settings))
            status_value = "deleted"
        else:
            logger.warning(
                "webhook_unknown_event_type",
                event_type=event_type,
                product_id=product_id,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                "product_id": product_id,
                "event_type": event_type,
                "status": "ignored",
                "duration_ms": duration_ms,
            }
    except Exception as exc:
        if is_transient(exc):
            logger.warning(
                "webhook_transient_error_will_retry",
                event_type=event_type,
                product_id=product_id,
                error_type=type(exc).__name__,
                error=str(exc),
                retries=self.request.retries,
            )
            # Celery handles the actual delay; we re-raise with
            # ``self.retry`` so the worker records a retryable failure.
            raise self.retry(exc=exc) from exc

        # Permanent error — log and return a structured failure.
        # Do NOT re-raise: a real bug should not consume the retry
        # budget, and the caller already has the 2xx response from
        # the webhook endpoint.
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "webhook_task_failed_permanent",
            event_type=event_type,
            product_id=product_id,
            error_type=type(exc).__name__,
            error=str(exc),
            duration_ms=duration_ms,
        )
        return {
            "product_id": product_id,
            "event_type": event_type,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "duration_ms": duration_ms,
        }

    # ── Success ─────────────────────────────────────────────────────────
    duration_ms = int((time.monotonic() - start) * 1000)
    result: dict[str, Any] = {
        "product_id": product_id,
        "event_type": event_type,
        "status": status_value,
        "duration_ms": duration_ms,
    }
    if event_type in ("PRODUCT_CREATED", "PRODUCT_UPDATED"):
        result["qdrant_point_id"] = to_qdrant_point_id(product_id)

    logger.info(
        "webhook_task_completed",
        event_type=event_type,
        product_id=product_id,
        status=status_value,
        duration_ms=duration_ms,
    )
    return result
