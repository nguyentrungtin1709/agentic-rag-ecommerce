"""Unit tests — ProductIndexer (Saleor → Qdrant pipeline).

These tests mock every external dependency (Qdrant client,
IngestionPipeline, OpenAI embedding, Saleor client) and verify
the orchestration logic in isolation.  Integration tests
(``tests/integration/test_qdrant.py``) cover the real wire format.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.models.product import ProductPayload
from app.rag.indexer import PermanentProductError, ProductIndexer, to_qdrant_point_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(settings_overrides: None) -> Settings:
    """Return a ``Settings`` instance with safe test values."""
    from app.config import get_settings

    return get_settings()


@pytest.fixture
def indexer(settings: Settings) -> ProductIndexer:
    """Return a ``ProductIndexer`` bound to test settings."""
    return ProductIndexer(settings)


@pytest.fixture
def stub_pipeline() -> AsyncMock:
    """Return an AsyncMock suitable for ``indexer._run_pipeline``."""
    return AsyncMock()


@pytest.fixture
def sample_payload() -> ProductPayload:
    """Return a single short-description ``ProductPayload``."""
    return ProductPayload(
        product_id="prod-1",
        name="Cotton T-Shirt",
        slug="cotton-t-shirt",
        description="A soft cotton t-shirt, perfect for daily wear.",
        category="Apparel",
        collections=["Spring 2026"],
        price_min=20.0,
        price_max=20.0,
        currency="USD",
        available=True,
        saleor_url="https://example.com/p/cotton-t-shirt/",
        thumbnail_url="https://example.com/img/cotton-t-shirt.webp",
    )


@pytest.fixture
def long_payload() -> ProductPayload:
    """Return a product whose cleaned description exceeds ``description_max_chars``."""
    long_desc = "A high-quality product. " * 100  # well over 500 chars
    return ProductPayload(
        product_id="prod-2",
        name="Premium Leather Jacket",
        description=long_desc,
        category="Apparel",
        price_min=150.0,
        price_max=200.0,
        currency="USD",
    )


@pytest.fixture
def bad_payload() -> ProductPayload:
    """Return a product whose description raises on ``clean_product_description``."""
    return ProductPayload(
        product_id="prod-bad",
        name="Bad JSON product",
        description='{"blocks": [INVALID',
        category="X",
    )


# ---------------------------------------------------------------------------
# _build_node / two-track description rule
# ---------------------------------------------------------------------------


def test_build_node_uses_summary_in_text_and_full_in_metadata(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """The embedded ``text`` is ``name + summary``; ``description`` keeps the full text."""
    summary = "short summary"
    full = "the full original cleaned description"

    node = indexer._build_node(
        sample_payload,
        summary=summary,
        full_description=full,
    )

    assert node.text == f"{sample_payload.name}\n\n{summary}"
    assert node.id_ == to_qdrant_point_id(sample_payload.product_id)
    assert node.metadata["description"] == full
    assert node.metadata["product_id"] == sample_payload.product_id
    assert node.metadata["name"] == sample_payload.name
    assert node.metadata["category"] == sample_payload.category


def test_build_node_metadata_has_all_13_fields(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """All 13 expected metadata keys are present."""
    node = indexer._build_node(sample_payload, summary="x", full_description="y")

    expected = {
        "product_id",
        "name",
        "description",
        "slug",
        "category",
        "collections",
        "price_min",
        "price_max",
        "currency",
        "price_range",
        "available",
        "saleor_url",
        "thumbnail_url",
    }
    assert expected.issubset(node.metadata.keys())


def test_build_node_text_uses_name_and_summary_joined_by_newlines(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """``node.text`` is exactly ``f"{name}\\n\\n{summary}"``."""
    node = indexer._build_node(sample_payload, summary="My summary", full_description="Full")
    assert node.text == f"{sample_payload.name}\n\nMy summary"


def test_build_node_id_is_deterministic_uuid5_of_product_id(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """``node.id_`` is the UUID5-hashed Saleor ID (FR-080 idempotency + Qdrant strict mode).

    Qdrant 1.18 strict mode only accepts UUID or integer point IDs, but
    Saleor product IDs are base64 GraphQL Global IDs.  We hash the
    Saleor ID into a stable UUID5 so re-indexing the same product is
    idempotent.  The original Saleor ID is preserved in
    ``metadata['product_id']`` for filtering.
    """
    node = indexer._build_node(sample_payload, summary="x", full_description="y")
    assert node.id_ == to_qdrant_point_id(sample_payload.product_id)


def test_to_qdrant_point_id_is_deterministic() -> None:
    """Same input produces the same UUID (idempotency)."""
    saleor_id = "UHJvZHVjdDoxMjM="
    assert to_qdrant_point_id(saleor_id) == to_qdrant_point_id(saleor_id)


def test_to_qdrant_point_id_returns_uuid_string() -> None:
    """The output is a valid UUID string parseable by ``uuid.UUID``."""
    import uuid

    saleor_id = "UHJvZHVjdDoxMjM="
    point_id = to_qdrant_point_id(saleor_id)
    # Should not raise.
    parsed = uuid.UUID(point_id)
    assert str(parsed) == point_id


def test_to_qdrant_point_id_different_inputs_different_uuids() -> None:
    """Distinct Saleor IDs map to distinct UUIDs (no collision for the test sample)."""
    assert to_qdrant_point_id("id-a") != to_qdrant_point_id("id-b")


def test_format_price_range_min_equals_max(indexer: ProductIndexer) -> None:
    """Equal min/max renders as a single price (no ``-``)."""
    result = indexer._format_price_range(20.0, 20.0, "USD")
    assert result == "20.00 USD"


def test_format_price_range_min_less_than_max(indexer: ProductIndexer) -> None:
    """A range renders as ``min - max currency``."""
    result = indexer._format_price_range(150.0, 200.0, "USD")
    assert result == "150.00 - 200.00 USD"


# ---------------------------------------------------------------------------
# _clean_for_payload / _summarize_with_limit
# ---------------------------------------------------------------------------


async def test_clean_for_payload_returns_cleaned_text(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """The helper returns the cleaned description when cleaning succeeds."""
    result = await indexer._clean_for_payload(sample_payload)
    assert isinstance(result, str)
    assert "cotton" in result.lower()


async def test_clean_for_payload_raises_permanent_on_malformed(
    indexer: ProductIndexer,
) -> None:
    """A ValueError inside the cleaner surfaces as :class:`PermanentProductError`."""
    payload = ProductPayload(
        product_id="x",
        name="x",
        description="some text",
    )
    with (
        patch(
            "app.rag.indexer.clean_product_description",
            side_effect=ValueError("malformed"),
        ),
        pytest.raises(PermanentProductError),
    ):
        await indexer._clean_for_payload(payload)


async def test_summarize_with_limit_short_text_passes_through(
    indexer: ProductIndexer,
) -> None:
    """Short text is returned verbatim without any LLM call."""
    short = "x" * 100
    with patch("app.rag.text_cleaning.ChatOpenAI") as mock_chat:
        result = await indexer._summarize_with_limit(short)
    assert result == short
    mock_chat.assert_not_called()


async def test_summarize_with_limit_long_text_calls_llm(
    indexer: ProductIndexer, settings: Settings
) -> None:
    """Long text triggers an LLM call via the centralized helper."""
    long_text = "x" * (settings.description_max_chars + 100)
    fake_response = MagicMock()
    fake_response.content = "summary"
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        result = await indexer._summarize_with_limit(long_text)

    assert result == "summary"
    fake_model.ainvoke.assert_awaited_once()


# ---------------------------------------------------------------------------
# index_batch
# ---------------------------------------------------------------------------


async def test_index_batch_builds_nodes_and_calls_pipeline(
    indexer: ProductIndexer,
    sample_payload: ProductPayload,
    stub_pipeline: AsyncMock,
) -> None:
    """``index_batch`` builds TextNodes and upserts via the pipeline."""
    with patch.object(indexer, "_run_pipeline", stub_pipeline):
        succeeded, skipped = await indexer.index_batch([sample_payload])

    assert succeeded == 1
    assert skipped == []
    stub_pipeline.assert_awaited_once()
    # The single node's text was the cleaned short description.
    nodes_arg = stub_pipeline.call_args.args[0]
    assert len(nodes_arg) == 1
    assert nodes_arg[0].id_ == to_qdrant_point_id(sample_payload.product_id)


async def test_index_batch_skips_permanent_product_errors(
    indexer: ProductIndexer,
    sample_payload: ProductPayload,
    bad_payload: ProductPayload,
    stub_pipeline: AsyncMock,
) -> None:
    """A permanently-failed product is skipped, the rest of the batch proceeds."""

    def _clean_or_raise(d: str) -> str:
        if d == bad_payload.description:
            raise ValueError("forced")
        return "cleaned " + d

    with (
        patch.object(indexer, "_run_pipeline", stub_pipeline),
        patch(
            "app.rag.indexer.clean_product_description",
            side_effect=_clean_or_raise,
        ),
    ):
        succeeded, skipped = await indexer.index_batch([sample_payload, bad_payload])

    assert succeeded == 1
    assert len(skipped) == 1
    assert skipped[0]["product_id"] == bad_payload.product_id
    assert skipped[0]["stage"] == "cleaning"


async def test_index_batch_raises_when_all_products_fail(
    indexer: ProductIndexer,
) -> None:
    """When every product in the batch fails cleaning, raise a synthetic error."""

    def _raise(_text: str) -> str:
        raise ValueError("forced")

    with patch("app.rag.indexer.clean_product_description", side_effect=_raise):
        bad_1 = ProductPayload(product_id="x1", name="x", description="a")
        bad_2 = ProductPayload(product_id="x2", name="x", description="b")
        with pytest.raises(PermanentProductError) as exc:
            await indexer.index_batch([bad_1, bad_2])
    assert "2" in str(exc.value)


async def test_index_batch_closes_qdrant_on_success(
    indexer: ProductIndexer,
    sample_payload: ProductPayload,
    stub_pipeline: AsyncMock,
) -> None:
    """Pipeline is invoked exactly once on the success path."""
    with patch.object(indexer, "_run_pipeline", stub_pipeline):
        await indexer.index_batch([sample_payload])
    stub_pipeline.assert_awaited_once()


async def test_index_batch_raises_when_pipeline_raises(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """A pipeline error propagates and no swallowing happens."""

    async def _raise(_nodes: list) -> None:
        raise RuntimeError("pipeline down")

    with (
        patch.object(indexer, "_run_pipeline", side_effect=_raise),
        pytest.raises(RuntimeError, match="pipeline down"),
    ):
        await indexer.index_batch([sample_payload])


async def test_index_batch_short_descriptions_skip_llm_call(
    indexer: ProductIndexer,
    settings: Settings,
    stub_pipeline: AsyncMock,
) -> None:
    """All-short batch: ``_summarize_with_limit`` is never called."""
    payloads = [
        ProductPayload(
            product_id=f"p-{i}",
            name=f"Product {i}",
            description="short " * 10,  # ~60 chars
        )
        for i in range(3)
    ]
    assert all(len(p.description) <= settings.description_max_chars for p in payloads)

    with (
        patch.object(indexer, "_run_pipeline", stub_pipeline),
        patch.object(indexer, "_summarize_with_limit") as mock_summarize,
    ):
        succeeded, _ = await indexer.index_batch(payloads)

    assert succeeded == 3
    mock_summarize.assert_not_called()


async def test_index_batch_summarizes_long_descriptions(
    indexer: ProductIndexer,
    settings: Settings,
    stub_pipeline: AsyncMock,
) -> None:
    """Long descriptions all hit the LLM path."""
    payloads = [
        ProductPayload(
            product_id=f"lp-{i}",
            name=f"Long {i}",
            description="x" * (settings.description_max_chars + 50),
        )
        for i in range(3)
    ]

    with (
        patch.object(indexer, "_run_pipeline", stub_pipeline),
        patch.object(indexer, "_summarize_with_limit", new_callable=AsyncMock) as mock_summarize,
    ):
        mock_summarize.return_value = "summary"
        succeeded, _ = await indexer.index_batch(payloads)

    assert succeeded == 3
    assert mock_summarize.await_count == 3


async def test_index_batch_per_product_summarization_failure_does_not_abort_batch(
    indexer: ProductIndexer,
    settings: Settings,
    stub_pipeline: AsyncMock,
) -> None:
    """A single summarization failure is recorded in ``skipped_products``."""
    payloads = [
        ProductPayload(
            product_id=f"lp-{i}",
            name=f"Long {i}",
            description="x" * (settings.description_max_chars + 50),
        )
        for i in range(3)
    ]

    # All three descriptions clean to the same string ("x" * n) since
    # the cleaner just collapses whitespace.  Use the index in the
    # call counter to raise only on the first invocation.
    call_count = {"n": 0}

    async def _flaky(cleaned: str, *, max_chars: int) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("LLM transient error")
        return "summary"

    with (
        patch.object(indexer, "_run_pipeline", stub_pipeline),
        patch(
            "app.rag.indexer.description_for_embedding",
            side_effect=_flaky,
        ),
    ):
        succeeded, skipped = await indexer.index_batch(payloads)

    assert succeeded == 2
    assert len(skipped) == 1
    assert skipped[0]["product_id"] == "lp-0"
    assert skipped[0]["stage"] == "summarization"


async def test_index_batch_summarize_semaphore_bounded_by_settings(
    settings: Settings, stub_pipeline: AsyncMock
) -> None:
    """The semaphore is created from ``settings.description_summarize_concurrency``."""
    # Patch settings to use a small concurrency for the test.
    settings.description_summarize_concurrency = 2
    indexer = ProductIndexer(settings)

    long_payloads = [
        ProductPayload(
            product_id=f"lp-{i}",
            name=f"Long {i}",
            description="x" * (settings.description_max_chars + 50),
        )
        for i in range(6)
    ]

    in_flight = 0
    max_in_flight = 0

    async def _track_concurrency(cleaned: str, *, max_chars: int) -> str:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Let other tasks run, then release.
        import asyncio

        await asyncio.sleep(0)
        in_flight -= 1
        return "summary"

    with (
        patch.object(indexer, "_run_pipeline", stub_pipeline),
        patch(
            "app.rag.indexer.description_for_embedding",
            side_effect=_track_concurrency,
        ),
    ):
        await indexer.index_batch(long_payloads)

    assert max_in_flight <= 2


# ---------------------------------------------------------------------------
# reindex_all
# ---------------------------------------------------------------------------


async def test_reindex_all_batches_by_reindex_batch_size(
    settings: Settings,
) -> None:
    """``reindex_all`` splits payloads into chunks of ``reindex_batch_size``."""
    settings.reindex_batch_size = 3
    indexer = ProductIndexer(settings)
    payloads = [ProductPayload(product_id=f"p-{i}", name=f"Product {i}") for i in range(10)]

    batch_sizes: list[int] = []

    async def _fake_index_batch(payloads_arg: list[ProductPayload]) -> tuple[int, list[dict]]:
        batch_sizes.append(len(payloads_arg))
        return len(payloads_arg), []

    with patch.object(indexer, "index_batch", side_effect=_fake_index_batch):
        succeeded, skipped = await indexer.reindex_all(payloads)

    assert succeeded == 10
    assert skipped == []
    # 10 / 3 -> batches of 3, 3, 3, 1
    assert batch_sizes == [3, 3, 3, 1]


async def test_reindex_all_accumulates_skipped_products_across_batches(
    settings: Settings,
) -> None:
    """Skipped products from each batch are concatenated in the final return."""
    settings.reindex_batch_size = 2
    indexer = ProductIndexer(settings)
    payloads = [ProductPayload(product_id=f"p-{i}", name=f"Product {i}") for i in range(4)]

    async def _fake_index_batch(payloads_arg: list[ProductPayload]) -> tuple[int, list[dict]]:
        # Each batch skips one product.
        return len(payloads_arg) - 1, [
            {
                "product_id": payloads_arg[0].product_id,
                "stage": "cleaning",
                "error": "x",
            }
        ]

    with patch.object(indexer, "index_batch", side_effect=_fake_index_batch):
        succeeded, skipped = await indexer.reindex_all(payloads)

    assert succeeded == 2
    assert len(skipped) == 2


# ---------------------------------------------------------------------------
# upsert_product / delete_product
# ---------------------------------------------------------------------------


async def test_upsert_product_coerces_saleor_node_to_payload(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """A dict with an ``id`` key is coerced via ``SaleorClient.node_to_product_payload``."""
    node = {
        "id": sample_payload.product_id,
        "name": sample_payload.name,
        "slug": sample_payload.slug,
        "description": sample_payload.description,
        "category": {"name": sample_payload.category},
        # Saleor's current GraphQL schema returns a flat list of
        # Collection objects (no ``edges`` wrapper).  See
        # ``SaleorClient.node_to_product_payload`` docstring.
        "collections": [],
        "pricing": {
            "priceRange": {
                "start": {"gross": {"amount": 20.0, "currency": "USD"}},
                "stop": {"gross": {"amount": 20.0, "currency": "USD"}},
            }
        },
        "isAvailable": True,
        "thumbnail": {"url": ""},
    }

    with patch.object(indexer, "index_batch", new_callable=AsyncMock) as mock_batch:
        returned_id = await indexer.upsert_product(node)

    assert returned_id == sample_payload.product_id
    mock_batch.assert_awaited_once()


async def test_upsert_product_accepts_payload_dict_directly(
    indexer: ProductIndexer, sample_payload: ProductPayload
) -> None:
    """A dict without an ``id`` key is parsed as a ``ProductPayload`` directly."""
    payload_dict = sample_payload.model_dump()

    with patch.object(indexer, "index_batch", new_callable=AsyncMock) as mock_batch:
        returned_id = await indexer.upsert_product(payload_dict)

    assert returned_id == sample_payload.product_id
    mock_batch.assert_awaited_once()


async def test_delete_product_builds_correct_filter(
    indexer: ProductIndexer,
) -> None:
    """``delete_product`` issues a Qdrant delete with a flat ``product_id`` filter."""
    with patch("app.rag.indexer.QdrantService") as mock_qdrant_cls:
        mock_qdrant = MagicMock()
        mock_qdrant.client = MagicMock()
        mock_qdrant.client.delete = AsyncMock()
        mock_qdrant.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant

        await indexer.delete_product("prod-42")

    mock_qdrant.client.delete.assert_awaited_once()
    call = mock_qdrant.client.delete.call_args
    assert call.kwargs["collection_name"] == indexer._settings.qdrant_collection_name
    selector = call.kwargs["points_selector"]
    assert selector.must[0].key == "product_id"
    assert selector.must[0].match.value == "prod-42"


async def test_delete_product_closes_qdrant(indexer: ProductIndexer) -> None:
    """``QdrantService.close`` is always called on the delete path."""
    with patch("app.rag.indexer.QdrantService") as mock_qdrant_cls:
        mock_qdrant = MagicMock()
        mock_qdrant.client = MagicMock()
        mock_qdrant.client.delete = AsyncMock()
        mock_qdrant.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant

        await indexer.delete_product("prod-99")

    mock_qdrant.close.assert_awaited_once()
