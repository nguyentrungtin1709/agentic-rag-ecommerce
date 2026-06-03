"""product_rag subagent — hybrid product retrieval using LlamaIndex.

Implements a ReAct-style subagent that queries the Qdrant ``products``
collection using hybrid search (dense + sparse BM25 via FastEmbed) and
returns a ranked list of matching products.

The subagent is invoked by the orchestrator node when the user's
query requires product recommendations.
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def run_product_rag(state: AgentState) -> dict:
    """Run the product RAG subagent and populate retrieved_products.

    This is a stub.  Full implementation will:
    1. Extract the user query from the latest ``HumanMessage``.
    2. Apply query expansion using the user's style profile.
    3. Run hybrid search (dense + BM25) against Qdrant via LlamaIndex.
    4. Rerank results with a cross-encoder model.
    5. Return the top-N ``ProductPayload`` dicts.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``retrieved_products`` populated.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="product_rag",
    )

    logger.debug(
        "product_rag subagent invoked (stub)",
        thread_id=state["thread_id"],
        user_id=state["user_id"],
    )
    return {"retrieved_products": []}
