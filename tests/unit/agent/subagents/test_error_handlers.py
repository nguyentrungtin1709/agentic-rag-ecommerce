"""Unit tests for app.agent.subagents.product_rag.fault_tolerance error handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import NodeError
from langgraph.types import Command

from app.agent.subagents.product_rag.fault_tolerance import (
    handle_hybrid_search_error,
    handle_llm_postprocess_error,
    handle_prepare_query_error,
)

if TYPE_CHECKING:
    from app.agent.subagents.product_rag.state import ProductRAGState


def _update(cmd: Command) -> dict:
    """Return ``cmd.update`` as a dict, asserting it is not None."""
    assert cmd.update is not None
    return cast("dict", cmd.update)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> ProductRAGState:
    """Build a minimal ProductRAGState dict for error handler tests."""
    base: dict = {
        "messages": [HumanMessage(content="Original user message")],
        "query": "",
        "candidates": [],
    }
    base.update(kwargs)
    return cast("ProductRAGState", base)


def _make_node_error(
    node: str = "prepare_query_node",
    exc: Exception | None = None,
) -> NodeError:
    """Build a NodeError for testing handlers."""
    return NodeError(node=node, error=exc or RuntimeError("boom"))


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


async def test_handle_prepare_query_error_falls_back_to_latest_human_message() -> None:
    """prepare_query fallback must reuse the latest HumanMessage content and goto hybrid_search."""
    state = _make_state(messages=[HumanMessage(content="user asked for a mug")])
    err = _make_node_error(node="prepare_query_node")

    cmd = await handle_prepare_query_error(state, err)

    assert isinstance(cmd, Command)
    assert cmd.update == {"query": "user asked for a mug", "filters": None}
    assert cmd.goto == "hybrid_search"


async def test_handle_prepare_query_error_picks_latest_among_multiple_messages() -> None:
    """prepare_query fallback must pick the most recent HumanMessage, not an earlier one."""
    state = _make_state(
        messages=[
            HumanMessage(content="first turn"),
            AIMessage(content="AI reply"),
            HumanMessage(content="second turn"),
            AIMessage(content="AI reply 2"),
            HumanMessage(content="latest turn -- should win"),
        ]
    )
    err = _make_node_error(node="prepare_query_node")

    cmd = await handle_prepare_query_error(state, err)

    assert _update(cmd)["query"] == "latest turn -- should win"


async def test_handle_prepare_query_error_handles_no_messages() -> None:
    """prepare_query fallback must produce an empty query when state has no messages."""
    state = _make_state(messages=[])
    err = _make_node_error(node="prepare_query_node")

    cmd = await handle_prepare_query_error(state, err)

    assert _update(cmd) == {"query": "", "filters": None}
    assert cmd.goto == "hybrid_search"


async def test_handle_prepare_query_error_skips_non_human_messages() -> None:
    """prepare_query fallback must skip AI/System messages and find the latest HumanMessage."""
    state = _make_state(
        messages=[
            SystemMessage(content="system"),
            AIMessage(content="AI only"),
            HumanMessage(content="real user message"),
        ]
    )
    err = _make_node_error(node="prepare_query_node")

    cmd = await handle_prepare_query_error(state, err)

    assert _update(cmd)["query"] == "real user message"


async def test_handle_hybrid_search_error_returns_empty_candidates() -> None:
    """hybrid_search fallback must set candidates=[] and goto llm_postprocess."""
    state = _make_state(query="any", filters={"available": True})
    err = _make_node_error(node="hybrid_search_node", exc=ConnectionError("qdrant down"))

    cmd = await handle_hybrid_search_error(state, err)

    assert isinstance(cmd, Command)
    assert cmd.update == {"candidates": []}
    assert cmd.goto == "llm_postprocess"


async def test_handle_llm_postprocess_error_falls_back_to_top_k_by_score() -> None:
    """llm_postprocess fallback must return top-K candidates by position."""
    candidates = [
        {"product_id": "p1", "name": "A"},
        {"product_id": "p2", "name": "B"},
        {"product_id": "p3", "name": "C"},
        {"product_id": "p4", "name": "D"},
        {"product_id": "p5", "name": "E"},
    ]
    state = _make_state(query="any", candidates=candidates)
    err = _make_node_error(node="llm_postprocess_node", exc=TimeoutError("openai slow"))

    cmd = await handle_llm_postprocess_error(state, err)

    assert isinstance(cmd, Command)
    update = _update(cmd)
    # settings.qdrant_rerank_top_k defaults to 3
    assert len(update["retrieved_products"]) == 3
    assert [p["product_id"] for p in update["retrieved_products"]] == ["p1", "p2", "p3"]
    assert cmd.goto == "__end__"


async def test_handle_llm_postprocess_error_caps_at_min_candidates() -> None:
    """llm_postprocess fallback must cap at min(top_k, len(candidates))."""
    candidates = [{"product_id": "p1"}, {"product_id": "p2"}]
    state = _make_state(query="any", candidates=candidates)
    err = _make_node_error(node="llm_postprocess_node")

    cmd = await handle_llm_postprocess_error(state, err)

    update = _update(cmd)
    assert len(update["retrieved_products"]) == 2
    assert cmd.goto == "__end__"
