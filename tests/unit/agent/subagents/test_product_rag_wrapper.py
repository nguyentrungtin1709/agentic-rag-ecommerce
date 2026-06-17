"""Unit tests for app.agent.subagents.product_rag.agent (wrapper + subgraph)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from app.agent.state import AgentState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> AgentState:
    """Build a minimal AgentState dict for product_rag wrapper tests."""
    base: dict = {
        "messages": [HumanMessage(content="I want a minimalist t-shirt")],
        "correlation_id": "test-corr-id",
        "user_id": "user-123",
        "thread_id": "thread-456",
        "summary": "",
        "generate_image": False,
        "first_user_message": None,
        "user_profile": None,
        "retrieved_products": [],
        "trend_summary": None,
        "thread_title": None,
        "intent": None,
        "title_generated": False,
        "fallback_count": 0,
        "image_url": None,
        "image_prompt": None,
    }
    base.update(kwargs)
    return cast("AgentState", base)


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    """Inject minimal env vars and clear settings cache around each test."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    yield
    get_settings.cache_clear()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_run_product_rag_returns_empty_when_subgraph_returns_empty() -> None:
    """run_product_rag must return retrieved_products=[] when the subgraph produces no products."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state()

    with patch.object(
        __import__("app.agent.subagents.product_rag.agent", fromlist=["_PRODUCT_RAG_GRAPH"]),
        "_PRODUCT_RAG_GRAPH",
    ) as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        result = await run_product_rag(state)

    assert result == {"retrieved_products": []}


async def test_run_product_rag_forwards_messages_list_to_subgraph() -> None:
    """run_product_rag must pass state['messages'] verbatim into the subgraph's messages field."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    messages = [
        HumanMessage(content="first question"),
        HumanMessage(content="latest question"),
    ]
    state = _make_state(messages=messages)

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state)

    sub_state = mock_graph.ainvoke.call_args[0][0]
    assert sub_state["messages"] == messages


async def test_run_product_rag_handles_empty_messages_list() -> None:
    """run_product_rag must pass an empty messages list to the subgraph when parent has none."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state(messages=[])

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state)

    sub_state = mock_graph.ainvoke.call_args[0][0]
    assert sub_state["messages"] == []


async def test_run_product_rag_forwards_summary_and_user_profile() -> None:
    """run_product_rag must pass summary and user_profile from AgentState to the subgraph."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state(
        summary="Earlier conversation about gifts.",
        user_profile={"style_preferences": ["minimalist"]},
    )

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state)

    sub_state = mock_graph.ainvoke.call_args[0][0]
    assert sub_state["summary"] == "Earlier conversation about gifts."
    assert sub_state["user_profile"] == {"style_preferences": ["minimalist"]}


async def test_run_product_rag_forwards_correlation_id_in_sub_state_and_metadata() -> None:
    """run_product_rag forwards correlation_id to subgraph via sub_state and config metadata."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state(correlation_id="abc-123")

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state)

    sub_state = mock_graph.ainvoke.call_args[0][0]
    assert sub_state["correlation_id"] == "abc-123"

    config = mock_graph.ainvoke.call_args.kwargs.get("config", {})
    assert config["metadata"]["correlation_id"] == "abc-123"


async def test_run_product_rag_path_a_forwards_aclient_to_subgraph() -> None:
    """run_product_rag must forward Path A's aclient via configurable.qdrant_aclient."""
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state()
    aclient = MagicMock()
    parent_config = cast(RunnableConfig, {"configurable": {"qdrant_aclient": aclient}})

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state, config=parent_config)

    config = mock_graph.ainvoke.call_args.kwargs.get("config", {})
    assert config["configurable"]["qdrant_aclient"] is aclient


async def test_run_product_rag_path_b_works_without_injected_aclient() -> None:
    """run_product_rag must invoke the subgraph with no qdrant_aclient when Path B is in effect.

    Path B (Phase 4 only) leaves config['configurable'] without the key,
    so hybrid_search_node will build a transient AsyncQdrantClient.
    """
    from app.agent.subagents.product_rag.agent import run_product_rag

    state = _make_state()
    parent_config = cast(RunnableConfig, {"configurable": {}})  # Path B: no aclient

    with patch("app.agent.subagents.product_rag.agent._PRODUCT_RAG_GRAPH") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={"retrieved_products": []})
        await run_product_rag(state, config=parent_config)

    config = mock_graph.ainvoke.call_args.kwargs.get("config", {})
    assert "qdrant_aclient" not in config["configurable"]
