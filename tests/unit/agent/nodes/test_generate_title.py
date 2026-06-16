"""Unit tests for app.agent.nodes.generate_title.

Covers Phase 12, D12.8-D12.11:

* No-op when the title is already generated.
* No-op when ``first_user_message`` is missing.
* LLM-success path: persists via ``ThreadRepository.update_title``,
  invalidates the Valkey cache, emits a ``thread_title`` SSE event.
* Sanitisation strips surrounding quotes.
* Truncation fallback after ``title_generation_max_attempts``.
* LLM timeout / API error returns ``{}`` and does NOT call
  ``update_title``.
* Valkey cache invalidation failure is swallowed (best-effort).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.agent.state import AgentState

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

# ── Helpers ───────────────────────────────────────────────────────────────────


_THREAD_UUID = uuid.uuid4()
_THREAD_UUID_STR = str(_THREAD_UUID)
_USER_ID = "user-123"
_CORR_ID = "test-corr-id"


def _make_state(**kwargs: Any) -> AgentState:
    """Build a minimal AgentState dict for generate_title tests."""
    base: dict = {
        "messages": [],
        "correlation_id": _CORR_ID,
        "user_id": _USER_ID,
        "thread_id": _THREAD_UUID_STR,
        "summary": "",
        "generate_image": False,
        "first_user_message": "I need a gift for my sister's birthday, she loves minimalism",
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


def _make_config(
    sse_queue: asyncio.Queue | None = None,
    valkey: Any = None,
) -> RunnableConfig:
    """Build a RunnableConfig carrying ``sse_queue`` and ``valkey``."""
    return cast(
        "RunnableConfig",
        {"configurable": {"sse_queue": sse_queue, "valkey": valkey}},
    )


def _patch_thread_repo(*, attempts_return: int = 1) -> MagicMock:
    """Return a MagicMock ThreadRepository with the right async methods."""
    repo = MagicMock()
    repo.increment_title_attempts = AsyncMock(return_value=attempts_return)
    repo.update_title = AsyncMock(return_value=None)
    return repo


def _patch_get_asyncpg_pool(repo: MagicMock) -> Any:
    """Return a context-manager shim so ``get_asyncpg_pool()`` returns ``repo``."""
    return repo  # The pool is only fed to ``ThreadRepository(pool)``.


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


# ── Test 1: no-op when title already generated ────────────────────────────────


async def test_generate_title_is_noop_when_title_already_generated() -> None:
    """``title_generated=True`` short-circuits to ``{}`` with no I/O."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state(title_generated=True)
    config = _make_config()

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_llm,
        patch("app.agent.nodes.generate_title.get_asyncpg_pool") as mock_pool,
    ):
        result = await generate_title(state, config)

    assert result == {}
    mock_llm.assert_not_called()
    mock_pool.assert_not_called()


# ── Test 2: no-op when first_user_message is missing ─────────────────────────


async def test_generate_title_is_noop_when_first_user_message_missing() -> None:
    """An agent-driven run without a human turn yields ``{}``."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state(first_user_message=None)
    config = _make_config()

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_llm,
        patch("app.agent.nodes.generate_title.get_asyncpg_pool") as mock_pool,
    ):
        result = await generate_title(state, config)

    assert result == {}
    mock_llm.assert_not_called()
    mock_pool.assert_not_called()


# ── Test 3: LLM success path — persist, invalidate, emit ─────────────────────


async def test_generate_title_llm_success_persists_and_emits_sse() -> None:
    """LLM returns a clean title; the node persists, invalidates, and emits."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()
    valkey = MagicMock()
    valkey.delete_pattern = AsyncMock(return_value=3)
    config = _make_config(sse_queue=queue, valkey=valkey)

    fake_response = AIMessage(content="Birthday Gift for Sister")

    thread_repo = _patch_thread_repo(attempts_return=1)

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_cls,
        patch(
            "app.agent.nodes.generate_title.ThreadRepository",
            return_value=thread_repo,
        ) as mock_repo_cls,
        patch("app.agent.nodes.generate_title.get_asyncpg_pool", return_value=MagicMock()),
    ):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await generate_title(state, config)

    # Title is persisted as the cleaned string.
    thread_repo.update_title.assert_awaited_once()
    persisted_args = thread_repo.update_title.await_args.args
    assert persisted_args[0] == _THREAD_UUID
    assert persisted_args[1] == "Birthday Gift for Sister"
    # ThreadRepository was constructed with the pool.
    mock_repo_cls.assert_called_once()
    # Cache invalidation is called with the right key pattern.
    valkey.delete_pattern.assert_awaited_once_with(f"threads:{_USER_ID}:*")
    # One ``thread_title`` event on the queue.
    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 1
    assert events[0]["type"] == "thread_title"
    assert events[0]["payload"]["title"] == "Birthday Gift for Sister"
    # State update carries the title and the flag.
    assert result == {"thread_title": "Birthday Gift for Sister", "title_generated": True}


# ── Test 4: surrounding quotes are stripped ───────────────────────────────────


async def test_generate_title_strips_surrounding_quotes() -> None:
    """LLM returns ``"Birthday Gift"`` → stored as ``Birthday Gift``."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state()
    config = _make_config()

    fake_response = AIMessage(content='"Birthday Gift for Sister"')
    thread_repo = _patch_thread_repo(attempts_return=1)

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_cls,
        patch(
            "app.agent.nodes.generate_title.ThreadRepository",
            return_value=thread_repo,
        ),
        patch("app.agent.nodes.generate_title.get_asyncpg_pool", return_value=MagicMock()),
    ):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        await generate_title(state, config)

    persisted = thread_repo.update_title.await_args.args[1]
    assert persisted == "Birthday Gift for Sister"


# ── Test 5: truncation fallback after max attempts ──────────────────────────


async def test_generate_title_falls_back_to_truncation_after_max_attempts() -> None:
    """``increment_title_attempts`` returns > max → no LLM call, truncation."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state()
    config = _make_config()

    thread_repo = _patch_thread_repo(attempts_return=4)  # max default is 3

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_cls,
        patch(
            "app.agent.nodes.generate_title.ThreadRepository",
            return_value=thread_repo,
        ),
        patch("app.agent.nodes.generate_title.get_asyncpg_pool", return_value=MagicMock()),
    ):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock()
        mock_cls.return_value = mock_llm

        result = await generate_title(state, config)

    # No LLM call when attempts are exhausted.
    mock_llm.ainvoke.assert_not_called()
    # ``update_title`` called with the truncated first user message
    # (default 50 chars).
    persisted = thread_repo.update_title.await_args.args[1]
    expected = state["first_user_message"]
    assert expected is not None
    assert persisted == expected[:50].strip()
    # Returned state carries the truncation title.
    assert result["thread_title"] == expected[:50].strip()
    assert result["title_generated"] is True


# ── Test 6: LLM timeout returns {} without persisting ───────────────────────


async def test_generate_title_returns_empty_on_llm_timeout() -> None:
    """``asyncio.TimeoutError`` short-circuits to ``{}`` — no DB write."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state()
    config = _make_config()

    thread_repo = _patch_thread_repo(attempts_return=1)

    async def _raise_timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("simulated LLM timeout")

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_cls,
        patch(
            "app.agent.nodes.generate_title.ThreadRepository",
            return_value=thread_repo,
        ),
        patch("app.agent.nodes.generate_title.get_asyncpg_pool", return_value=MagicMock()),
    ):
        mock_llm = MagicMock()
        mock_llm.ainvoke = _raise_timeout
        mock_cls.return_value = mock_llm

        result = await generate_title(state, config)

    # No persistence happens on LLM failure.
    thread_repo.update_title.assert_not_called()
    # And the node returns an empty update.
    assert result == {}


# ── Test 7: Valkey cache invalidation failure is swallowed ──────────────────


async def test_generate_title_swallows_valkey_failure() -> None:
    """A failing ``delete_pattern`` does NOT fail the run; the title persists."""
    from app.agent.nodes.generate_title import generate_title

    state = _make_state()
    config = _make_config()

    valkey = MagicMock()
    valkey.delete_pattern = AsyncMock(side_effect=RuntimeError("simulated redis down"))
    thread_repo = _patch_thread_repo(attempts_return=1)

    fake_response = AIMessage(content="Birthday Gift for Sister")

    with (
        patch("app.agent.nodes.generate_title.ChatOpenAI") as mock_cls,
        patch(
            "app.agent.nodes.generate_title.ThreadRepository",
            return_value=thread_repo,
        ),
        patch("app.agent.nodes.generate_title.get_asyncpg_pool", return_value=MagicMock()),
    ):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_llm

        result = await generate_title(state, config)

    # Persistence still happened.
    thread_repo.update_title.assert_awaited_once()
    # And the node returned the title — the cache failure is best-effort.
    assert result["thread_title"] == "Birthday Gift for Sister"
    assert result["title_generated"] is True
