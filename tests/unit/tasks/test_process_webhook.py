"""Unit tests — ``process_webhook`` Celery task.

Covers the A2 dispatch logic (CREATED / UPDATED / DELETED / unknown),
the payload-first with Saleor-fallback flow, the transient-vs-permanent
error classifier, the retry path, and the structured return value.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
import qdrant_client.http.exceptions as qdrant_exc

from app.services.saleor_client import SaleorClient
from app.tasks.process_webhook import process_webhook
from app.utils.transient import is_transient

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _invoke_task(
    fake_self: MagicMock,
    event_type: str,
    product_id: str,
    product_data: dict,
) -> dict:
    """Invoke the underlying ``process_webhook`` function with a fake self.

    Celery's ``.run()`` attribute does not pass ``self``, so the only
    way to inject a controlled ``request.retries`` / ``retry()`` is to
    bind the unbound ``process_webhook`` function (the original
    function captured by the ``@celery_app.task(bind=True)``
    decorator) to a mock instance.
    """
    real_task = process_webhook._get_current_object()  # type: ignore[attr-defined]
    bound = types.MethodType(real_task.run.__func__, fake_self)
    return bound(event_type, product_id, product_data)


def _fake_self(retries: int = 0) -> MagicMock:
    """Build a fake bound Celery task instance."""
    self = MagicMock()
    self.request.retries = retries
    # ``self.retry`` must re-raise the original exception (Celery
    # semantics) so the test's try/except sees it.
    self.retry.side_effect = lambda exc: (_ for _ in ()).throw(exc)
    return self


def _full_pricing_payload(
    product_id: str = "prod-1",
    *,
    with_pricing: bool = True,
    with_channel_pricing: bool = True,
) -> dict:
    """Build a Saleor-shaped product dict matching the subscription query.

    Default has full pricing data.  Pass ``with_pricing=False`` (and
    optionally ``with_channel_pricing=False``) to simulate an
    incomplete payload that should trigger the Saleor fallback.
    """
    data: dict = {
        "id": product_id,
        "name": "Test Tee",
        "slug": f"test-{product_id[:8]}",
        "description": "A test product",
        "category": {"name": "Apparel"},
        "collections": [{"name": "Spring 2026"}],
        "thumbnail": {"url": "https://cdn.example.com/tee.webp"},
    }
    if with_pricing:
        data["pricing"] = {
            "priceRange": {
                "start": {"gross": {"amount": 10.0, "currency": "USD"}},
                "stop": {"gross": {"amount": 20.0, "currency": "USD"}},
            },
        }
    if with_channel_pricing:
        data["channelListings"] = [
            {
                "channel": {"slug": "default-channel"},
                "pricing": {
                    "priceRange": {
                        "start": {"gross": {"amount": 10.0, "currency": "USD"}},
                        "stop": {"gross": {"amount": 20.0, "currency": "USD"}},
                    },
                },
            },
        ]
    return data


# ---------------------------------------------------------------------------
# is_transient classifier (re-tested here for the shared util)
# ---------------------------------------------------------------------------


def test_is_transient_openai_rate_limit() -> None:
    """``openai.RateLimitError`` is transient."""
    exc = openai.RateLimitError(message="rate limit", response=MagicMock(), body=None)
    assert is_transient(exc) is True


def test_is_transient_openai_api_timeout() -> None:
    """``openai.APITimeoutError`` is transient."""
    exc = openai.APITimeoutError(request=MagicMock())
    assert is_transient(exc) is True


def test_is_transient_openai_internal_server_error() -> None:
    """``openai.InternalServerError`` is transient."""
    exc = openai.InternalServerError(message="boom", response=MagicMock(), body=None)
    assert is_transient(exc) is True


def test_is_transient_httpx_connect_error() -> None:
    """``httpx.ConnectError`` is transient."""
    exc = httpx.ConnectError("refused")
    assert is_transient(exc) is True


def test_is_transient_httpx_read_timeout() -> None:
    """``httpx.ReadTimeout`` is transient."""
    exc = httpx.ReadTimeout("slow")
    assert is_transient(exc) is True


def test_is_transient_httpx_connect_timeout() -> None:
    """``httpx.ConnectTimeout`` is transient."""
    exc = httpx.ConnectTimeout("slow")
    assert is_transient(exc) is True


def test_is_transient_qdrant_unexpected_response() -> None:
    """``qdrant_client.UnexpectedResponse`` is transient."""
    exc = qdrant_exc.UnexpectedResponse(
        status_code=503,
        reason_phrase="Service Unavailable",
        content=b"",
        headers=httpx.Headers(),
    )
    assert is_transient(exc) is True


def test_is_transient_value_error_is_permanent() -> None:
    """``ValueError`` is not in the transient whitelist -> permanent."""
    assert is_transient(ValueError("bad data")) is False


def test_is_transient_runtime_error_is_permanent() -> None:
    """``RuntimeError`` is not in the transient whitelist -> permanent."""
    assert is_transient(RuntimeError("oops")) is False


def test_is_transient_key_error_is_permanent() -> None:
    """``KeyError`` is not in the transient whitelist -> permanent."""
    assert is_transient(KeyError("missing")) is False


# ---------------------------------------------------------------------------
# process_webhook dispatch (A2: payload-first, Saleor fallback)
# ---------------------------------------------------------------------------


def test_process_webhook_product_created_uses_payload_when_complete() -> None:
    """Full payload -> no Saleor fetch -> ``index_batch`` called once."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock()

    product_data = _full_pricing_payload("prod-1")

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(_fake_self(), "PRODUCT_CREATED", "prod-1", product_data)

    # index_batch was called with a single ProductPayload.
    assert fake_indexer.index_batch.await_count == 1
    payloads = fake_indexer.index_batch.await_args.args[0]
    assert len(payloads) == 1
    assert payloads[0].product_id == "prod-1"
    assert payloads[0].price_min == 10.0
    assert payloads[0].price_max == 20.0
    assert payloads[0].currency == "USD"

    # Saleor was NOT called (payload had everything we need).
    fake_saleor.fetch_product_by_id.assert_not_called()

    # Return value shape.
    assert result["status"] == "upserted"
    assert result["event_type"] == "PRODUCT_CREATED"
    assert result["product_id"] == "prod-1"
    assert "qdrant_point_id" in result
    assert "duration_ms" in result
    assert isinstance(result["duration_ms"], int)


def test_process_webhook_product_updated_uses_payload_when_complete() -> None:
    """``PRODUCT_UPDATED`` follows the same A2 flow as ``PRODUCT_CREATED`` (FR-078)."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock()

    product_data = _full_pricing_payload("prod-2")

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(_fake_self(), "PRODUCT_UPDATED", "prod-2", product_data)

    fake_saleor.fetch_product_by_id.assert_not_called()
    assert result["status"] == "upserted"
    assert result["event_type"] == "PRODUCT_UPDATED"
    assert result["product_id"] == "prod-2"


def test_process_webhook_falls_back_to_saleor_when_payload_lacks_pricing() -> None:
    """Incomplete payload -> Saleor fetch -> ``index_batch`` with merged data."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock(
        return_value=_full_pricing_payload("prod-3"),
    )
    fake_saleor.close = AsyncMock()

    # Payload WITHOUT pricing (subscription query was trimmed, or field is null).
    product_data = _full_pricing_payload(
        "prod-3",
        with_pricing=False,
        with_channel_pricing=False,
    )

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        _invoke_task(_fake_self(), "PRODUCT_CREATED", "prod-3", product_data)

    # Saleor was called to fetch the canonical data.
    fake_saleor.fetch_product_by_id.assert_awaited_once_with("prod-3")
    # The fetched data was upserted.
    assert fake_indexer.index_batch.await_count == 1
    payloads = fake_indexer.index_batch.await_args.args[0]
    assert len(payloads) == 1
    assert payloads[0].product_id == "prod-3"
    assert payloads[0].price_min == 10.0


def test_process_webhook_falls_back_to_channel_listings_pricing() -> None:
    """If top-level pricing is missing but ``channelListings[0].pricing`` exists, use it."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock()  # should NOT be called

    # Top-level pricing missing, but channel listings pricing present.
    product_data = _full_pricing_payload(
        "prod-4",
        with_pricing=False,
        with_channel_pricing=True,
    )

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        _invoke_task(_fake_self(), "PRODUCT_CREATED", "prod-4", product_data)

    # No Saleor fetch — channel listing pricing was used.
    fake_saleor.fetch_product_by_id.assert_not_called()
    assert fake_indexer.index_batch.await_count == 1


def test_process_webhook_permanent_error_when_both_payload_and_saleor_fail() -> None:
    """Payload has no pricing AND Saleor returns None -> ``failed`` (no retry)."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock(return_value=None)
    fake_saleor.close = AsyncMock()

    product_data = _full_pricing_payload(
        "prod-5",
        with_pricing=False,
        with_channel_pricing=False,
    )

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(_fake_self(), "PRODUCT_CREATED", "prod-5", product_data)

    fake_saleor.fetch_product_by_id.assert_awaited_once_with("prod-5")
    # No upsert happened.
    fake_indexer.index_batch.assert_not_called()
    # Structured permanent failure returned.
    assert result["status"] == "failed"
    assert result["error_type"] == "PermanentProductError"
    assert "prod-5" in result["error"]
    assert result["product_id"] == "prod-5"
    assert result["event_type"] == "PRODUCT_CREATED"


def test_process_webhook_transient_error_on_saleor_fetch_triggers_retry() -> None:
    """Saleor fetch raises ``httpx.ConnectError`` -> transient -> retry."""
    fake_self = _fake_self(retries=0)
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()
    fake_saleor.fetch_product_by_id = AsyncMock(
        side_effect=httpx.ConnectError("refused"),
    )
    fake_saleor.close = AsyncMock()

    product_data = _full_pricing_payload(
        "prod-6",
        with_pricing=False,
        with_channel_pricing=False,
    )

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
        pytest.raises(httpx.ConnectError),
    ):
        MockSC.return_value = fake_saleor
        _invoke_task(fake_self, "PRODUCT_CREATED", "prod-6", product_data)

    fake_self.retry.assert_called_once()


def test_process_webhook_product_deleted_calls_delete_product() -> None:
    """``PRODUCT_DELETED`` calls ``ProductIndexer.delete_product`` (FR-079)."""
    fake_indexer = MagicMock()
    fake_indexer.delete_product = AsyncMock(return_value=None)
    fake_saleor = MagicMock()  # should NOT be called for delete

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(_fake_self(), "PRODUCT_DELETED", "prod-7", {})

    fake_indexer.delete_product.assert_awaited_once_with("prod-7")
    fake_saleor.fetch_product_by_id.assert_not_called()
    assert result["status"] == "deleted"
    assert result["event_type"] == "PRODUCT_DELETED"
    assert result["product_id"] == "prod-7"
    assert "qdrant_point_id" not in result
    assert "duration_ms" in result


def test_process_webhook_unknown_event_is_ignored() -> None:
    """Unknown event types log a warning and return ``ignored`` without any work."""
    fake_indexer = MagicMock()
    fake_saleor = MagicMock()

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(_fake_self(), "PRODUCT_TRANSLATED", "prod-8", {})

    fake_indexer.index_batch.assert_not_called()
    fake_indexer.delete_product.assert_not_called()
    fake_saleor.fetch_product_by_id.assert_not_called()
    assert result["status"] == "ignored"
    assert result["event_type"] == "PRODUCT_TRANSLATED"
    assert result["product_id"] == "prod-8"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_process_webhook_openai_rate_limit_triggers_retry() -> None:
    """OpenAI rate limit during embed -> transient -> retry."""
    fake_self = _fake_self(retries=0)
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(
        side_effect=openai.RateLimitError(message="rate limit", response=MagicMock(), body=None),
    )
    fake_saleor = MagicMock()

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
        pytest.raises(openai.RateLimitError),
    ):
        MockSC.return_value = fake_saleor
        _invoke_task(fake_self, "PRODUCT_CREATED", "prod-9", _full_pricing_payload("prod-9"))

    fake_self.retry.assert_called_once()


def test_process_webhook_permanent_error_returns_failed_status() -> None:
    """Permanent (non-transient) error during index_batch -> structured failure."""
    fake_self = _fake_self(retries=0)
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(side_effect=ValueError("malformed data"))
    fake_saleor = MagicMock()

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(
            fake_self,
            "PRODUCT_CREATED",
            "prod-10",
            _full_pricing_payload("prod-10"),
        )

    fake_self.retry.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_type"] == "ValueError"
    assert "malformed data" in result["error"]
    assert result["product_id"] == "prod-10"
    assert result["event_type"] == "PRODUCT_CREATED"


# ---------------------------------------------------------------------------
# Return-value shape
# ---------------------------------------------------------------------------


def test_process_webhook_upsert_returns_qdrant_point_id() -> None:
    """Successful upsert includes the deterministic Qdrant point id (UUID v5)."""
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock()
    fake_saleor = MagicMock()

    with (
        patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_webhook.SaleorClient", wraps=SaleorClient) as MockSC,
    ):
        MockSC.return_value = fake_saleor
        result = _invoke_task(
            _fake_self(),
            "PRODUCT_CREATED",
            "prod-11",
            _full_pricing_payload("prod-11"),
        )

    assert result["qdrant_point_id"]
    from app.rag.indexer import to_qdrant_point_id

    assert result["qdrant_point_id"] == to_qdrant_point_id("prod-11")


def test_process_webhook_duration_ms_is_non_negative_int() -> None:
    """``duration_ms`` is a non-negative integer on success."""
    fake_indexer = MagicMock()
    fake_indexer.delete_product = AsyncMock(return_value=None)

    with patch("app.tasks.process_webhook.ProductIndexer", return_value=fake_indexer):
        result = _invoke_task(_fake_self(), "PRODUCT_DELETED", "prod-12", {})

    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0
