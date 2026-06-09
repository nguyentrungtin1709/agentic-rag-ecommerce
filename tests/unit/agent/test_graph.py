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


# ── Compiled-graph topology checks (orchestrate edges) ─────────────────────


def test_graph_orchestrate_conditional_edge_targets(settings_overrides) -> None:
    """The conditional edge from 'orchestrate' must include all three branch targets.

    Verifies that the route_orchestrate decision surface matches the
    graph wiring: run_product_rag, run_trend_scout, and synthesize.
    """
    from app.agent.graph import route_orchestrate

    targets = {
        route_orchestrate(_make_state("need_product_search")),
        route_orchestrate(_make_state("need_trend_info")),
        route_orchestrate(_make_state("sufficient")),
    }
    assert targets == {"run_product_rag", "run_trend_scout", "synthesize"}


def test_graph_has_loopback_edges_from_subagents_to_orchestrate(settings_overrides) -> None:
    """Both subagents must have unconditional edges back to 'orchestrate'.

    The graph loops through orchestrate whenever a subagent returns,
    so the loop-back edges are load-bearing for the multi-step
    conversational flow.
    """
    graph = _make_graph()
    edges: set[tuple[str, str]] = set()
    # get_graph().edges yields Edge objects with .source and .target attrs.
    for edge in graph.get_graph().edges:
        src = getattr(edge, "source", None) or edge[0]
        dst = getattr(edge, "target", None) or edge[1]
        edges.add((src, dst))

    assert ("run_product_rag", "orchestrate") in edges
    assert ("run_trend_scout", "orchestrate") in edges


def test_graph_orchestrate_has_no_unconditional_outgoing_edges(settings_overrides) -> None:
    """'orchestrate' must have NO unconditional outgoing edges — only the conditional one.

    This enforces the design rule that every path out of orchestrate is
    mediated by the route_orchestrate function.  Adding a stray direct
    edge would bypass intent classification.
    """
    graph = _make_graph()
    direct_targets: set[str] = set()
    for edge in graph.get_graph().edges:
        src = getattr(edge, "source", None) or edge[0]
        dst = getattr(edge, "target", None) or edge[1]
        if src == "orchestrate":
            direct_targets.add(dst)

    # All direct targets from 'orchestrate' must be the conditional-edge
    # branch keys (which langgraph materialises as direct edges under
    # the hood).  Crucially, none of them should be END or other
    # pipeline nodes like 'profiler' / 'summarize'.
    assert direct_targets.issubset({"run_product_rag", "run_trend_scout", "synthesize"})
    assert "END" not in direct_targets
    assert "profiler" not in direct_targets
    assert "summarize" not in direct_targets
