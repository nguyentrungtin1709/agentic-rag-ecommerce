"""Saleor webhook request schemas (HTTP boundary only)."""

from __future__ import annotations

from pydantic import BaseModel


class SaleorWebhookPayload(BaseModel):
    """Inbound Saleor webhook event payload (FR-076, FR-077).

    Saleor sends HMAC-SHA256-signed POST requests to ``/webhooks/saleor``
    for ``PRODUCT_CREATED``, ``PRODUCT_UPDATED``, and ``PRODUCT_DELETED``
    lifecycle events.  This model is an input-only boundary type — it
    is never returned to API callers.
    """

    event: str
    payload: dict
