"""Qdrant service — collection management and vector upsert/search.

Wraps the ``qdrant_client.AsyncQdrantClient`` to provide typed helpers
for the application.  Vector search is performed here; LlamaIndex
query engines use ``QdrantVectorStore`` which talks to the same client.
"""

from __future__ import annotations

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    Distance,
    HnswConfigDiff,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from app.config import Settings

logger = structlog.get_logger(__name__)

# Match LlamaIndex QdrantVectorStore defaults with enable_hybrid=True
# (see llama_index/vector_stores/qdrant/base.py).  Phase 4 migrated
# from "dense"/"sparse" to "text-dense"/"text-sparse" to align with
# the LlamaIndex default and enable hybrid search out of the box.
_DENSE_VECTOR_NAME = "text-dense"
_SPARSE_VECTOR_NAME = "text-sparse"


class QdrantService:
    """Application-level wrapper around the async Qdrant client.

    Args:
        settings: Application settings providing ``qdrant_url``,
            ``qdrant_api_key``, ``qdrant_collection_name``, and
            ``embedding_dims``.
    """

    def __init__(self, settings: Settings) -> None:
        self._collection = settings.qdrant_collection_name
        self._dims = settings.embedding_dims
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )

    @property
    def client(self) -> AsyncQdrantClient:
        """Expose the raw async client for LlamaIndex integration."""
        return self._client

    @property
    def collection_name(self) -> str:
        """Name of the Qdrant collection used for products."""
        return self._collection

    async def ensure_collection(self) -> None:
        """Create the products collection with the correct hybrid config.

        Behaviour:

        1. If the collection does not exist, create it with dense
           (``text-dense``) and sparse (``text-sparse``) vector
           configs.
        2. If it exists, inspect its current ``vectors_config`` and
           ``sparse_vectors_config``.  When the configured vector
           names match, this is a no-op.
        3. When the existing collection uses different vector names
           (e.g. legacy ``dense``/``sparse`` from before Phase 4),
           log a WARNING and drop + recreate it.  Phase 6 ingestion
           has not run yet at this point, so the collection holds at
           most dev test data — recreation is safe.

        Called once at application startup (inside FastAPI
        ``lifespan``).
        """
        if not await self._client.collection_exists(self._collection):
            await self._create_collection()
            return

        try:
            info = await self._client.get_collection(self._collection)
        except UnexpectedResponse:
            await self._create_collection()
            return

        vectors = getattr(info.config.params, "vectors", None)
        sparse_vectors = getattr(info.config.params, "sparse_vectors", None)

        dense_ok = isinstance(vectors, dict) and _DENSE_VECTOR_NAME in vectors
        sparse_ok = isinstance(sparse_vectors, dict) and _SPARSE_VECTOR_NAME in sparse_vectors

        if dense_ok and sparse_ok:
            logger.info(
                "Qdrant collection already exists with expected config",
                collection=self._collection,
            )
            return

        logger.warning(
            "Qdrant collection has mismatched vector config — recreating",
            collection=self._collection,
            has_dense_vector=dense_ok,
            has_sparse_vector=sparse_ok,
        )
        await self._client.delete_collection(collection_name=self._collection)
        await self._create_collection()

    async def _create_collection(self) -> None:
        """Create the products collection with hybrid dense + sparse config."""
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                _DENSE_VECTOR_NAME: VectorParams(
                    size=self._dims,
                    distance=Distance.COSINE,
                    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
                ),
            },
            sparse_vectors_config={
                _SPARSE_VECTOR_NAME: SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                ),
            },
        )
        logger.info(
            "Qdrant collection created",
            collection=self._collection,
            dims=self._dims,
            dense_vector_name=_DENSE_VECTOR_NAME,
            sparse_vector_name=_SPARSE_VECTOR_NAME,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
