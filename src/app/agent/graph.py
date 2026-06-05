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
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agent.nodes.generate_image import generate_image
from app.agent.nodes.generate_title import generate_title
from app.agent.nodes.orchestrate import orchestrate
from app.agent.nodes.profiler import profiler
from app.agent.nodes.synthesize import synthesize
from app.agent.state import AgentState
from app.agent.subagents.product_rag.agent import run_product_rag
from app.agent.subagents.trend_scout.agent import run_trend_scout

logger = structlog.get_logger(__name__)


def build_graph(
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
) -> CompiledStateGraph:
    """Construct and compile the POD Stylist agent graph.

    # STUB — simplified linear scaffold.  Real topology per
    # docs/diagrams/05-agent-workflow.mermaid:
    #
    #   START --> TitleGen (parallel)
    #   START --> Profiler
    #   Profiler --> Orchestrate
    #   Orchestrate --> RAG | Trend | ResGen (conditional edges)
    #   RAG --> Orchestrate  (loop back)
    #   Trend --> Orchestrate (loop back)
    #   ResGen --> ImgGen (parallel) + Checkpointer
    #   ImgGen --> Checkpointer
    #   Checkpointer --> END
    #
    # This stub uses a single sequential chain for initial scaffolding only.

    Args:
        checkpointer: Short-term memory backend (``AsyncPostgresSaver``).
        store: Long-term memory backend (``AsyncPostgresStore``).

    Returns:
        Compiled ``StateGraph`` ready to invoke.
    """
    builder = StateGraph(AgentState)

    builder.add_node("profiler", profiler)
    builder.add_node("orchestrate", orchestrate)
    builder.add_node("run_product_rag", run_product_rag)
    builder.add_node("run_trend_scout", run_trend_scout)
    builder.add_node("synthesize", synthesize)
    builder.add_node("generate_image", generate_image)
    builder.add_node("generate_title", generate_title)

    builder.set_entry_point("profiler")
    builder.add_edge("profiler", "orchestrate")
    builder.add_edge("orchestrate", "run_product_rag")
    builder.add_edge("run_product_rag", "run_trend_scout")
    builder.add_edge("run_trend_scout", "synthesize")
    builder.add_edge("synthesize", "generate_image")
    builder.add_edge("generate_image", "generate_title")
    builder.add_edge("generate_title", END)

    graph = builder.compile(checkpointer=checkpointer, store=store)
    logger.info("Agent graph compiled")
    return graph
