"""LangChain tools for the ProductRAG subagent.

Full implementation is in Phase 7.  Stubs define the tool signatures
that the ReAct agent will use:

- ``rewrite_query``   — Rewrite the user query to English for Qdrant search.
- ``hybrid_search``   — Run dense + BM25 search against the Qdrant collection.
- ``filter_products`` — Apply metadata filters to a candidate result set.
"""

from __future__ import annotations

import structlog
from langchain_core.tools import tool

logger = structlog.get_logger(__name__)


@tool
def rewrite_query(query: str, user_profile_json: str = "") -> str:
    """Rewrite a user query to an optimised English search query.

    Produces a concise English keyword phrase that captures the user's
    product intent, incorporating style signals from the profile when
    available.  Search queries must always be English regardless of user
    input language (FR-040).

    This is a stub.  Full implementation in Phase 7.

    Args:
        query: Original user query (any language).
        user_profile_json: Serialised ``UserProfile`` JSON string.

    Returns:
        Optimised English query string for embedding/BM25.
    """
    logger.debug("rewrite_query tool called (stub)", query=query)
    return query


@tool
def hybrid_search(query: str, top_k: int = 5) -> list[dict]:
    """Run hybrid dense + sparse BM25 search in Qdrant.

    Combines semantic vector search (``EMBEDDING_MODEL``) with keyword
    search (FastEmbed BM25) and returns the fused ranked list (FR-036).

    This is a stub.  Full implementation in Phase 7.

    Args:
        query: English search query (output of ``rewrite_query``).
        top_k: Maximum number of results to return (FR-038).

    Returns:
        List of ``ProductPayload`` dicts serialised as plain dicts.
    """
    logger.debug("hybrid_search tool called (stub)", query=query, top_k=top_k)
    return []


@tool
def filter_products(
    products: list[dict],
    category: str = "",
    max_price: float = 0.0,
    tags: list[str] | None = None,
) -> list[dict]:
    """Apply metadata filters to a candidate product result set.

    Supports filtering by ``category``, ``price_range``, and ``tags``
    (FR-037).

    This is a stub.  Full implementation in Phase 7.

    Args:
        products: List of ``ProductPayload`` dicts from ``hybrid_search``.
        category: Category name filter (empty string = no filter).
        max_price: Maximum price filter (0.0 = no filter).
        tags: Required tags — all must be present (empty = no filter).

    Returns:
        Filtered list of product dicts.
    """
    logger.debug("filter_products tool called (stub)", count=len(products))
    return products
