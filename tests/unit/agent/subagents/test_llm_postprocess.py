"""Unit tests for app.agent.subagents.product_rag.nodes.llm_postprocess_node."""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

if TYPE_CHECKING:
    from app.agent.subagents.product_rag.state import ProductRAGState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> ProductRAGState:
    """Build a minimal ProductRAGState dict for llm_postprocess tests."""
    base: dict = {
        "query": "minimalist cotton t-shirt",
        "candidates": [],
        "summary": "",
        "user_profile": None,
    }
    base.update(kwargs)
    return cast("ProductRAGState", base)


def _make_candidate(product_id: str, name: str = "Item", **extra: object) -> dict:
    """Build a candidate product payload dict."""
    return {
        "product_id": product_id,
        "name": name,
        "category": "t-shirt",
        "price_range": "100k VND",
        "available": True,
        **extra,
    }


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


async def test_rerank_returns_empty_when_no_candidates() -> None:
    """llm_postprocess_node must return retrieved_products=[] and skip LLM when no candidates."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(candidates=[])

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        result = await llm_postprocess_node(state)
        mock_cls.assert_not_called()

    assert result == {"retrieved_products": []}


async def test_rerank_returns_reranked_payloads_in_order() -> None:
    """llm_postprocess_node must return candidates in the order returned by the LLM."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        query="birthday gift for mom",
        candidates=[
            _make_candidate("prod_a", "Alpha"),
            _make_candidate("prod_b", "Bravo"),
            _make_candidate("prod_c", "Charlie"),
        ],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_b", "prod_a", "prod_c"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await llm_postprocess_node(state)

    assert [p["product_id"] for p in result["retrieved_products"]] == [
        "prod_b",
        "prod_a",
        "prod_c",
    ]


async def test_rerank_caps_result_at_top_k() -> None:
    """llm_postprocess_node must cap retrieved_products at qdrant_rerank_top_k (default 3)."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        query="any",
        candidates=[_make_candidate(f"prod_{i}", f"Item {i}") for i in range(5)],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_0", "prod_1", "prod_2"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await llm_postprocess_node(state)

    assert len(result["retrieved_products"]) == 3


async def test_rerank_fills_with_candidates_when_llm_returns_unknown_ids() -> None:
    """llm_postprocess_node must fall back to remaining candidates for unknown IDs."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        query="any",
        candidates=[
            _make_candidate("prod_a", "Alpha"),
            _make_candidate("prod_b", "Bravo"),
            _make_candidate("prod_c", "Charlie"),
        ],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        # Only prod_a is real; prod_unknown doesn't exist; remaining slots filled
        # by walking the original candidate list in order.
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a", "prod_unknown"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await llm_postprocess_node(state)

    ids = [p["product_id"] for p in result["retrieved_products"]]
    assert ids[0] == "prod_a"
    # The remaining slots must be filled from the candidate list (prod_b then prod_c).
    assert "prod_b" in ids
    assert "prod_c" in ids
    assert len(ids) == 3


async def test_rerank_human_message_contains_query_and_candidates() -> None:
    """llm_postprocess_node must put the rewritten query + candidate list in the HumanMessage."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        query="birthday gift",
        candidates=[
            _make_candidate("prod_a", "Alpha"),
            _make_candidate("prod_b", "Bravo"),
        ],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a", "prod_b"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        # SystemMessage + 1 HumanMessage (no forwarded raw messages)
        assert len(call_messages) == 2
        assert isinstance(call_messages[0], SystemMessage)
        assert isinstance(call_messages[1], HumanMessage)
        human_content = call_messages[1].content
        assert "birthday gift" in human_content
        assert "prod_a" in human_content
        assert "prod_b" in human_content
        assert "Candidate products" in human_content
        assert "top 3" in human_content


async def test_rerank_uses_rerank_model() -> None:
    """llm_postprocess_node must call ChatOpenAI with the configured rerank model."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node
    from app.config import get_settings

    state = _make_state(
        candidates=[_make_candidate("prod_a", "Alpha")],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == get_settings().rerank_model


async def test_rerank_uses_structured_output_list_str() -> None:
    """llm_postprocess_node must call with_structured_output with list[str] as the schema."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        candidates=[_make_candidate("prod_a", "Alpha")],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        schema_arg = mock_cls.return_value.with_structured_output.call_args[0][0]
        # Compare by origin + args to avoid relying on identity of generic aliases.
        assert typing.get_origin(schema_arg) is list
        assert typing.get_args(schema_arg) == (str,)


async def test_rerank_system_message_includes_base_and_context() -> None:
    """llm_postprocess_node must compose SystemMessage from base + summary + user_profile."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        candidates=[_make_candidate("prod_a", "Alpha")],
        summary="Earlier conversation digest",
        user_profile={"budget_range": "under 200k VND"},
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        system_content = call_messages[0].content
        # base prompt present
        assert "product relevance ranker" in system_content
        # injected sections present
        assert "## Conversation Summary" in system_content
        assert "Earlier conversation digest" in system_content
        assert "## User Profile" in system_content
        assert "under 200k VND" in system_content


async def test_rerank_omits_context_sections_when_empty() -> None:
    """llm_postprocess_node must not inject empty Conversation Summary / User Profile sections."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        candidates=[_make_candidate("prod_a", "Alpha")],
        summary="",
        user_profile=None,
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        system_content = call_messages[0].content
        assert "## Conversation Summary" not in system_content
        assert "## User Profile" not in system_content


async def test_rerank_does_not_forward_state_messages() -> None:
    """llm_postprocess_node must NOT unpack state['messages']; the rewritten query is sufficient."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        messages=[HumanMessage(content="raw conversation text -- should not appear")],
        candidates=[_make_candidate("prod_a", "Alpha")],
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        # SystemMessage + 1 HumanMessage; raw messages must not be unpacked.
        assert len(call_messages) == 2
        human_content = call_messages[1].content
        assert "raw conversation text" not in human_content


async def test_rerank_propagates_correlation_id_in_metadata() -> None:
    """llm_postprocess_node must forward correlation_id via config['metadata']."""
    from app.agent.subagents.product_rag.nodes import llm_postprocess_node

    state = _make_state(
        candidates=[_make_candidate("prod_a", "Alpha")],
        correlation_id="corr-xyz-789",
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=["prod_a"])
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await llm_postprocess_node(state)

        metadata = mock_llm.ainvoke.call_args.kwargs["config"]["metadata"]
        assert metadata["correlation_id"] == "corr-xyz-789"
