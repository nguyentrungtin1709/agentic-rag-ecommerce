"""Unit tests for app.agent.subagents.trend_scout.agent (wrapper + subgraph).

The ``run_trend_scout`` function is the integration point between the
parent ``AgentState`` and the compiled TrendScout subgraph. These tests
mock the compiled graph (``_TREND_SCOUT_GRAPH``) and verify the wrapper:

- injects a ``SystemMessage`` (built via ``_build_trend_scout_system``)
  as ``messages[0]``,
- forwards ``generate_image`` into the sub-state,
- extracts ``structured_response`` and projects it back to the partial
  ``AgentState`` update,
- attaches ``correlation_id`` to ``config["metadata"]``,
- degrades gracefully to ``{trend_summary: None, image_prompt: None}``
  when the subgraph raises or returns a malformed result.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

if TYPE_CHECKING:
    from app.agent.state import AgentState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs: Any) -> AgentState:
    """Build a minimal ``AgentState`` dict for trend_scout wrapper tests."""
    base: dict = {
        "messages": [HumanMessage(content="I want a kettle design")],
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


@contextmanager
def patch_graph(return_value: object | None = None, side_effect: BaseException | None = None):
    """Patch ``_TREND_SCOUT_GRAPH.ainvoke`` and yield the mock so tests can inspect call args.

    Yielding the mock *inside* the active patch is what keeps the
    reference valid -- the patch is only active for the duration of
    the ``with`` block, so inspecting ``mock.call_args`` after exiting
    the block would silently re-bind to the unpatched graph.
    """
    mock_graph = MagicMock(name="_TREND_SCOUT_GRAPH")
    if side_effect is not None:
        mock_graph.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        mock_graph.ainvoke = AsyncMock(return_value=return_value)
    with patch(
        "app.agent.subagents.trend_scout.agent._TREND_SCOUT_GRAPH",
        mock_graph,
    ):
        yield mock_graph


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


async def test_run_trend_scout_returns_structured_output_fields_on_success() -> None:
    """``run_trend_scout`` must project ``structured_response`` to the partial AgentState update."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout
    from app.agent.subagents.trend_scout.schemas import TrendScoutOutput

    output = TrendScoutOutput(
        trend_summary="Cottagecore and earthy palettes are trending for 2026.",
        image_prompt="kettle with hand-painted lavender field, soft morning light",
    )
    state = _make_state()

    with patch_graph({"structured_response": output}):
        result = await run_trend_scout(state)

    assert result == {
        "trend_summary": output.trend_summary,
        "image_prompt": output.image_prompt,
    }


async def test_run_trend_scout_injects_system_message_as_first_message() -> None:
    """Wrapper must inject ``_build_trend_scout_system`` result as ``messages[0]``."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout
    from app.agent.subagents.trend_scout.schemas import TrendScoutOutput

    state = _make_state(
        summary="User previously asked about minimalist tees.",
        user_profile={"style_preferences": ["minimalist"]},
    )
    output = TrendScoutOutput(trend_summary="x", image_prompt=None)

    with patch_graph({"structured_response": output}) as mock_graph:
        await run_trend_scout(state)
        sub_state = mock_graph.ainvoke.call_args[0][0]

    messages = sub_state["messages"]
    assert isinstance(messages[0], SystemMessage)
    assert "Conversation history summary" in messages[0].content
    assert "minimalist" in messages[0].content
    # Original parent messages are preserved in order after the SystemMessage.
    assert messages[1:] == list(state["messages"])


async def test_run_trend_scout_forwards_generate_image_flag_to_subgraph() -> None:
    """Wrapper must forward ``generate_image`` into the sub-state."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout
    from app.agent.subagents.trend_scout.schemas import TrendScoutOutput

    output = TrendScoutOutput(trend_summary="x", image_prompt=None)
    state = _make_state(generate_image=True)

    with patch_graph({"structured_response": output}) as mock_graph:
        await run_trend_scout(state)
        sub_state = mock_graph.ainvoke.call_args[0][0]

    assert sub_state["generate_image"] is True


async def test_run_trend_scout_attaches_correlation_id_to_metadata() -> None:
    """Wrapper must attach ``correlation_id`` to ``config['metadata']`` (D11.9)."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout
    from app.agent.subagents.trend_scout.schemas import TrendScoutOutput

    output = TrendScoutOutput(trend_summary="x", image_prompt=None)
    state = _make_state(correlation_id="corr-abc-123")

    with patch_graph({"structured_response": output}) as mock_graph:
        await run_trend_scout(state)
        config = mock_graph.ainvoke.call_args.kwargs.get("config", {})

    assert config["metadata"]["correlation_id"] == "corr-abc-123"


async def test_run_trend_scout_merges_parent_config_when_provided() -> None:
    """When a parent ``config`` is supplied, wrapper must merge it before adding metadata."""
    from langchain_core.runnables import RunnableConfig

    from app.agent.subagents.trend_scout.agent import run_trend_scout
    from app.agent.subagents.trend_scout.schemas import TrendScoutOutput

    output = TrendScoutOutput(trend_summary="x", image_prompt=None)
    state = _make_state(correlation_id="corr-xyz")
    parent_config = cast(RunnableConfig, {"configurable": {"thread_id": "t-1"}})

    with patch_graph({"structured_response": output}) as mock_graph:
        await run_trend_scout(state, config=parent_config)
        config = mock_graph.ainvoke.call_args.kwargs.get("config", {})

    assert config["configurable"]["thread_id"] == "t-1"
    assert config["metadata"]["correlation_id"] == "corr-xyz"


async def test_run_trend_scout_returns_none_pair_when_subgraph_raises() -> None:
    """D11.6: when the subgraph raises, return ``{None, None}`` gracefully."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout

    state = _make_state()

    with patch_graph(side_effect=RuntimeError("graph boom")):
        result = await run_trend_scout(state)

    assert result == {"trend_summary": None, "image_prompt": None}


async def test_run_trend_scout_returns_none_pair_when_structured_response_missing() -> None:
    """D11.6: when ``structured_response`` is missing or wrong type, return ``{None, None}``."""
    from app.agent.subagents.trend_scout.agent import run_trend_scout

    state = _make_state()

    # Case 1: no structured_response key at all
    with patch_graph({}):
        result = await run_trend_scout(state)
    assert result == {"trend_summary": None, "image_prompt": None}

    # Case 2: structured_response present but not a TrendScoutOutput
    with patch_graph({"structured_response": {"trend_summary": "x", "image_prompt": None}}):
        result = await run_trend_scout(state)
    assert result == {"trend_summary": None, "image_prompt": None}


async def test_d11_12_d11_13_build_graph_exposes_only_tavily_and_summarization_middleware() -> None:
    """D11.12 + D11.13: ``_build_trend_scout_graph`` calls ``create_agent`` with
    exactly one tool (``tavily_search``) and a single ``SummarizationMiddleware``
    whose ``trigger`` is the ``("tokens", N)`` form.
    """
    from app.agent.subagents.trend_scout import agent as ts_agent

    fake_compiled = MagicMock(name="compiled_subgraph")
    fake_create_agent = MagicMock(return_value=fake_compiled)
    fake_middleware = MagicMock(name="summarization_middleware")

    with (
        patch.object(ts_agent, "create_agent", fake_create_agent),
        patch.object(ts_agent, "SummarizationMiddleware", fake_middleware),
    ):
        result = ts_agent._build_trend_scout_graph()

    assert result is fake_compiled  # no .compile() call -- D11.7 amended
    call_kwargs = fake_create_agent.call_args.kwargs
    assert call_kwargs["tools"] == [ts_agent.tavily_search], (
        "D11.12: create_agent must receive tools=[tavily_search] only"
    )
    assert call_kwargs["response_format"] is ts_agent.TrendScoutOutput
    assert call_kwargs["state_schema"] is ts_agent.TrendScoutState

    middleware_list = call_kwargs["middleware"]
    assert len(middleware_list) == 1
    assert middleware_list[0] is fake_middleware.return_value

    smw_kwargs = fake_middleware.call_args.kwargs
    trigger = smw_kwargs["trigger"]
    assert isinstance(trigger, tuple) and len(trigger) == 2
    assert trigger[0] == "tokens"
    assert isinstance(trigger[1], int) and trigger[1] > 0
    assert smw_kwargs["keep"] == ("messages", 20)
    assert smw_kwargs["model"] is not None
