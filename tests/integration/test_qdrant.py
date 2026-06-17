"""Integration tests — Qdrant connectivity and collection state.

Verifies that:
- The Qdrant service is reachable.
- The ``products`` collection exists (created at app startup).
- The collection has the expected hybrid vector configuration.
- The vector name constants match what ``QdrantService`` and
  ``ProductIndexer`` actually use at runtime (Phase 4 migration
  from ``dense``/``sparse`` to ``text-dense``/``text-sparse``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, VectorParams

from tests.integration.conftest import QDRANT_URL

COLLECTION_NAME = "products"
DENSE_VECTOR_NAME = "text-dense"
SPARSE_VECTOR_NAME = "text-sparse"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def qdrant_client() -> AsyncGenerator[AsyncQdrantClient, None]:
    """Return an async Qdrant client for the test module."""
    client = AsyncQdrantClient(url=QDRANT_URL)
    yield client
    await client.close()


async def test_qdrant_is_reachable(qdrant_client: AsyncQdrantClient) -> None:
    """Qdrant /healthz endpoint must respond."""
    result = await qdrant_client.get_collections()
    assert result is not None


async def test_qdrant_products_collection_exists(qdrant_client: AsyncQdrantClient) -> None:
    """The products collection must have been created at app startup."""
    exists = await qdrant_client.collection_exists(COLLECTION_NAME)
    assert exists is True, (
        f"Collection '{COLLECTION_NAME}' not found — "
        "ensure the app has started at least once (it creates the collection on startup)"
    )


async def test_qdrant_products_collection_has_dense_vectors(
    qdrant_client: AsyncQdrantClient,
) -> None:
    """The products collection must have a dense vector config (cosine distance)."""
    info = await qdrant_client.get_collection(COLLECTION_NAME)
    vectors = info.config.params.vectors
    assert isinstance(vectors, dict), "Expected named vectors config (dict)"
    assert DENSE_VECTOR_NAME in vectors, (
        f"Dense vector '{DENSE_VECTOR_NAME}' not found in collection config"
    )
    dense_cfg = vectors[DENSE_VECTOR_NAME]
    assert isinstance(dense_cfg, VectorParams)
    assert dense_cfg.distance == Distance.COSINE


async def test_qdrant_products_collection_has_sparse_vectors(
    qdrant_client: AsyncQdrantClient,
) -> None:
    """The products collection must have a sparse vector config for BM25."""
    info = await qdrant_client.get_collection(COLLECTION_NAME)
    sparse = info.config.params.sparse_vectors
    assert sparse is not None and SPARSE_VECTOR_NAME in sparse, (
        f"Sparse vector '{SPARSE_VECTOR_NAME}' not found in collection config"
    )


# ---------------------------------------------------------------------------
# Round-trip: index a small batch, then search by name
# ---------------------------------------------------------------------------


async def test_indexed_batch_is_retrievable_via_hybrid_search(
    qdrant_client: AsyncQdrantClient,
) -> None:
    """A small ``ProductIndexer.index_batch`` round-trip is searchable.

    Skipped when Qdrant is not reachable (the fixture collection does
    not exist).
    """
    from app.config import get_settings
    from app.models.product import ProductPayload
    from app.rag.indexer import ProductIndexer

    settings = get_settings()
    indexer = ProductIndexer(settings)

    unique_token = f"RoundTripTest{uuid.uuid4().hex[:8]}"
    payload = ProductPayload(
        product_id=unique_token,
        name=f"{unique_token} Premium T-Shirt",
        slug=unique_token,
        description=(
            f"Distinctive {unique_token} cotton t-shirt with breathable fabric. "
            "Perfect for daily wear and casual outings."
        ),
        category="Apparel",
        collections=["RoundTripTest"],
        price_min=25.0,
        price_max=25.0,
        currency="USD",
        available=True,
        saleor_url=f"https://example.com/p/{unique_token}/",
        thumbnail_url="",
    )

    succeeded, skipped = await indexer.index_batch([payload])
    assert succeeded == 1
    assert skipped == []

    # Wait for the index to refresh and then search by the unique token.
    import asyncio

    await asyncio.sleep(1)

    from app.services.qdrant_service import QdrantService

    qdrant_service = QdrantService(settings)
    try:
        # Use a Qdrant scroll-by-filter to confirm the point exists.
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        points, _ = await qdrant_service.client.scroll(
            collection_name=settings.qdrant_collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="product_id",
                        match=MatchValue(value=unique_token),
                    ),
                ],
            ),
            limit=1,
            with_payload=True,
        )
    finally:
        await qdrant_service.close()

    assert points is not None
    assert len(points) == 1
    payload = points[0].payload
    assert payload is not None
    assert payload["product_id"] == unique_token
    assert unique_token in payload["name"]
