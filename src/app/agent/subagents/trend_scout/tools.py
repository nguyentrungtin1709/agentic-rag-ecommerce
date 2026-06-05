"""LangChain tools for the TrendScout subagent.

Full implementation is in Phase 7.  Stubs define the tool signatures
that the ReAct agent will use:

- ``tavily_search``     — Primary web search via Tavily API (FR-042).
- ``duckduckgo_search`` — Fallback search when Tavily is unavailable (FR-043).
"""

from __future__ import annotations

import structlog
from langchain_core.tools import tool

logger = structlog.get_logger(__name__)


@tool
def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for design trend information using Tavily (FR-042).

    Returns curated, high-quality web results suitable for trend
    summarisation.  Falls back to ``duckduckgo_search`` if unavailable.

    This is a stub.  Full implementation in Phase 7.

    Args:
        query: Search query (e.g. "summer 2026 print-on-demand design trends").
        max_results: Maximum number of results to return.

    Returns:
        List of result dicts with ``title``, ``url``, ``content`` keys.
    """
    logger.debug("tavily_search tool called (stub)", query=query)
    return []


@tool
def duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for design trend information using DuckDuckGo (FR-043).

    Used as a fallback when the Tavily API returns an error or quota is
    exhausted.

    This is a stub.  Full implementation in Phase 7.

    Args:
        query: Search query.
        max_results: Maximum number of results to return.

    Returns:
        List of result dicts with ``title``, ``url``, ``snippet`` keys.
    """
    logger.debug("duckduckgo_search tool called (stub)", query=query)
    return []
