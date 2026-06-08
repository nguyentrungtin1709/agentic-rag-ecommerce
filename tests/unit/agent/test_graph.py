"""Unit tests for app.agent.graph (topology and routing)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

if TYPE_CHECKING:
    from app.agent.state import AgentState


def _make_graph():
    """Build the graph with in-memory checkpointer and store."""
    from app.agent.graph import build_graph

    return build_graph(checkpointer=InMemorySaver(), store=InMemoryStore())


def test_graph_compiles(settings_overrides) -> None:
    """build_graph must return a compiled graph without raising."""
    graph = _make_graph()
    assert graph is not None


def test_graph_node_set(settings_overrides) -> None:
    """Compiled graph must contain exactly the expected node names."""
    graph = _make_graph()
    # get_graph() returns the underlying graph representation
    node_names = set(graph.get_graph().nodes.keys())
    expected = {
        "__start__",
        "profiler",
        "summarize",
        "orchestrate",
        "run_product_rag",
        "run_trend_scout",
        "synthesize",
        "generate_image",
        "generate_title",
        "__end__",
    }
    assert expected == node_names, f"Node mismatch. Got: {node_names}"


# ── route_orchestrate unit tests ─────────────────────────────────────────────


def _make_state(intent: str | None) -> AgentState:
    """Return a minimal state dict for route_orchestrate."""
    from app.agent.state import AgentState as _AgentState

    return cast(_AgentState, {"intent": intent})


def test_route_orchestrate_need_product_search(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("need_product_search")) == "run_product_rag"


def test_route_orchestrate_need_trend_info(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("need_trend_info")) == "run_trend_scout"


def test_route_orchestrate_sufficient(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("sufficient")) == "synthesize"


def test_route_orchestrate_clarification_needed(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("clarification_needed")) == "synthesize"


def test_route_orchestrate_out_of_scope(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("out_of_scope")) == "synthesize"


def test_route_orchestrate_fallback(settings_overrides) -> None:
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("fallback")) == "synthesize"


def test_route_orchestrate_none_intent(settings_overrides) -> None:
    """None intent (before orchestrate runs) must route to synthesize."""
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state(None)) == "synthesize"


def test_route_orchestrate_unknown_intent(settings_overrides) -> None:
    """Unknown/unexpected intent must fall back to synthesize."""
    from app.agent.graph import route_orchestrate

    assert route_orchestrate(_make_state("totally_unknown_intent")) == "synthesize"
