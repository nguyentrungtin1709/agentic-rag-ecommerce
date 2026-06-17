"""Unit tests for app.agent.nodes.profiler."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.models.profile import UserProfile

if TYPE_CHECKING:
    from app.agent.state import AgentState

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> AgentState:
    """Build a minimal AgentState dict for profiler tests."""
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


def _make_store(profile_value: dict | None = None) -> MagicMock:
    """Build a mock AsyncPostgresStore."""
    store = MagicMock()
    if profile_value is not None:
        item = MagicMock()
        item.value = profile_value
        store.aget = AsyncMock(return_value=item)
    else:
        store.aget = AsyncMock(return_value=None)
    store.aput = AsyncMock()
    return store


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


async def test_profiler_creates_profile_when_store_is_empty() -> None:
    """ProfilerNode must create an empty default profile when no existing profile is found."""
    from app.agent.nodes.profiler import profiler

    store = _make_store(profile_value=None)
    state = _make_state(messages=[HumanMessage(content="I want a minimalist t-shirt")])
    merged = UserProfile(style_preferences=["minimalist"])

    with patch("app.agent.nodes.profiler.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=merged)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await profiler(state, store)

    assert result["user_profile"]["style_preferences"] == ["minimalist"]


async def test_profiler_returns_unchanged_when_no_human_message() -> None:
    """ProfilerNode must skip the LLM call when no HumanMessage is present in state."""
    from app.agent.nodes.profiler import profiler

    existing = {
        "age_group": None,
        "style_preferences": ["vintage"],
        "product_interests": [],
        "occasion_context": None,
        "recipient_context": None,
        "budget_range": None,
    }
    store = _make_store(profile_value=existing)
    state = _make_state(messages=[AIMessage(content="How can I help?")])

    with patch("app.agent.nodes.profiler.ChatOpenAI") as mock_cls:
        result = await profiler(state, store)
        mock_cls.assert_not_called()

    assert result["user_profile"]["style_preferences"] == ["vintage"]


async def test_profiler_llm_called_with_two_fields_only() -> None:
    """ProfilerNode must pass exactly 2 fields to the LLM: current_profile + latest_message."""
    from app.agent.nodes.profiler import profiler

    store = _make_store(profile_value=None)
    state = _make_state(messages=[HumanMessage(content="I like bold designs")])
    merged = UserProfile()

    with patch("app.agent.nodes.profiler.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=merged)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await profiler(state, store)

        call_messages = mock_llm.ainvoke.call_args[0][0]
        # Must be exactly 2 messages: SystemMessage + HumanMessage
        assert len(call_messages) == 2
        # The human message body must contain exactly the two expected keys
        body = json.loads(call_messages[1].content)
        assert set(body.keys()) == {"current_profile", "latest_message"}


async def test_profiler_writes_merged_profile_to_store() -> None:
    """ProfilerNode must persist the merged profile via store.aput with correct args."""
    from app.agent.nodes.profiler import profiler

    store = _make_store(profile_value=None)
    state = _make_state(messages=[HumanMessage(content="I like mugs")])
    merged = UserProfile(product_interests=["mug"])

    with patch("app.agent.nodes.profiler.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=merged)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        await profiler(state, store)

    store.aput.assert_awaited_once()
    namespace, key, value = store.aput.call_args[0]
    assert namespace == ("profiles", "user-123")
    assert key == "profile"
    assert value["product_interests"] == ["mug"]


async def test_profiler_returns_user_profile_in_state_update() -> None:
    """ProfilerNode must return {'user_profile': <dict>} as the partial state update."""
    from app.agent.nodes.profiler import profiler

    store = _make_store(profile_value=None)
    state = _make_state(messages=[HumanMessage(content="Gift for my mom")])
    merged = UserProfile(recipient_context="mom")

    with patch("app.agent.nodes.profiler.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=merged)
        mock_cls.return_value.with_structured_output.return_value = mock_llm

        result = await profiler(state, store)

    assert "user_profile" in result
    assert result["user_profile"]["recipient_context"] == "mom"
