"""Unit tests for app.agent.nodes.synthesize.

Covers Phase 12, D12.1-D12.7:

* Intent-to-prompt dispatch (4 happy paths + 1 defensive fallback).
* Token streaming onto the per-request SSE queue.
* Products event emission (only when ``retrieved_products`` non-empty).
* Done event carries usage and intent.
* Returned ``AIMessage`` content equals the concatenation of streamed
  deltas — the persisted message is what shows up in thread history.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from app.agent.state import AgentState

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs: Any) -> AgentState:
    """Build a minimal AgentState dict for synthesize tests."""
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
        "intent": "sufficient",
        "title_generated": False,
        "fallback_count": 0,
        "image_url": None,
        "image_prompt": None,
    }
    base.update(kwargs)
    return cast("AgentState", base)


def _make_config(sse_queue: asyncio.Queue | None = None) -> RunnableConfig:
    """Build a RunnableConfig carrying an optional ``sse_queue``."""
    return cast("RunnableConfig", {"configurable": {"sse_queue": sse_queue}})


def _patched_llm(
    chunks: list[AIMessageChunk],
    *,
    usage_chunks: list[dict[str, int]] | None = None,
) -> MagicMock:
    """Return a mock ``ChatOpenAI`` whose ``astream`` yields the given chunks.

    ``astream`` is set up so that calling it returns an async
    generator (matching LangChain's contract — ``async for x in
    llm.astream(messages)`` iterates the result, not a coroutine).
    A thin ``MagicMock`` wraps the call so ``call_args`` is
    inspectable.
    """
    usage_chunks = usage_chunks or []

    async def _astream_gen(messages: Any, **kwargs: Any) -> Any:
        # Reference the params so ruff/pyright don't flag them; the
        # test captures them through ``astream_mock.call_args``.
        del messages, kwargs
        for i, chunk in enumerate(chunks):
            if i < len(usage_chunks):
                # ``usage_metadata`` is a frozen ``UsageMetadata`` TypedDict
                # in production; tests inject raw dicts via ``setattr`` so
                # we cast through ``Any`` to bypass the strict type.
                chunk.usage_metadata = cast(Any, usage_chunks[i])  # noqa: B010
            yield chunk

    astream_mock = MagicMock()
    astream_mock.side_effect = _astream_gen
    llm = MagicMock()
    llm.astream = astream_mock
    return llm


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


# ── Intent-to-prompt dispatch (Tests 1-5) ─────────────────────────────────────


@pytest.mark.parametrize(
    ("intent", "expected_prompt_stem"),
    [
        ("sufficient", "synthesize_sufficient_system"),
        ("clarification_needed", "synthesize_clarification_system"),
        ("out_of_scope", "synthesize_out_of_scope_system"),
        ("fallback", "synthesize_fallback_system"),
    ],
)
async def test_synthesize_dispatches_intent_to_dedicated_prompt(
    intent: str, expected_prompt_stem: str
) -> None:
    """Each known intent selects its own system-prompt file."""
    from app.agent.nodes import synthesize as synthesize_mod
    from app.agent.nodes.synthesize import synthesize

    expected_prompt_text = synthesize_mod.load_prompt(expected_prompt_stem)

    state = _make_state(intent=intent)
    config = _make_config()
    chunks = [AIMessageChunk(content="ok")]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_llm = _patched_llm(chunks)
        mock_cls.return_value = mock_llm
        await synthesize(state, config)

    # Capture the message list sent to ``llm.astream``.
    call_messages = mock_llm.astream.call_args.args[0]
    first_msg = call_messages[0]
    assert isinstance(first_msg, SystemMessage)
    # The first chunk of the system prompt must equal the loaded file.
    # ``content`` is typed ``str | list[...]``; in this code path it is
    # always a plain string, but cast for pyright.
    content = cast(str, first_msg.content)
    assert content.startswith(expected_prompt_text[:100])


async def test_synthesize_falls_back_to_fallback_prompt_for_unknown_intent() -> None:
    """An unknown intent value must defensively map to the fallback prompt."""
    from app.agent.nodes import synthesize as synthesize_mod
    from app.agent.nodes.synthesize import synthesize

    fallback_text = synthesize_mod.load_prompt("synthesize_fallback_system")

    state = _make_state(intent="totally_bogus_intent")
    config = _make_config()
    chunks = [AIMessageChunk(content="ok")]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_llm = _patched_llm(chunks)
        mock_cls.return_value = mock_llm
        await synthesize(state, config)

    call_messages = mock_llm.astream.call_args.args[0]
    assert isinstance(call_messages[0], SystemMessage)
    content = cast(str, call_messages[0].content)
    assert content.startswith(fallback_text[:100])


# ── Streaming + SSE emission (Tests 6-8) ─────────────────────────────────────


async def test_synthesize_streams_tokens_to_sse_queue() -> None:
    """Each non-empty chunk must trigger one ``token`` SSE event."""
    from app.agent.nodes.synthesize import synthesize

    state = _make_state(intent="sufficient")
    queue: asyncio.Queue = asyncio.Queue()
    config = _make_config(sse_queue=queue)

    chunks = [
        AIMessageChunk(content="hello "),
        AIMessageChunk(content="world "),
        AIMessageChunk(content="!"),
        AIMessageChunk(content=""),  # empty chunk must be dropped
    ]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_cls.return_value = _patched_llm(chunks)
        result = await synthesize(state, config)

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 3  # empty chunk dropped
    assert token_events[0]["payload"]["delta"] == "hello "
    assert token_events[1]["payload"]["delta"] == "world "
    assert token_events[2]["payload"]["delta"] == "!"
    assert all(e["payload"]["done"] is False for e in token_events)

    # The persisted AIMessage concatenates the non-empty deltas.
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "hello world !"


async def test_synthesize_emits_products_event_when_retrieved_products_present() -> None:
    """When ``retrieved_products`` is non-empty, one ``products`` event follows."""
    from app.agent.nodes.synthesize import synthesize

    products = [
        {
            "product_id": "p-1",
            "name": "Vintage Tee",
            "description": "A soft cotton tee with a retro graphic.",
            "price_range": "$20-$30",
            "saleor_url": "https://shop.example.com/p/vintage-tee",
            "thumbnail_url": "https://cdn.example.com/vintage-tee.webp",
        }
    ]
    state = _make_state(intent="sufficient", retrieved_products=products)
    queue: asyncio.Queue = asyncio.Queue()
    config = _make_config(sse_queue=queue)

    chunks = [AIMessageChunk(content="Here is a tee.")]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_cls.return_value = _patched_llm(chunks)
        await synthesize(state, config)

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    products_events = [e for e in events if e["type"] == "products"]
    assert len(products_events) == 1
    assert len(products_events[0]["payload"]["items"]) == 1
    item = products_events[0]["payload"]["items"][0]
    assert item["id"] == "p-1"
    assert item["name"] == "Vintage Tee"
    assert item["price_range"] == "$20-$30"
    assert item["saleor_url"] == "https://shop.example.com/p/vintage-tee"


async def test_synthesize_skips_products_event_when_retrieved_empty() -> None:
    """When ``retrieved_products`` is empty, no ``products`` event is emitted."""
    from app.agent.nodes.synthesize import synthesize

    state = _make_state(intent="clarification_needed", retrieved_products=[])
    queue: asyncio.Queue = asyncio.Queue()
    config = _make_config(sse_queue=queue)

    chunks = [AIMessageChunk(content="Could you tell me more?")]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_cls.return_value = _patched_llm(chunks)
        await synthesize(state, config)

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert not any(e["type"] == "products" for e in events)
    # The done event is still emitted.
    assert any(e["type"] == "done" for e in events)


async def test_synthesize_emits_done_event_with_usage_and_intent() -> None:
    """The terminal ``done`` event carries the intent and a UsagePayload."""
    from app.agent.nodes.synthesize import synthesize

    state = _make_state(intent="out_of_scope")
    queue: asyncio.Queue = asyncio.Queue()
    config = _make_config(sse_queue=queue)

    chunks = [AIMessageChunk(content="I can help with POD products only.")]
    usage_chunks = [{"prompt_tokens": 42, "completion_tokens": 7}]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_cls.return_value = _patched_llm(chunks, usage_chunks=usage_chunks)
        await synthesize(state, config)

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1
    payload = done_events[0]["payload"]
    assert payload["intent"] == "out_of_scope"
    assert payload["thread_id"] == "thread-456"
    assert payload["usage"]["prompt_tokens"] == 42
    assert payload["usage"]["completion_tokens"] == 7
    assert payload["run_id"]  # non-empty uuid string


# ── Context block assembly (Test 9) ───────────────────────────────────────────


async def test_synthesize_includes_context_sections_in_system_prompt() -> None:
    """When user_profile / trend_summary / summary are set, they appear in the prompt."""
    from app.agent.nodes.synthesize import synthesize

    state = _make_state(
        intent="sufficient",
        user_profile={"style": "minimalist", "budget_max": 200000},
        trend_summary="Bold typography is trending for Q2 2026.",
        summary="User previously asked about t-shirts.",
    )
    config = _make_config()

    chunks = [AIMessageChunk(content="ok")]

    with patch("app.agent.nodes.synthesize.ChatOpenAI") as mock_cls:
        mock_llm = _patched_llm(chunks)
        mock_cls.return_value = mock_llm
        await synthesize(state, config)

    call_messages = mock_llm.astream.call_args.args[0]
    system_text = cast(str, call_messages[0].content)
    # The dynamic context block must include the user profile, the
    # trend summary, and the conversation summary.
    assert "## Conversation Context" in system_text
    assert "minimalist" in system_text
    assert "Bold typography" in system_text
    assert "t-shirts" in system_text
    # The user profile is JSON-dumped with indentation (so the LLM
    # can read it as a block).
    assert "{\n" in system_text
    assert json.dumps({"style": "minimalist", "budget_max": 200000}, indent=2) in system_text
