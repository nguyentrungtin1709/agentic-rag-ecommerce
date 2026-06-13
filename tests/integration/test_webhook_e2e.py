"""End-to-end tests — ``POST /webhooks/saleor`` with a live Celery worker.

Unlike :mod:`test_webhook_dispatch` (which uses an in-process ASGI
transport and mocks ``process_webhook.delay``), these tests hit the
real running app on ``APP_TEST_URL`` and let the real Celery worker
process the task.  The result is verified by reading the Qdrant
collection directly.

Pre-requisites (see :mod:`tests.integration.conftest`)::

    docker compose up -d        # app, celery-worker, qdrant
    export SALEOR_WEBHOOK_SECRET=<matches .env>   # if .env is not loaded

If the stack is not running, all tests in this module are skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from tests.integration.conftest import APP_URL, QDRANT_URL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stack_available() -> bool:
    """Return ``True`` when the app URL responds to ``GET /health``."""
    try:
        response = httpx.get(f"{APP_URL}/health", timeout=2.0)
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


_SKIP_REASON = (
    "Docker stack not running or SALEOR_WEBHOOK_SECRET not set — skipping webhook E2E tests"
)
requires_stack = pytest.mark.skipif(
    not _stack_available() or "SALEOR_WEBHOOK_SECRET" not in os.environ,
    reason=_SKIP_REASON,
)


def _sign(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 hex digest of the body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post_webhook(
    client: httpx.AsyncClient,
    body: bytes,
    sig: str,
    event: str,
) -> httpx.Response:
    """POST a webhook to the running app with the event in the ``Saleor-Event`` header.

    ``event`` is sent in the **wire case** (lowercase) — that is what
    Saleor 3.23 actually emits (``WebhookEventAsyncType.PRODUCT_CREATED
    = "product_created"``, see
    ``saleor/webhook/event_types.py``).  The endpoint upper-cases the
    value before dispatch.
    """
    return await client.post(
        "/webhooks/saleor",
        content=body,
        headers={
            "Saleor-Signature": sig,
            "Saleor-Event": event,
            "Content-Type": "application/json",
        },
    )


def _build_payload(product_id: str, name: str) -> bytes:
    """Build a Saleor-shaped JSON body for an E2E test.

    Mirrors the production subscription query in
    ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 3 — the subscription
    uses an ``event { ... }`` root, so the body is just
    ``{"product": {...}}`` with the event type carried separately
    in the ``Saleor-Event`` HTTP header.  Pricing is included so
    the ``process_webhook`` task can build a ``ProductPayload``
    directly from the payload (A2 fast path) without falling back
    to a Saleor GraphQL fetch.  The product id is a generated UUID
    string, not a real Saleor record, so the fallback path is
    exercised by the unit tests, not here.
    """
    body = {
        "product": {
            "id": product_id,
            "name": name,
            "slug": f"e2e-{product_id[:8]}",
            "description": f"E2E test product {product_id}",
            "thumbnail": {"url": "https://cdn.example.com/e2e.webp"},
            "category": {"name": "E2E Test"},
            "collections": [{"name": "E2E"}],
            "channelListings": [],
            "media": [],
            "pricing": {
                "priceRange": {
                    "start": {"gross": {"amount": 10.0, "currency": "USD"}},
                    "stop": {"gross": {"amount": 20.0, "currency": "USD"}},
                },
            },
        },
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _build_delete_payload(product_id: str) -> bytes:
    """Build the wire shape for a ``PRODUCT_DELETED`` event.

    The production subscription in
    ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 3 selects only
    ``product { id }`` for the delete event — no ``name``, no
    pricing, no thumbnail.  The body Saleor actually sends is
    ``{"product": {"id": "..."}}``.
    """
    return json.dumps({"product": {"id": product_id}}, separators=(",", ":")).encode()


def _secret() -> str:
    """Return the webhook secret (must match the running app's secret)."""
    secret = os.environ.get("SALEOR_WEBHOOK_SECRET", "")
    assert secret, "SALEOR_WEBHOOK_SECRET not set"
    return secret


async def _wait_for_point(
    qdrant: AsyncQdrantClient,
    collection: str,
    product_id: str,
    *,
    should_exist: bool,
    timeout_s: float = 30.0,
) -> bool:
    """Poll Qdrant until a point with ``product_id`` appears (or disappears).

    Args:
        qdrant: Async Qdrant client.
        collection: Collection name.
        product_id: The product id to look for in metadata.
        should_exist: ``True`` to wait for the point to appear, ``False``
            to wait for it to be removed.
        timeout_s: Maximum wait time in seconds.

    Returns:
        ``True`` if the desired state was observed before the timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            results, _ = await qdrant.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="product_id",
                            match=MatchValue(value=product_id),
                        ),
                    ],
                ),
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            results = []
        found = len(results) > 0
        if found == should_exist:
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def qdrant() -> AsyncGenerator[AsyncQdrantClient, None]:
    """Async Qdrant client used to verify the webhook effect."""
    client = AsyncQdrantClient(url=QDRANT_URL)
    yield client
    await client.close()


@pytest.fixture
def unique_product_id() -> str:
    """A unique Saleor-style product id (base64 of ``Product:<uuid>``)."""
    raw = f"Product:{uuid.uuid4()}"
    return raw  # not base64 encoded — indexer doesn't require it


@pytest.fixture
def unique_product_name() -> str:
    """A unique product name for log/debug visibility."""
    return f"E2E Product {uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_stack
async def test_e2e_product_created_upserts_into_qdrant(
    qdrant: AsyncQdrantClient,
    unique_product_id: str,
    unique_product_name: str,
) -> None:
    """``product_created`` (wire) -> 200 -> Celery task runs -> Qdrant point exists.

    The header value uses the **wire case** (lowercase) to match what
    Saleor 3.23 actually sends.  The response ``event_type`` is the
    **canonical uppercase** form the endpoint normalises to.
    """
    secret = _secret()
    body = _build_payload(unique_product_id, unique_product_name)
    sig = _sign(body, secret)

    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        response = await _post_webhook(client, body, sig, "product_created")

    assert response.status_code == 200, response.text
    assert response.json()["event_type"] == "PRODUCT_CREATED"

    # Wait for the Celery worker to embed + upsert.  Embedding through
    # OpenAI + Qdrant upsert typically finishes in < 5s, so 30s is
    # generous.
    found = await _wait_for_point(
        qdrant,
        "products",
        unique_product_id,
        should_exist=True,
        timeout_s=30.0,
    )
    assert found, (
        f"Qdrant point for product_id={unique_product_id} was not created "
        "within 30s — check celery-worker logs"
    )


@requires_stack
async def test_e2e_product_deleted_removes_from_qdrant(
    qdrant: AsyncQdrantClient,
    unique_product_id: str,
    unique_product_name: str,
) -> None:
    """``product_created`` (wire) then ``product_deleted`` (wire) -> point removed.

    The delete body mirrors what Saleor actually sends — the
    production subscription selects only ``product { id }`` for the
    delete event, so the body has no ``name`` or pricing.
    """
    secret = _secret()
    create_body = _build_payload(unique_product_id, unique_product_name)
    delete_body = _build_delete_payload(unique_product_id)
    create_sig = _sign(create_body, secret)
    delete_sig = _sign(delete_body, secret)

    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        # 1) Create — wait for upsert.
        r1 = await _post_webhook(client, create_body, create_sig, "product_created")
        assert r1.status_code == 200, r1.text

    assert await _wait_for_point(
        qdrant,
        "products",
        unique_product_id,
        should_exist=True,
        timeout_s=30.0,
    ), "create webhook did not produce a Qdrant point"

    # 2) Delete — wait for removal.
    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        r2 = await _post_webhook(client, delete_body, delete_sig, "product_deleted")
        assert r2.status_code == 200, r2.text

    removed = await _wait_for_point(
        qdrant,
        "products",
        unique_product_id,
        should_exist=False,
        timeout_s=15.0,
    )
    assert removed, (
        f"Qdrant point for product_id={unique_product_id} was not removed "
        "within 15s of the delete webhook"
    )


@requires_stack
async def test_e2e_product_updated_is_idempotent(
    qdrant: AsyncQdrantClient,
    unique_product_id: str,
    unique_product_name: str,
) -> None:
    """Re-firing ``product_updated`` (wire) for the same id upserts the same point."""
    secret = _secret()
    body1 = _build_payload(unique_product_id, unique_product_name)
    body2 = _build_payload(unique_product_id, f"{unique_product_name} v2")
    sig1 = _sign(body1, secret)
    sig2 = _sign(body2, secret)

    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        r1 = await _post_webhook(client, body1, sig1, "product_updated")
        assert r1.status_code == 200, r1.text

    assert await _wait_for_point(
        qdrant,
        "products",
        unique_product_id,
        should_exist=True,
        timeout_s=30.0,
    )

    # Fire a second update with a different name — same product id.
    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        r2 = await _post_webhook(client, body2, sig2, "product_updated")
        assert r2.status_code == 200, r2.text

    # Give the worker a moment to process the second update; the point
    # count for this product_id must stay at exactly 1.
    await asyncio.sleep(5.0)
    results, _ = await qdrant.scroll(
        collection_name="products",
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="product_id",
                    match=MatchValue(value=unique_product_id),
                ),
            ],
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert len(results) == 1, (
        f"Expected exactly 1 Qdrant point for product_id={unique_product_id} "
        f"after two PRODUCT_UPDATED events, found {len(results)} — "
        "idempotency is broken (FR-080)"
    )
    # The name should have been updated to v2.
    payload = results[0].payload or {}
    assert "v2" in str(payload.get("name", ""))
