"""Qdrant service — collection management and vector upsert/search.

Wraps the ``qdrant_client.AsyncQdrantClient`` to provide typed helpers
for the application.  Vector search is performed here; LlamaIndex
query engines use ``QdrantVectorStore`` which talks to the same client.
"""

from __future__ import annotations

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    HnswConfigDiff,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from app.config import Settings

logger = structlog.get_logger(__name__)

_DENSE_VECTOR_NAME = "dense"
_SPARSE_VECTOR_NAME = "sparse"


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
        """Create the products collection if it does not already exist.

        Uses hybrid vector config: dense (cosine, HNSW) + sparse (BM25
        via FastEmbed).  Called once at application startup.
        """
        exists = await self._client.collection_exists(self._collection)
        if exists:
            logger.info("Qdrant collection already exists", collection=self._collection)
            return

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
                _SPARSE_VECTOR_NAME: SparseVectorParams(index=SparseIndexParams(on_disk=False)),
            },
        )
        logger.info(
            "Qdrant collection created",
            collection=self._collection,
            dims=self._dims,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
