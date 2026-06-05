"""Product retriever — hybrid Qdrant search via LlamaIndex.

Full implementation is in Phase 5.  This stub defines the public
interface that the ``ProductRAGAgent`` subagent will call.
"""

from __future__ import annotations

import structlog

from app.config import Settings
from app.models.product import ProductPayload

logger = structlog.get_logger(__name__)


class ProductRetriever:
    """Executes hybrid dense + sparse BM25 search against Qdrant.

    Uses LlamaIndex ``QdrantVectorStore`` with Qdrant FastEmbed BM25
    for sparse vectors and ``EMBEDDING_MODEL`` for dense vectors.

    Pipeline (FR-036, FR-037, FR-038, FR-039):
    1. Rewrite the user query to English (FR-040).
    2. Embed the rewritten query with ``EMBEDDING_MODEL``.
    3. Run hybrid search (dense + BM25) in Qdrant.
    4. Apply optional metadata filters (category, price_range, tags).
    5. Rerank with cross-encoder and return top-k results.

    Args:
        settings: Application settings providing Qdrant and OpenAI config.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
    ) -> list[ProductPayload]:
        """Run hybrid search and return ranked product results.

        This is a stub.  Full implementation in Phase 5.

        Args:
            query: User query (in English — FR-040).
            top_k: Max results to return; defaults to ``QDRANT_TOP_K``
                setting (FR-038).
            filters: Optional metadata filter dict (FR-037).

        Returns:
            Ranked list of ``ProductPayload`` dicts.
        """
        logger.info("ProductRetriever.search called (stub)", query=query)
        return []
