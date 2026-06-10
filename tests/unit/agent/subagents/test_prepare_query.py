"""Unit tests for app.agent.subagents.product_rag.nodes.prepare_query_node."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.subagents.product_rag.schemas import PrepareQueryOutput

if TYPE_CHECKING:
    from app.agent.subagents.product_rag.state import ProductRAGState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> ProductRAGState:
    """Build a minimal ProductRAGState dict for prepare_query tests."""
    base: dict = {
        "messages": [HumanMessage(content="I want a minimalist t-shirt")],
        "summary": "",
        "user_profile": None,
    }
    base.update(kwargs)
    return cast("ProductRAGState", base)


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


async def test_prepare_query_returns_query_and_filters() -> None:
    """prepare_query_node must return a dict with 'query' and 'filters' keys."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state()
    parsed = PrepareQueryOutput(
        query="minimalist cotton t-shirt",
        available=True,
        price_max=200.0,
    )

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await prepare_query_node(state)

    assert result["query"] == "minimalist cotton t-shirt"
    assert result["filters"] == {"available": True, "price_max": 200.0}


async def test_prepare_query_omits_filters_dict_when_both_null() -> None:
    """prepare_query_node must set filters=None when LLM returns no constraints."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state()
    parsed = PrepareQueryOutput(query="birthday gift mug", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await prepare_query_node(state)

    assert result["query"] == "birthday gift mug"
    assert result["filters"] is None


async def test_prepare_query_only_availability_filter() -> None:
    """prepare_query_node must produce a single-key filters dict for available only."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state()
    parsed = PrepareQueryOutput(query="summer dress", available=True, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await prepare_query_node(state)

    assert result["filters"] == {"available": True, "price_max": None}


async def test_prepare_query_only_price_max_filter() -> None:
    """prepare_query_node must produce a single-key filters dict for price_max only."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state()
    parsed = PrepareQueryOutput(query="canvas tote", available=None, price_max=150.0)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await prepare_query_node(state)

    assert result["filters"] == {"available": None, "price_max": 150.0}


async def test_prepare_query_uses_orchestrator_model() -> None:
    """prepare_query_node must call ChatOpenAI with the configured orchestrator model."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node
    from app.config import get_settings

    state = _make_state()
    parsed = PrepareQueryOutput(query="any", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await prepare_query_node(state)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == get_settings().orchestrator_model


async def test_prepare_query_system_message_includes_base_and_context() -> None:
    """prepare_query_node must compose SystemMessage from base + summary + user_profile."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state(
        messages=[HumanMessage(content="Latest user input")],
        summary="Existing summary digest",
        user_profile={"style_preferences": ["minimalist"]},
    )
    parsed = PrepareQueryOutput(query="any", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await prepare_query_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        assert isinstance(call_messages[0], SystemMessage)
        system_content = call_messages[0].content
        # base prompt always present
        assert "search query optimizer" in system_content
        # injected sections
        assert "## Conversation Summary" in system_content
        assert "Existing summary digest" in system_content
        assert "## User Profile" in system_content
        assert "minimalist" in system_content


async def test_prepare_query_omits_context_sections_when_empty() -> None:
    """prepare_query_node must not inject empty Conversation Summary / User Profile sections."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state(summary="", user_profile=None)
    parsed = PrepareQueryOutput(query="any", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await prepare_query_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        system_content = call_messages[0].content
        assert "## Conversation Summary" not in system_content
        assert "## User Profile" not in system_content


async def test_prepare_query_unpacks_messages_into_human_messages() -> None:
    """prepare_query_node must unpack state['messages'] verbatim into the LLM call."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    messages = [
        HumanMessage(content="First turn"),
        AIMessage(content="AI reply"),
        HumanMessage(content="Second turn"),
    ]
    state = _make_state(messages=messages)
    parsed = PrepareQueryOutput(query="any", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await prepare_query_node(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        # SystemMessage + 3 forwarded messages
        assert len(call_messages) == 4
        assert isinstance(call_messages[0], SystemMessage)
        for i, src in enumerate(messages, start=1):
            assert call_messages[i] is src


async def test_prepare_query_handles_empty_messages() -> None:
    """prepare_query_node must not raise when state['messages'] is empty.

    The LLM call then contains only the SystemMessage; no HumanMessage
    is forwarded.
    """
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state(messages=[])
    parsed = PrepareQueryOutput(query="", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await prepare_query_node(state)

    assert result["query"] == ""
    assert result["filters"] is None
    call_messages = mock_llm.ainvoke.call_args[0][0]
    assert len(call_messages) == 1
    assert isinstance(call_messages[0], SystemMessage)


async def test_prepare_query_propagates_correlation_id_in_metadata() -> None:
    """prepare_query_node must forward correlation_id via config['metadata']."""
    from app.agent.subagents.product_rag.nodes import prepare_query_node

    state = _make_state(correlation_id="corr-abc-123")
    parsed = PrepareQueryOutput(query="any", available=None, price_max=None)

    with patch("app.agent.subagents.product_rag.nodes.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=parsed)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await prepare_query_node(state)

        metadata = mock_llm.ainvoke.call_args.kwargs["config"]["metadata"]
        assert metadata["correlation_id"] == "corr-abc-123"
