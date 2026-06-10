"""Fault tolerance configuration for the ProductRAG subgraph.

Centralises the LangGraph ``RetryPolicy``, ``TimeoutPolicy``, and the
three node-level error handlers used by the 3-stage hybrid retrieval
pipeline.  This module is imported by ``agent.py`` when building the
subgraph; the policies are passed to ``set_node_defaults`` so they
apply to every node, and each handler is wired to its target node via
``add_node(..., error_handler=...)``.

Why a dedicated module:

- The three handlers share a common shape (build a fallback ``Command``
  with a partial state update and a ``goto``), but each must produce
  a node-specific fallback that is safe for the next stage.  Keeping
  them in a separate file makes the strategy unit-testable in
  isolation from the node code.
- The shared retry + timeout policy values (3 attempts, 60s run, 30s
  idle) are defined once and consumed by the graph builder, so
  tweaking them does not require touching node code.

Fallback strategy summary:

- ``prepare_query_node`` failure -> reuse the latest ``HumanMessage``
  content from ``state["messages"]`` as the search query, no
  filters, and continue to ``hybrid_search``.  This preserves at
  least the textual intent of the original request.
- ``hybrid_search_node`` failure -> continue with an empty candidate
  list, and let ``llm_postprocess_node`` short-circuit to ``END``
  with no products.  Better than skipping the rerank stage and
  returning a partial state shape.
- ``llm_postprocess_node`` failure -> return the top-K candidates by
  raw Qdrant score as the final retrieved_products, skip reranking
  but still produce a usable, ordered result set.
"""

# pyright: reportPrivateImportUsage=false

from __future__ import annotations

import structlog
from langgraph.errors import NodeError
from langgraph.types import (  # type: ignore[attr-defined]
    Command,
    RetryPolicy,
    TimeoutPolicy,
    default_retry_on,
)

from app.agent.subagents.product_rag.state import ProductRAGState

logger = structlog.get_logger(__name__)


# ── Shared policies ──────────────────────────────────────────────────────────

# 3 attempts with the default retry condition covers ConnectionError,
# TimeoutError, and httpx 5xx without retrying on validation errors.
_PRODUCT_RAG_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    retry_on=default_retry_on,
)

# 60s total run + 30s idle.  Hybrid search on the Qdrant side typically
# completes in <500ms, so 60s is generous; 30s idle covers slow LLM
# streams without masking a hung connection.
_PRODUCT_RAG_TIMEOUT_POLICY = TimeoutPolicy(
    run_timeout=60,
    idle_timeout=30,
)


# ── Error handlers ───────────────────────────────────────────────────────────


async def handle_prepare_query_error(
    state: ProductRAGState,
    error: NodeError,
) -> Command:
    """Fallback for ``prepare_query_node`` failures.

    Reuses the content of the most recent ``HumanMessage`` in
    ``state["messages"]`` as the English search query (worst case
    it is the same language as the user typed) and drops the
    metadata filters.  Routes forward to ``hybrid_search`` so the
    rest of the pipeline can still attempt a retrieval.

    The decision to keep the raw message (rather than abort) reflects
    the fact that the prepare-query step is an optimisation — the
    raw query is usually still serviceable, and the rerank step can
    rescue mediocre matches.

    Args:
        state: Current ``ProductRAGState``.  Must contain
            ``messages``.
        error: The ``NodeError`` raised by the failed prepare-query
            node, with ``.error`` carrying the original exception.

    Returns:
        ``Command`` writing ``query`` + ``filters`` and routing to
        ``hybrid_search``.
    """
    from langchain_core.messages import HumanMessage

    fallback_query = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            content = msg.content
            fallback_query = content if isinstance(content, str) else str(content)
            break

    logger.warning(
        "product_rag_prepare_query_failed",
        node=error.node,
        error_type=type(error.error).__name__,
        error_message=str(error.error)[:200],
        fallback_query_len=len(fallback_query),
    )
    return Command(
        update={"query": fallback_query, "filters": None},
        goto="hybrid_search",
    )


async def handle_hybrid_search_error(
    state: ProductRAGState,
    error: NodeError,
) -> Command:
    """Fallback for ``hybrid_search_node`` failures.

    Continues the pipeline with an empty candidate list and routes to
    ``llm_postprocess``.  The rerank step is invoked on ``[]`` (which
    is a no-op for the LLM and returns ``[]``), and the final
    ``retrieved_products`` is empty — a graceful degradation.

    Args:
        state: Current ``ProductRAGState``.  Not used here — the
            candidate list is forced to empty to keep the state shape
            consistent with the success path.
        error: The ``NodeError`` raised by the failed hybrid-search
            node.

    Returns:
        ``Command`` writing ``candidates=[]`` and routing to
        ``llm_postprocess``.
    """
    logger.error(
        "product_rag_hybrid_search_failed",
        node=error.node,
        error_type=type(error.error).__name__,
        error_message=str(error.error)[:200],
    )
    return Command(
        update={"candidates": []},
        goto="llm_postprocess",
    )


async def handle_llm_postprocess_error(
    state: ProductRAGState,
    error: NodeError,
) -> Command:
    """Fallback for ``llm_postprocess_node`` failures.

    Skips the LLM rerank and returns the first ``qdrant_rerank_top_k``
    candidates by their raw Qdrant score (as ordered in the
    ``candidates`` list returned by ``hybrid_search_node``).  This
    keeps the final shape of ``retrieved_products`` consistent with
    the success path.

    Args:
        state: Current ``ProductRAGState``.  Must contain the
            ``candidates`` list populated by ``hybrid_search_node``.
        error: The ``NodeError`` raised by the failed
            llm-postprocess node.

    Returns:
        ``Command`` writing ``retrieved_products`` (top-K by
        position) and routing to ``__end__``.
    """
    from app.config import get_settings

    settings = get_settings()
    candidates = state.get("candidates", [])
    top_k = min(settings.qdrant_rerank_top_k, len(candidates))
    fallback = candidates[:top_k]

    logger.error(
        "product_rag_llm_postprocess_failed",
        node=error.node,
        error_type=type(error.error).__name__,
        error_message=str(error.error)[:200],
        fallback_count=len(fallback),
    )
    return Command(
        update={"retrieved_products": fallback},
        goto="__end__",
    )
