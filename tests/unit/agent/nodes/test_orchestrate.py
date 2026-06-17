"""Unit tests for app.agent.nodes.orchestrate.

Covers the central LLM intent classifier:

* Step-budget guard (forced fallback when remaining_steps is low).
* LLM call is made with the ``update_intent`` tool bound.
* Each of the 5 main intents is extracted correctly.
* Defensive fallbacks: no tool call, unknown intent value.
* Context notes about ``retrieved_products``, ``trend_summary``,
  ``image_prompt``, and the ``generate_image`` flag (positive or
  negative) are appended to the LLM input but never written to
  ``state["messages"]``.
* The correlation_id is forwarded as LangChain metadata.
* (CRITICAL) the node must NOT mutate ``state["messages"]`` — the
  ``add_messages`` reducer would otherwise accumulate duplicate
  context notes on every loop iteration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from app.agent.state import AgentState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs: Any) -> AgentState:
    """Build a minimal AgentState dict for orchestrate tests."""
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


def _make_config(remaining_steps: int) -> RunnableConfig:
    """Build a RunnableConfig carrying configurable.remaining_steps."""
    return cast(
        "RunnableConfig",
        {"configurable": {"remaining_steps": remaining_steps}},
    )


def _make_ai_message(intent_value: str | None) -> AIMessage:
    """Build a fake LLM response carrying a single tool call (or none)."""
    if intent_value is None:
        return AIMessage(content="no tool call", tool_calls=[])
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "update_intent",
                "args": {"intent": intent_value},
                "id": "call_1",
            }
        ],
    )


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    """Inject minimal env vars and clear the settings cache around each test."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    # Lower the default threshold so the guard path can be triggered easily.
    monkeypatch.setenv("AGENT_FALLBACK_THRESHOLD", "2")
    yield
    get_settings.cache_clear()


# ── Step-budget guard (Tests 1-2) ────────────────────────────────────────────


async def test_orchestrate_forces_fallback_when_remaining_steps_below_threshold() -> None:
    """remaining_steps == threshold (default 2) must skip the LLM and return fallback."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=2)

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        result = await orchestrate(state, config)
        mock_cls.assert_not_called()

    assert result == {"intent": "fallback"}


async def test_orchestrate_forces_fallback_when_remaining_steps_zero() -> None:
    """remaining_steps == 0 (e.g. recursion exhausted) must return fallback safely."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=0)

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        result = await orchestrate(state, config)
        mock_cls.assert_not_called()

    assert result == {"intent": "fallback"}


# ── LLM call & tool binding (Test 3) ─────────────────────────────────────────


async def test_orchestrate_binds_update_intent_tool() -> None:
    """ChatOpenAI must be called with the update_intent tool bound exactly once."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=10)

    fake_response = _make_ai_message("need_product_search")
    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

    # bind_tools must be called exactly once
    mock_llm.bind_tools.assert_called_once()
    bound_tools = mock_llm.bind_tools.call_args[0][0]
    assert len(bound_tools) == 1
    # The tool's name must be 'update_intent'
    assert bound_tools[0].name == "update_intent"


# ── Intent extraction — 5 happy paths (Tests 4-8) ───────────────────────────


@pytest.mark.parametrize(
    "intent_value",
    [
        "need_product_search",
        "need_trend_info",
        "sufficient",
        "clarification_needed",
        "out_of_scope",
    ],
)
async def test_orchestrate_extracts_each_main_intent(intent_value: str) -> None:
    """Each of the 5 main intent values must be extracted from the tool call."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message(intent_value)

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await orchestrate(state, config)

    assert result == {"intent": intent_value}


# ── Context notes (Tests 9-11) ───────────────────────────────────────────────


async def test_orchestrate_appends_product_context_when_retrieved_present() -> None:
    """When retrieved_products is non-empty, a HumanMessage context note is appended."""
    from app.agent.nodes.orchestrate import orchestrate

    products = [
        {"name": "Vintage Tee", "category": "Apparel", "price_range": "$20-$30"},
        {"name": "Mug", "category": "Drinkware", "price_range": "$10-$15"},
    ]
    state = _make_state(retrieved_products=products)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    # Find the product context note by content (last message may be a
    # later hint such as the generate_image flag).
    product_notes = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "products already retrieved" in m.content
    ]
    assert len(product_notes) == 1
    note = product_notes[0]
    assert "Vintage Tee" in note.content
    assert "Apparel" in note.content
    assert "$20-$30" in note.content
    assert "Mug" in note.content


async def test_orchestrate_appends_trend_context_when_summary_present() -> None:
    """When trend_summary is set, a HumanMessage context note is appended."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(trend_summary="Bold typography and earth tones are trending for 2026 Q2.")
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    trend_notes = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "trend research already completed" in m.content
    ]
    assert len(trend_notes) == 1
    assert "Bold typography" in trend_notes[0].content


async def test_orchestrate_no_product_or_trend_context_when_both_empty() -> None:
    """No product or trend context note is appended when both fields are empty.

    The generate_image flag may still add its own hint (default is False
    -> negative hint), but no products/trend context note is appended.
    """
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(retrieved_products=[], trend_summary=None)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("need_product_search")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    # The SystemMessage must be present
    assert any(isinstance(m, SystemMessage) for m in call_messages)
    # No products or trend context notes
    for m in call_messages:
        if isinstance(m, HumanMessage):
            assert "products already retrieved" not in m.content
            assert "trend research already completed" not in m.content
            assert "image prompt is already prepared" not in m.content


# ── Defensive fallback (Tests 12-13) ────────────────────────────────────────


async def test_orchestrate_falls_back_when_no_tool_call_emitted() -> None:
    """When the LLM returns no tool call, intent must default to 'fallback'."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message(None)  # no tool_calls

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await orchestrate(state, config)

    assert result == {"intent": "fallback"}


async def test_orchestrate_falls_back_when_intent_value_unknown() -> None:
    """When the LLM emits an intent value not in the taxonomy, intent must default to 'fallback'."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state()
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("totally_made_up_intent")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await orchestrate(state, config)

    assert result == {"intent": "fallback"}


# ── Correlation ID forwarding (Test 14) ─────────────────────────────────────


async def test_orchestrate_forwards_correlation_id_as_metadata() -> None:
    """The correlation_id must be forwarded to the LLM as LangChain metadata."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(correlation_id="corr-xyz-789")
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        _, kwargs = mock_llm.ainvoke.call_args
    assert kwargs.get("config", {}).get("metadata", {}).get("correlation_id") == "corr-xyz-789"


# ── CRITICAL invariant: never mutate state["messages"] (Test 15) ─────────────


async def test_orchestrate_does_not_mutate_state_messages() -> None:
    """The node must NOT return a 'messages' key.

    Returning a 'messages' key would invoke the add_messages reducer,
    which APPENDS rather than replaces.  Since the graph may loop back
    to orchestrate 2-4 times per turn, context notes would accumulate
    and pollute the conversation history.
    """
    from app.agent.nodes.orchestrate import orchestrate

    original_messages = [HumanMessage(content="Find me a t-shirt")]
    state = _make_state(
        messages=original_messages,
        retrieved_products=[{"name": "Vintage Tee", "category": "Apparel", "price_range": "$20"}],
        trend_summary="Bold typography trending.",
    )
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await orchestrate(state, config)

    assert "messages" not in result
    # Only 'intent' may be in the returned dict
    assert set(result.keys()) == {"intent"}


# ── Context content shape (Tests 16-17) ─────────────────────────────────────


async def test_orchestrate_product_context_includes_name_and_price() -> None:
    """Product context line must include name and price_range for follow-ups."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(
        retrieved_products=[
            {"name": "Bold Hoodie", "category": "Apparel", "price_range": "$40-$50"}
        ]
    )
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    product_notes = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "products already retrieved" in m.content
    ]
    assert len(product_notes) == 1
    assert "Bold Hoodie" in product_notes[0].content
    assert "$40-$50" in product_notes[0].content


async def test_orchestrate_trend_context_includes_full_summary_text() -> None:
    """Trend context must include the full trend_summary text, not just a summary."""
    from app.agent.nodes.orchestrate import orchestrate

    full_text = (
        "Earth tones, oversized silhouettes, and bold typographic prints are "
        "the three biggest Q2 2026 trends in POD apparel."
    )
    state = _make_state(trend_summary=full_text)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    trend_notes = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "trend research already completed" in m.content
    ]
    assert len(trend_notes) == 1
    # Full text must be in the message (not truncated)
    assert full_text in trend_notes[0].content


# ── Multi-intent prompt content (Tests 18-20) ───────────────────────────────


def test_orchestrator_prompt_includes_multi_intent_rule_section() -> None:
    """The orchestrator system prompt must include a Multi-Intent Rule section.

    Regression guard: the section is what teaches the LLM to re-read the
    original user query for image / design / trend intent before settling
    on `sufficient` after ProductRAG completes.
    """
    from pathlib import Path

    prompt_path = Path("src/app/agent/prompts/orchestrator_system.md")
    content = prompt_path.read_text(encoding="utf-8")
    assert "## Multi-Intent Rule" in content
    # The rule must explicitly mention the image / design cases
    assert "image" in content.lower()
    assert "design" in content.lower()


def test_orchestrator_prompt_need_trend_info_includes_image_creation() -> None:
    """The need_trend_info definition must include image / design creation cases."""
    from pathlib import Path

    prompt_path = Path("src/app/agent/prompts/orchestrator_system.md")
    content = prompt_path.read_text(encoding="utf-8")
    # Extract the need_trend_info bullet
    assert "need_trend_info" in content
    # The bullet must mention image / illustration / artwork
    lower = content.lower()
    assert "image" in lower or "illustration" in lower
    assert "artwork" in lower or "design idea" in lower
    # And it must mention image_prompt as the artifact TrendScout produces
    assert "image_prompt" in content


def test_orchestrator_prompt_sufficient_definition_blocks_undispatched_trend() -> None:
    """The `sufficient` definition must guard against undispatched trend / image needs."""
    from pathlib import Path

    prompt_path = Path("src/app/agent/prompts/orchestrator_system.md")
    content = prompt_path.read_text(encoding="utf-8")
    # The sufficient bullet must mention that the original query must not
    # require trend / image generation that has not been dispatched yet
    assert "sufficient" in content
    assert "trend info" in content.lower() or "trend" in content.lower()
    assert "image generation" in content.lower() or "image" in content.lower()


# ── Runtime hints: image generation flag (Tests 21-23) ──────────────────────


async def test_orchestrate_injects_image_generation_hint_when_flag_true() -> None:
    """When generate_image is True, the positive hint is appended.

    The hint must mention 'image generation is enabled for this turn' and
    must NOT contain the negative 'NOT enabled' phrasing (mutually
    exclusive branches).
    """
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(generate_image=True)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("need_trend_info")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    pos_hints = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image generation is enabled for this turn" in m.content
    ]
    neg_hints = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image generation is NOT enabled" in m.content
    ]
    assert len(pos_hints) == 1
    assert len(neg_hints) == 0
    # The positive hint must tell the LLM to consider need_trend_info
    assert "need_trend_info" in pos_hints[0].content


async def test_orchestrate_injects_negative_image_generation_hint_when_flag_false() -> None:
    """When generate_image is False or missing, the negative hint is appended.

    The hint must mention 'image generation is NOT enabled for this turn'
    and must NOT contain the positive 'enabled for this turn' phrasing
    (mutually exclusive branches).
    """
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(generate_image=False)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    neg_hints = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image generation is NOT enabled" in m.content
    ]
    pos_hints = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image generation is enabled for this turn" in m.content
    ]
    assert len(neg_hints) == 1
    assert len(pos_hints) == 0
    # The negative hint must explicitly allow trend research to continue
    assert "Trend research may still be dispatched" in neg_hints[0].content


async def test_orchestrate_runtime_hint_appears_after_product_context() -> None:
    """The generate_image hint must appear AFTER the products context note in the list."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(
        retrieved_products=[{"name": "X", "category": "Y", "price_range": "$1"}],
        generate_image=True,
    )
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    # Find positions
    product_idx = next(
        i
        for i, m in enumerate(call_messages)
        if isinstance(m, HumanMessage) and "products already retrieved" in m.content
    )
    hint_idx = next(
        i
        for i, m in enumerate(call_messages)
        if isinstance(m, HumanMessage) and "image generation is enabled for this turn" in m.content
    )
    assert product_idx < hint_idx


# ── Runtime hints: image_prompt (Tests 24-25) ───────────────────────────────


async def test_orchestrate_injects_image_prompt_hint_when_prepared() -> None:
    """When image_prompt is set, a context note with the prompt text is appended."""
    from app.agent.nodes.orchestrate import orchestrate

    prompt_text = "A vintage-style Santa Claus illustration in muted earth tones"
    state = _make_state(image_prompt=prompt_text)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("sufficient")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    prompt_notes = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image prompt is already prepared this turn" in m.content
    ]
    assert len(prompt_notes) == 1
    # The full prompt text must be in the message
    assert prompt_text in prompt_notes[0].content


async def test_orchestrate_omits_image_prompt_hint_when_unset() -> None:
    """When image_prompt is None or missing, no image-prompt context note is appended."""
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(image_prompt=None)
    config = _make_config(remaining_steps=10)
    fake_response = _make_ai_message("need_trend_info")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    for m in call_messages:
        if isinstance(m, HumanMessage):
            assert "image prompt is already prepared" not in m.content


# ── Negative hint must not block trend research (Test 26) ────────────────────


async def test_orchestrate_negative_image_hint_does_not_block_trend_research() -> None:
    """Regression guard: the negative image hint must NOT prevent need_trend_info.

    When the user asks for trend / style research without requesting
    image generation, the orchestrator must still be able to dispatch
    need_trend_info. The negative hint explicitly allows this, so we
    verify the LLM input contains the negative hint that mentions
    trend research continuation.
    """
    from app.agent.nodes.orchestrate import orchestrate

    state = _make_state(
        messages=[HumanMessage(content="What are the current trends in Christmas t-shirts?")],
        generate_image=False,
    )
    config = _make_config(remaining_steps=10)
    # Simulate the LLM choosing need_trend_info
    fake_response = _make_ai_message("need_trend_info")

    with patch("app.agent.nodes.orchestrate.ChatOpenAI") as mock_cls:
        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await orchestrate(state, config)

        call_messages = mock_llm.ainvoke.call_args[0][0]
    # The negative image hint must be present and must explicitly allow
    # trend research
    neg_hints = [
        m
        for m in call_messages
        if isinstance(m, HumanMessage) and "image generation is NOT enabled" in m.content
    ]
    assert len(neg_hints) == 1
    assert "Trend research may still be dispatched" in neg_hints[0].content
    # The orchestrator is free to return need_trend_info (the LLM
    # classifier is mocked to do so above).
    assert result == {"intent": "need_trend_info"}
