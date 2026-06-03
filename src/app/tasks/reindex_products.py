"""Celery task — reindex all Saleor products into Qdrant.

This task is triggered manually via the ``POST /admin/reindex`` endpoint
(operator-only, protected by a static token) or on a schedule via
Celery Beat.  It fetches all products from Saleor's GraphQL API and
upserts their vector embeddings into the Qdrant ``products`` collection.
"""

from __future__ import annotations

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="tasks.reindex_products",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    time_limit=3600,
)
def reindex_products(self) -> dict:
    """Fetch all products from Saleor and upsert into Qdrant.

    This is a stub.  Full implementation will:
    1. Call ``SaleorClient.fetch_all_products()`` (synchronous wrapper
       around the async client using ``asyncio.run()``).
    2. Embed each product description using ``text-embedding-3-small``.
    3. Generate sparse BM25 vectors via FastEmbed.
    4. Upsert points into Qdrant in batches of 100.
    5. Return a summary dict with counts.

    Returns:
        Dict with ``products_indexed`` and ``duration_seconds`` keys.
    """
    logger.info("reindex_products task started (stub)")
    return {"products_indexed": 0, "duration_seconds": 0.0}
