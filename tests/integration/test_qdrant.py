"""Integration tests — Qdrant connectivity and collection state.

Verifies that:
- The Qdrant service is reachable.
- The ``products`` collection exists (created at app startup).
- The collection has the expected hybrid vector configuration.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, VectorParams

from tests.integration.conftest import QDRANT_URL

COLLECTION_NAME = "products"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


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
