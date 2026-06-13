"""Integration tests — ``POST /webhooks/saleor`` dispatch behaviour.

Verifies the FastAPI router, the HMAC verifier, the Pydantic schema
validation, and the ``process_webhook.delay`` enqueue.  The Celery
worker is **not** exercised here — that path is covered by
``test_webhook_e2e.py``.

The tests run **in-process** against a minimal FastAPI app that
mounts only the webhooks router.  This is the same approach used by
``test_rate_limit.py`` for its in-process slowapi tests, and it lets
us patch ``process_webhook.delay`` so no Celery message is sent.

These tests do NOT need the full Docker stack to be running.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.webhooks import router as webhooks_router
from app.config import Settings
from app.rate_limit import get_limiter

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 hex digest of the body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(event: str, product_id: str = "prod-test-123", **extra: object) -> bytes:
    """Build a Saleor-shaped JSON body for a given event type.

    Mirrors the production subscription query in
    ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 3 — the subscription
    uses an ``event { ... }`` root, so Saleor emits the inner
    selection set directly.  The body is just ``{"product": {...}}``
    with the event type carried separately in the ``Saleor-Event``
    HTTP header.
    """
    body_dict: dict = {
        "product": {
            "id": product_id,
            "name": "Test Tee",
            "description": "A test product",
            "thumbnail": {"url": "https://cdn.example.com/tee.webp"},
            "category": {"name": "Apparel"},
            "collections": [],
            "channelListings": [],
            "media": [],
        },
    }
    body_dict["product"].update(extra)
    return json.dumps(body_dict, separators=(",", ":")).encode()


def _delete_payload(product_id: str) -> bytes:
    """Build a Saleor-shaped body for ``PRODUCT_DELETED``.

    The production subscription in
    ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` (Step 3) selects only
    ``product { id }`` for the delete event — no ``name``, no pricing,
    no thumbnail.  The body Saleor actually sends is just
    ``{"product": {"id": "..."}}``.
    """
    return json.dumps({"product": {"id": product_id}}, separators=(",", ":")).encode()


async def _post(client: AsyncClient, body: bytes, sig: str, event: str) -> httpx.Response:
    """POST a webhook with the event carried in the ``Saleor-Event`` header."""
    return await client.post(
        "/webhooks/saleor",
        content=body,
        headers={
            "Saleor-Signature": sig,
            "Saleor-Event": event,
            "Content-Type": "application/json",
        },
    )


_WEBHOOK_SECRET = "test-secret-32-chars-minimum-abc-test-secret-32-chars-minimum-abc"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a minimal FastAPI app that mounts only the webhooks router.

    The app includes the rate-limiter state because the webhook
    route is decorated with ``@_limiter.exempt`` (FR-094) and the
    exempt marker is registered at import time.  We import the
    real router so the same code path is exercised in production.

    The endpoint calls ``get_settings()`` directly (not via
    ``Depends``), so we patch the symbol it imported rather than
    using ``app.dependency_overrides`` — this keeps the
    production code untouched.
    """
    test_settings = Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        openai_api_key="test-openai-key-not-used",
        saleor_webhook_secret=_WEBHOOK_SECRET,
    )
    monkeypatch.setattr("app.api.webhooks.get_settings", lambda: test_settings)
    app = FastAPI()
    app.state.limiter = get_limiter()
    app.include_router(webhooks_router, prefix="/webhooks")
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client bound to the in-process test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_celery_delay() -> Generator[MagicMock, None, None]:
    """Patch ``process_webhook.delay`` so no Celery message is sent.

    The endpoint imports the ``process_webhook`` symbol from
    ``app.tasks.process_webhook``; we patch the same attribute on
    the same module so the endpoint's call resolves to the mock.
    """
    with patch("app.api.webhooks.process_webhook.delay") as fake_delay:
        yield fake_delay


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_webhook_valid_hmac_dispatches_celery_task(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """``PRODUCT_CREATED`` with valid HMAC returns 200 and enqueues the task."""
    body = _payload("PRODUCT_CREATED", "prod-create-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "PRODUCT_CREATED")

    assert response.status_code == 200
    body_json = response.json()
    assert body_json["status"] == "accepted"
    assert body_json["event_type"] == "PRODUCT_CREATED"

    mock_celery_delay.assert_called_once()
    call_args = mock_celery_delay.call_args
    # process_webhook.delay(event_type, product_id, product_data)
    assert call_args.args[0] == "PRODUCT_CREATED"
    assert call_args.args[1] == "prod-create-1"
    assert call_args.args[2]["name"] == "Test Tee"


async def test_webhook_valid_hmac_for_product_updated_dispatches(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """``PRODUCT_UPDATED`` with valid HMAC enqueues the task (FR-078)."""
    body = _payload("PRODUCT_UPDATED", "prod-update-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "PRODUCT_UPDATED")

    assert response.status_code == 200
    assert response.json()["event_type"] == "PRODUCT_UPDATED"
    mock_celery_delay.assert_called_once()
    assert mock_celery_delay.call_args.args[0] == "PRODUCT_UPDATED"


async def test_webhook_valid_hmac_for_product_deleted_dispatches_with_correct_id(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """``PRODUCT_DELETED`` enqueues with the product id from the body (FR-079)."""
    body = _payload("PRODUCT_DELETED", "prod-delete-99")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "PRODUCT_DELETED")

    assert response.status_code == 200
    mock_celery_delay.assert_called_once()
    call_args = mock_celery_delay.call_args
    assert call_args.args[0] == "PRODUCT_DELETED"
    assert call_args.args[1] == "prod-delete-99"


async def test_webhook_product_deleted_accepts_id_only_wire_shape(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """``PRODUCT_DELETED`` body is ``{"product": {"id": "..."}}`` in production.

    The subscription in ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 3
    selects only ``product { id }`` for ``ProductDeleted`` — no
    ``name``, no pricing.  ``ProductObject.name`` must be optional
    for the schema to accept this wire shape.  See
    ``history/7_0_0_WEBHOOK_HANDLING.md`` "Live-wire fix #3".
    """
    body = _delete_payload("prod-delete-wire-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "product_deleted")

    assert response.status_code == 200
    assert response.json()["event_type"] == "PRODUCT_DELETED"
    mock_celery_delay.assert_called_once()
    call_args = mock_celery_delay.call_args
    assert call_args.args[0] == "PRODUCT_DELETED"
    assert call_args.args[1] == "prod-delete-wire-1"


async def test_webhook_accepts_deprecated_x_saleor_event_header(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """The deprecated ``X-Saleor-Event`` header is honoured as a fallback."""
    body = _payload("PRODUCT_UPDATED", "prod-fallback-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await client.post(
        "/webhooks/saleor",
        content=body,
        headers={
            "Saleor-Signature": sig,
            "X-Saleor-Event": "PRODUCT_UPDATED",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["event_type"] == "PRODUCT_UPDATED"
    mock_celery_delay.assert_called_once()


async def test_webhook_canonicalises_lowercase_wire_event_to_uppercase(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """Saleor 3.23 emits ``product_updated`` (lowercase) in the header.

    Verified against ``saleor/webhook/event_types.py`` (the
    ``WebhookEventAsyncType`` enum stores ``"product_updated"`` etc.).
    The endpoint upper-cases the value at the boundary so the task
    dispatch and log keys stay in the conventional ``PRODUCT_UPDATED``
    form.  Both the response and the Celery task call must use the
    canonical uppercase form.
    """
    body = _payload("PRODUCT_UPDATED", "prod-wire-case-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "product_updated")

    assert response.status_code == 200
    assert response.json()["event_type"] == "PRODUCT_UPDATED"

    mock_celery_delay.assert_called_once()
    assert mock_celery_delay.call_args.args[0] == "PRODUCT_UPDATED"


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------


async def test_webhook_invalid_hmac_returns_401(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """Signature signed with the wrong secret returns 401 (FR-086)."""
    body = _payload("PRODUCT_CREATED", "prod-1")
    sig = _sign(body, "wrong-secret-32-chars-minimum-abc-wrong-secret-32-chars")

    response = await _post(client, body, sig, "PRODUCT_CREATED")

    assert response.status_code == 401
    assert "Invalid webhook signature" in response.text
    mock_celery_delay.assert_not_called()


async def test_webhook_missing_signature_header_returns_401(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """No ``Saleor-Signature`` header -> 401 (FR-086)."""
    body = _payload("PRODUCT_CREATED", "prod-1")

    response = await client.post(
        "/webhooks/saleor",
        content=body,
        headers={
            "Saleor-Event": "PRODUCT_CREATED",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    mock_celery_delay.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed bodies / missing event header
# ---------------------------------------------------------------------------


async def test_webhook_malformed_json_returns_400(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """Body that is not valid JSON -> 400 (decision 3 in the ADR)."""
    body = b"not valid json {{{"
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "PRODUCT_CREATED")

    assert response.status_code == 400
    assert "Malformed" in response.text
    mock_celery_delay.assert_not_called()


async def test_webhook_missing_event_header_returns_400(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """No ``Saleor-Event`` (or deprecated ``X-Saleor-Event``) header -> 400."""
    body = _payload("PRODUCT_CREATED", "prod-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await client.post(
        "/webhooks/saleor",
        content=body,
        headers={"Saleor-Signature": sig, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert "Saleor-Event" in response.text
    mock_celery_delay.assert_not_called()


async def test_webhook_unknown_event_header_returns_400(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """``Saleor-Event`` outside the known lifecycle set -> 400."""
    body = _payload("PRODUCT_CREATED", "prod-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    response = await _post(client, body, sig, "ORDER_CREATED")

    assert response.status_code == 400
    mock_celery_delay.assert_not_called()


# ---------------------------------------------------------------------------
# Latency (NFR-003)
# ---------------------------------------------------------------------------


async def test_webhook_response_under_200ms(
    client: AsyncClient,
    mock_celery_delay: MagicMock,
) -> None:
    """Endpoint round-trip is < 200 ms (NFR-003).

    The budget is end-to-end over the in-process ASGI transport.
    A slow CI runner may need a small skip — we use a generous
    250 ms ceiling to absorb normal variance.
    """
    body = _payload("PRODUCT_CREATED", "prod-latency-1")
    sig = _sign(body, _WEBHOOK_SECRET)

    start = time.monotonic()
    response = await _post(client, body, sig, "PRODUCT_CREATED")
    elapsed_ms = (time.monotonic() - start) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 250, f"webhook took {elapsed_ms:.1f} ms (NFR-003: 200 ms)"
