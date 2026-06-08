"""LangGraph agent graph for the POD Stylist.

Assembles the five nodes and two subagents into a compiled
``StateGraph``.  Checkpoints are stored in PostgreSQL via
``AsyncPostgresSaver`` (short-term per-thread memory); long-term user
profiles use ``AsyncPostgresStore``.

Usage at startup (inside FastAPI ``lifespan``):

    from app.agent.graph import build_graph

    checkpointer = AsyncPostgresSaver(psycopg_pool)
    store = AsyncPostgresStore(psycopg_pool)
    await checkpointer.setup()
    await store.setup()
    graph = build_graph(checkpointer=checkpointer, store=store)
"""

from __future__ import annotations

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agent.nodes.generate_image import generate_image
from app.agent.nodes.generate_title import generate_title
from app.agent.nodes.orchestrate import orchestrate
from app.agent.nodes.profiler import profiler
from app.agent.nodes.summarize import summarize
from app.agent.nodes.synthesize import synthesize
from app.agent.state import AgentState
from app.agent.subagents.product_rag.agent import run_product_rag
from app.agent.subagents.trend_scout.agent import run_trend_scout
from app.config import get_settings

logger = structlog.get_logger(__name__)


def route_orchestrate(state: AgentState) -> str:
    """Map ``AgentState.intent`` to the next node key.

    Called by the conditional edge attached to the ``orchestrate`` node.
    Returns ``"synthesize"`` for all intents that do not require further
    retrieval (``sufficient``, ``clarification_needed``, ``out_of_scope``,
    ``fallback``) as well as for any unexpected/unknown intent value.

    Args:
        state: Current agent state after the orchestrate node has run.

    Returns:
        One of ``"run_product_rag"``, ``"run_trend_scout"``, or ``"synthesize"``.
    """
    intent = state.get("intent")
    if intent == "need_product_search":
        return "run_product_rag"
    if intent == "need_trend_info":
        return "run_trend_scout"
    # sufficient, clarification_needed, out_of_scope, fallback, None, unknown
    return "synthesize"


def build_graph(
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
) -> CompiledStateGraph:
    """Construct and compile the POD Stylist agent graph.

    Full conditional topology per ``docs/diagrams/05-agent-workflow.mermaid``
    and Section 1.3 of DRAFT 0.6:

        START --> profiler          (main pipeline)
        START --> title_generation  (parallel branch; node is no-op when title_generated=True)

        title_generation --> END

        profiler --> summarize
        summarize --> orchestrate

        orchestrate --[route_orchestrate]--> run_product_rag
                                          | run_trend_scout
                                          | synthesize

        run_product_rag --> orchestrate  (loop back)
        run_trend_scout --> orchestrate  (loop back)

        synthesize --> image_generation  (parallel branch)
        synthesize --> END

        image_generation --> END

    Args:
        checkpointer: Short-term memory backend (``AsyncPostgresSaver``).
        store: Long-term memory backend (``AsyncPostgresStore``).

    Returns:
        Compiled ``StateGraph`` ready to invoke.
    """
    settings = get_settings()
    builder = StateGraph(AgentState)

    # ── Register nodes ──────────────────────────────────────────────────────
    builder.add_node("profiler", profiler)
    builder.add_node("summarize", summarize)
    builder.add_node("orchestrate", orchestrate)
    builder.add_node("run_product_rag", run_product_rag)
    builder.add_node("run_trend_scout", run_trend_scout)
    builder.add_node("synthesize", synthesize)
    builder.add_node("generate_image", generate_image)
    builder.add_node("generate_title", generate_title)

    # ── Main pipeline: START --> profiler --> summarize --> orchestrate ─────
    builder.add_edge(START, "profiler")
    builder.add_edge("profiler", "summarize")
    builder.add_edge("summarize", "orchestrate")

    # ── Parallel title-generation branch from START ─────────────────────────
    builder.add_edge(START, "generate_title")
    builder.add_edge("generate_title", END)

    # ── Conditional routing from orchestrate ───────────────────────────────
    builder.add_conditional_edges(
        "orchestrate",
        route_orchestrate,
        {
            "run_product_rag": "run_product_rag",
            "run_trend_scout": "run_trend_scout",
            "synthesize": "synthesize",
        },
    )

    # ── Loop-backs from subagents to orchestrate ────────────────────────────
    builder.add_edge("run_product_rag", "orchestrate")
    builder.add_edge("run_trend_scout", "orchestrate")

    # ── Synthesize fans out to image_generation (parallel) and END ─────────
    builder.add_edge("synthesize", "generate_image")
    builder.add_edge("synthesize", END)
    builder.add_edge("generate_image", END)

    graph = builder.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=None,
        interrupt_after=None,
    )

    logger.info(
        "Agent graph compiled",
        nodes=list(builder.nodes.keys()),
        recursion_limit=settings.max_agent_steps,
    )
    return graph
