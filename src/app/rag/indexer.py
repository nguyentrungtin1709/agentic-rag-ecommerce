"""Product indexer — Saleor → LlamaIndex → Qdrant pipeline.

Full implementation is in Phase 5.  This stub defines the public
interface that the Celery ``reindex_products`` task will call.
"""

from __future__ import annotations

import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)


class ProductIndexer:
    """Orchestrates the full Saleor → Qdrant product indexing pipeline.

    Pipeline steps (FR-033, FR-034, FR-035):
    1. Fetch all products from Saleor via ``SaleorClient``.
    2. Build LlamaIndex ``TextNode`` objects from product data.
    3. Generate dense embeddings using ``EMBEDDING_MODEL``.
    4. Build sparse BM25 vectors via Qdrant FastEmbed.
    5. Upsert points into the Qdrant ``products`` collection.

    Args:
        settings: Application settings providing Qdrant, Saleor, and
            OpenAI configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def reindex_all(self) -> dict:
        """Fetch all products from Saleor and upsert into Qdrant.

        This is a stub.  Full implementation in Phase 5.

        Returns:
            Dict with ``products_indexed`` and ``duration_seconds`` keys.
        """
        logger.info("ProductIndexer.reindex_all called (stub)")
        return {"products_indexed": 0, "duration_seconds": 0.0}

    async def upsert_product(self, product_data: dict) -> None:
        """Embed and upsert a single product into Qdrant (FR-078).

        Called by the ``process_webhook`` Celery task for
        ``PRODUCT_CREATED`` and ``PRODUCT_UPDATED`` events.

        This is a stub.  Full implementation in Phase 5.

        Args:
            product_data: Raw product dict from the Saleor webhook payload.
        """
        logger.info(
            "ProductIndexer.upsert_product called (stub)",
            product_id=product_data.get("id"),
        )

    async def delete_product(self, product_id: str) -> None:
        """Remove a product vector from Qdrant (FR-079).

        Called by the ``process_webhook`` Celery task for
        ``PRODUCT_DELETED`` events.

        This is a stub.  Full implementation in Phase 5.

        Args:
            product_id: Saleor product ID string.
        """
        logger.info("ProductIndexer.delete_product called (stub)", product_id=product_id)
