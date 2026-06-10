"""product_rag subagent -- hybrid product retrieval subgraph.

Builds and exposes the 3-stage ``StateGraph`` that runs hybrid
(dense + sparse BM25) search against the Qdrant ``products``
collection and reranks the top candidates with an LLM.  The subgraph
is invoked by the parent graph when the orchestrator's intent is
``need_product_search``.

Pipeline::

    START -> prepare_query_node -> hybrid_search_node
           -> llm_postprocess_node -> END

Fault tolerance is delegated to LangGraph native mechanisms:

- ``RetryPolicy(max_attempts=3, retry_on=default_retry_on)`` applied
  to every node via ``set_node_defaults`` -- handles transient
  ``ConnectionError``, ``TimeoutError``, and httpx 5xx.
- ``TimeoutPolicy(run_timeout=60, idle_timeout=30)`` applied to
  every node via ``set_node_defaults``.
- A per-node ``error_handler`` (defined in ``fault_tolerance.py``)
  that emits a fallback ``Command(update=..., goto=...)`` so the
  subgraph always produces a ``retrieved_products`` field of the
  expected shape.

The ``run_product_rag`` wrapper translates the parent ``AgentState``
into ``ProductRAGState``, injects the shared ``AsyncQdrantClient``
(dual-path: from ``config["configurable"]["qdrant_aclient"]`` if
present, else a transient client), invokes the compiled subgraph,
and maps the resulting ``retrieved_products`` back to the parent
state.
"""

from __future__ import annotations

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.state import AgentState
from app.agent.subagents.product_rag.fault_tolerance import (
    _PRODUCT_RAG_RETRY_POLICY,
    _PRODUCT_RAG_TIMEOUT_POLICY,
    handle_hybrid_search_error,
    handle_llm_postprocess_error,
    handle_prepare_query_error,
)
from app.agent.subagents.product_rag.nodes import (
    hybrid_search_node,
    llm_postprocess_node,
    prepare_query_node,
)
from app.agent.subagents.product_rag.state import ProductRAGState

logger = structlog.get_logger(__name__)


# ── Subgraph builder ─────────────────────────────────────────────────────────


def _build_product_rag_graph() -> CompiledStateGraph:
    """Construct the ProductRAG 3-stage subgraph.

    Returns a compiled ``StateGraph`` with:

    - Shared retry + timeout policies applied to every node.
    - Per-node ``error_handler`` for graceful fallback.
    - ``checkpointer=None`` -- subgraph state is transient and the
      parent graph's ``AsyncPostgresSaver`` handles thread-level
      checkpoints.

    The returned graph is cached at module level as
    ``_PRODUCT_RAG_GRAPH`` and reused for every call; only the data
    passed at invocation time varies.
    """
    builder = StateGraph(ProductRAGState)

    # Apply shared retry + timeout to every node added below.
    builder.set_node_defaults(
        retry_policy=_PRODUCT_RAG_RETRY_POLICY,
        timeout=_PRODUCT_RAG_TIMEOUT_POLICY,
    )

    builder.add_node(
        "prepare_query_node",
        prepare_query_node,
        error_handler=handle_prepare_query_error,  # type: ignore[arg-type]
    )
    builder.add_node(
        "hybrid_search_node",
        hybrid_search_node,
        error_handler=handle_hybrid_search_error,  # type: ignore[arg-type]
    )
    builder.add_node(
        "llm_postprocess_node",
        llm_postprocess_node,
        error_handler=handle_llm_postprocess_error,  # type: ignore[arg-type]
    )

    builder.add_edge(START, "prepare_query_node")
    builder.add_edge("prepare_query_node", "hybrid_search_node")
    builder.add_edge("hybrid_search_node", "llm_postprocess_node")
    builder.add_edge("llm_postprocess_node", END)

    return builder.compile(checkpointer=None)


# Compiled once at import time.  Topology is static; per-call data
# flows through the invocation, not the graph definition.
_PRODUCT_RAG_GRAPH: CompiledStateGraph = _build_product_rag_graph()


# ── Public entry point used by the parent graph ─────────────────────────────


async def run_product_rag(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict:
    """Run the ProductRAG subgraph and return ``retrieved_products``.

    Translates the parent ``AgentState`` into a ``ProductRAGState``,
    invokes the compiled subgraph, and returns a partial state
    update containing only the ``retrieved_products`` field -- the
    intermediate ``query``, ``filters``, ``candidates`` fields are
    transient and intentionally not written back to the parent.

    The parent graph wires this function as a node::

        builder.add_node("run_product_rag", run_product_rag)

    The dual-path ``AsyncQdrantClient`` injection is forwarded
    through ``config["configurable"]``:

    - **Path A (production, Phase 7):** the chat endpoint
      (``api/chat.py``) injects
      ``config["configurable"]["qdrant_aclient"] = request.app.state.qdrant.client``
      so the subgraph reuses the shared singleton.
    - **Path B (Phase 4 only -- no API caller yet):** the key is
      absent, and ``hybrid_search_node`` builds a transient client
      and closes it on exit.  The chat endpoint stub returns 501,
      so Path B is what actually executes in Phase 4.

    Args:
        state: Current ``AgentState`` from the parent graph.
        config: Optional ``RunnableConfig`` forwarded by the parent.
            When provided, must carry ``configurable.qdrant_aclient``
            for Path A.

    Returns:
        ``{"retrieved_products": list[dict]}`` -- a list of product
        payload dicts in relevance order, or an empty list when
        nothing matched.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="product_rag",
    )

    sub_state: ProductRAGState = {
        "messages": list(state["messages"]),
        "correlation_id": state["correlation_id"],
        "summary": state.get("summary", "") or "",
        "user_profile": state.get("user_profile"),
    }

    invoke_config: RunnableConfig = {}
    if config is not None:
        invoke_config.update(config)
    invoke_config.setdefault("configurable", {})
    metadata = invoke_config.setdefault("metadata", {})
    metadata["correlation_id"] = state["correlation_id"]

    result = await _PRODUCT_RAG_GRAPH.ainvoke(sub_state, config=invoke_config)
    retrieved = result.get("retrieved_products", []) if isinstance(result, dict) else []

    logger.info(
        "product_rag_subgraph_completed",
        thread_id=state["thread_id"],
        retrieved_count=len(retrieved),
    )

    return {"retrieved_products": retrieved}
