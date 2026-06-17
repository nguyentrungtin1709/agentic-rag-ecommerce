"""Unit tests for app.agent.nodes.summarize."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

if TYPE_CHECKING:
    from app.agent.state import AgentState

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_messages(count: int) -> list:
    """Build an alternating HumanMessage / AIMessage list with stable IDs."""
    msgs = []
    for i in range(count):
        if i % 2 == 0:
            msgs.append(HumanMessage(content=f"User message {i}", id=f"msg-{i}"))
        else:
            msgs.append(AIMessage(content=f"AI message {i}", id=f"msg-{i}"))
    return msgs


def _make_state(**kwargs) -> AgentState:
    """Build a minimal AgentState dict for summarize tests."""
    base: dict = {
        "messages": [],
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
    """Inject minimal env vars and clear the settings cache around each test."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    yield
    get_settings.cache_clear()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_summarize_returns_empty_dict_below_threshold() -> None:
    """SummarizeNode must return {} when message count is below the threshold (default 12)."""
    from app.agent.nodes.summarize import summarize

    state = _make_state(messages=_make_messages(5))
    result = await summarize(state)
    assert result == {}


async def test_summarize_triggers_llm_at_threshold() -> None:
    """SummarizeNode must call the LLM when message count reaches the threshold."""
    from app.agent.nodes.summarize import summarize

    state = _make_state(messages=_make_messages(12))
    mock_response = MagicMock()
    mock_response.content = "Summary text"

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_llm

        result = await summarize(state)

    mock_llm.ainvoke.assert_awaited_once()
    assert result["summary"] == "Summary text"


async def test_summarize_uses_extend_prompt_when_summary_exists() -> None:
    """SummarizeNode must use the extend-summary instruction when state['summary'] is non-empty."""
    from app.agent.nodes.summarize import summarize

    state = _make_state(
        messages=_make_messages(12),
        summary="Existing summary about user preferences.",
    )
    mock_response = MagicMock()
    mock_response.content = "Updated summary"

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_llm

        await summarize(state)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        last_msg = call_messages[-1]
        assert isinstance(last_msg, HumanMessage)
        assert "Existing summary" in last_msg.content
        assert "Incorporate" in last_msg.content


async def test_summarize_returns_correct_remove_message_ops() -> None:
    """SummarizeNode must return RemoveMessage ops for exactly the oldest N messages."""
    from app.agent.nodes.summarize import summarize

    messages = _make_messages(12)
    state = _make_state(messages=messages)
    mock_response = MagicMock()
    mock_response.content = "Summary"

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_llm

        result = await summarize(state)

    delete_ops = result["messages"]
    # message_summarize_count defaults to 8
    assert len(delete_ops) == 8
    assert all(isinstance(op, RemoveMessage) for op in delete_ops)
    expected_ids = {m.id for m in messages[:8]}
    actual_ids = {op.id for op in delete_ops}
    assert expected_ids == actual_ids


async def test_summarize_returns_new_summary_in_state_update() -> None:
    """SummarizeNode must return the LLM-generated summary string in the state update."""
    from app.agent.nodes.summarize import summarize

    state = _make_state(messages=_make_messages(12))
    mock_response = MagicMock()
    mock_response.content = "The user expressed interest in vintage t-shirts."

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_llm

        result = await summarize(state)

    assert result["summary"] == "The user expressed interest in vintage t-shirts."


async def test_summarize_adjusts_cut_point_to_human_message_boundary() -> None:
    """SummarizeNode must walk the cut point back until messages[cut] is a HumanMessage.

    Scenario: 12 messages where index 8 (the default cut) is an AIMessage.
    The node must reduce cut to 7 so the remaining list starts with messages[7],
    which is a HumanMessage.
    """
    from app.agent.nodes.summarize import summarize

    # Build 12 messages all as HumanMessage except index 8, which is an AIMessage,
    # making the default cut point (8) invalid.
    msgs = []
    for i in range(12):
        if i == 8:
            msgs.append(AIMessage(content=f"AI message {i}", id=f"msg-{i}"))
        else:
            msgs.append(HumanMessage(content=f"Human message {i}", id=f"msg-{i}"))

    state = _make_state(messages=msgs)
    mock_response = MagicMock()
    mock_response.content = "Summary"

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_llm

        result = await summarize(state)

    # Cut must have been reduced from 8 to 7
    delete_ops = result["messages"]
    assert len(delete_ops) == 7
    removed_ids = {op.id for op in delete_ops}
    assert removed_ids == {f"msg-{i}" for i in range(7)}
    # The first surviving message (index 7) must be a HumanMessage
    surviving_first = msgs[7]
    assert isinstance(surviving_first, HumanMessage)


async def test_summarize_skips_when_no_human_boundary_found() -> None:
    """SummarizeNode must return {} without calling LLM when all candidate
    boundary positions are non-HumanMessage (cut would reach 0)."""
    from app.agent.nodes.summarize import summarize

    # First message is an AIMessage — makes it impossible to find a valid cut
    msgs = [AIMessage(content=f"AI {i}", id=f"msg-{i}") for i in range(12)]
    state = _make_state(messages=msgs)

    with patch("app.agent.nodes.summarize.ChatOpenAI") as mock_cls:
        result = await summarize(state)
        mock_cls.assert_not_called()

    assert result == {}
