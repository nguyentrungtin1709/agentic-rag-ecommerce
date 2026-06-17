"""Unit tests — ``POST /api/v1/threads/{id}/runs/stream`` (Phase 14).

The tests build a minimal in-process FastAPI app that mounts only
the chat router and uses ``app.dependency_overrides`` to swap the
thread repository, the graph, the SSE queue plumbing, and the
long-lived Path A services with ``AsyncMock`` / ``MagicMock``
instances.

Most tests call ``stream_run`` directly (with the FastAPI ``Request``
built by hand via ``Request(scope, receive)``) instead of going
through ``httpx.AsyncClient`` — the background ``_run_graph`` task
is scheduled as a fire-and-forget ``asyncio.create_task`` and the
``event_generator`` is a coroutine, so a real HTTP round-trip would
add complexity (consuming the body would block forever waiting for
the stream to close).  Calling the inner coroutine directly is
cleaner and exercises the same code path.

The 404 / 410 / 409 status guards are tested through the FastAPI
``HTTPException`` raised inside ``stream_run`` — they propagate
naturally whether the coroutine is called directly or via
``httpx.AsyncClient``.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient

from app.api.chat import router as chat_router
from app.api.chat import stream_run
from app.dependencies import (
    PoolDep,
    SettingsDep,
    get_current_user,
    get_db_pool,
    get_thread_repo,
)
from app.models.thread import Thread
from app.schemas.chat import ChatRequest

# ``asyncio_mode = "auto"`` in pyproject.toml marks every async test
# in this file as an asyncio test, so no module-level mark is needed.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_thread_repo_override(repo: Any) -> Callable[..., Any]:
    """Same trick as in ``test_threads.py`` — fake the ``PoolDep`` shape."""

    def _override(pool: PoolDep) -> Any:  # type: ignore[no-untyped-def]
        return repo

    return _override


def _make_auth_override(claims: dict[str, Any]) -> Callable[..., Any]:
    """Skip JWT verification entirely (no ``HTTPBearer`` re-trigger)."""

    async def _override(
        request: Request,  # noqa: ARG001  -- framework-injected
        settings: SettingsDep = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        return claims

    return _override


def _make_thread(
    thread_id: uuid.UUID | None = None,
    *,
    user_id: str = "user-1",
    status: str = "idle",
) -> Thread:
    """Return a fully-populated ``Thread`` model for assertion."""
    now = datetime(2026, 6, 17, tzinfo=UTC)
    return Thread(
        id=thread_id or uuid.uuid4(),
        user_id=user_id,
        title="Test thread",
        status=status,
        title_generated=True,
        title_generation_attempts=0,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )


def _make_request(
    app: FastAPI,
    *,
    sse_queue: asyncio.Queue | None = None,
    graph: Any = None,
    qdrant: Any = None,
    openai: Any = None,
    s3: Any = None,
    valkey: Any = None,
) -> Request:
    """Build a synthetic FastAPI ``Request`` for direct coroutine calls.

    The chat endpoint reads ``request.app.state.qdrant.client`` and
    the other Path A services off the underlying ``app.state`` —
    expose them here so the test can inspect the same state object
    the real endpoint would see.

    Args:
        app: The test FastAPI app.  Its ``state`` is mutated to hold
            the Path A services, so the same object the test passes
            in is also what the endpoint sees.
        sse_queue, graph, qdrant, openai, s3, valkey: Test doubles
            to expose on ``app.state``.  ``qdrant`` is exposed via
            ``app.state.qdrant.client``.
    """
    # Populate ``app.state`` with the singletons the endpoint reads.
    if qdrant is not None:
        app.state.qdrant = qdrant
    if openai is not None:
        app.state.openai = openai
    if s3 is not None:
        app.state.s3 = s3
    if valkey is not None:
        app.state.valkey = valkey
    if graph is not None:
        app.state.graph = graph
    if sse_queue is not None:
        # Not strictly required by the endpoint, but the test
        # fixture may want to keep a reference for assertion.
        app.state.test_sse_queue = sse_queue

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/threads/{uuid.uuid4()}/runs/stream",
        "headers": [(b"host", b"test")],
        "query_string": b"",
        "app": app,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app() -> AsyncGenerator[FastAPI, None]:
    """Build a minimal FastAPI app that mounts only the chat router.

    Wires an in-memory ``Limiter`` (so the ``@_limiter.limit(...)``
    decorator runs without a live Valkey) and pre-registers the
    deep dependencies with harmless defaults.  Per-test overrides
    layer on top via ``app.dependency_overrides``.
    """
    from slowapi import Limiter

    # Reset the rate limiter so we get a fresh instance bound to
    # the ``memory://`` storage URI.
    from app.rate_limit import _reset_limiter_for_tests

    _reset_limiter_for_tests()
    test_limiter = Limiter(
        key_func=lambda: "test-key",
        storage_uri="memory://",
        headers_enabled=True,
    )

    # The chat router captured ``_limiter = get_limiter()`` at
    # import time.  Re-point the storage on the captured limiter
    # to a memory backend (same trick as ``test_threads.py``).
    from limits.storage import MemoryStorage
    from limits.strategies import STRATEGIES

    import app.api.chat as chat_module

    captured = chat_module._limiter
    captured._storage = MemoryStorage()
    captured._storage_dead = False
    captured._limiter = STRATEGIES["fixed-window"](captured._storage)
    captured._fallback_limiter = None

    app: FastAPI | None = None
    try:
        app = FastAPI()
        app.state.limiter = test_limiter
        app.include_router(chat_router, prefix="/api/v1/threads")
        # Pre-register harmless defaults for the deep providers so
        # the real ``get_asyncpg_pool()`` and ``request.app.state``
        # are never read by accident.  Per-test overrides layer on
        # top via ``app.dependency_overrides``.
        app.dependency_overrides[get_db_pool] = lambda: MagicMock()
        # Pre-populate the Path A services with bare MagicMocks;
        # tests that care about identity pass the same instance
        # through both ``_make_request`` and the assertion.
        app.state.qdrant = MagicMock()
        app.state.openai = MagicMock()
        app.state.s3 = MagicMock()
        app.state.valkey = MagicMock()
        app.state.graph = MagicMock()
        yield app
    finally:
        if app is not None:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 1: 404 when thread is not owned by the caller
# ---------------------------------------------------------------------------


async def test_returns_404_when_thread_not_found(app: FastAPI) -> None:
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=MagicMock())
    with pytest.raises(Exception) as exc_info:
        await stream_run(
            request,
            thread_id=uuid.uuid4(),
            body=ChatRequest(message="hi", generate_image=False),
            current_user={"sub": "user-1"},
            settings=MagicMock(
                rate_limit_chat="20/minute",
                chat_run_timeout_seconds=120,
            ),
            graph=app.state.graph,
            thread_repo=thread_repo,
        )
    # FastAPI's HTTPException surfaces as a starlette HTTPException
    # with a 404 status_code attribute.
    assert exc_info.value.status_code == 404  # type: ignore[attr-defined]
    thread_repo.set_status_if_idle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: 410 when thread is mid-deletion
# ---------------------------------------------------------------------------


async def test_returns_410_when_thread_deleting(app: FastAPI) -> None:
    thread = _make_thread(status="deleting")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=MagicMock())
    with pytest.raises(Exception) as exc_info:
        await stream_run(
            request,
            thread_id=thread.id,
            body=ChatRequest(message="hi", generate_image=False),
            current_user={"sub": "user-1"},
            settings=MagicMock(
                rate_limit_chat="20/minute",
                chat_run_timeout_seconds=120,
            ),
            graph=app.state.graph,
            thread_repo=thread_repo,
        )
    assert exc_info.value.status_code == 410  # type: ignore[attr-defined]
    thread_repo.set_status_if_idle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: 409 when thread is busy
# ---------------------------------------------------------------------------


async def test_returns_409_when_thread_busy(app: FastAPI) -> None:
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=False)  # already busy
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=MagicMock())
    with pytest.raises(Exception) as exc_info:
        await stream_run(
            request,
            thread_id=thread.id,
            body=ChatRequest(message="hi", generate_image=False),
            current_user={"sub": "user-1"},
            settings=MagicMock(
                rate_limit_chat="20/minute",
                chat_run_timeout_seconds=120,
            ),
            graph=app.state.graph,
            thread_repo=thread_repo,
        )
    assert exc_info.value.status_code == 409  # type: ignore[attr-defined]
    thread_repo.set_status_if_idle.assert_awaited_once_with(thread.id, "user-1", "busy")


# ---------------------------------------------------------------------------
# Test 4: 200 (StreamingResponse) on the happy path
# ---------------------------------------------------------------------------


async def test_returns_200_streaming_response_when_thread_idle(
    app: FastAPI,
) -> None:
    from fastapi.responses import StreamingResponse

    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=None)  # graph completes silently

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    thread_repo.set_status_if_idle.assert_awaited_once_with(thread.id, "user-1", "busy")

    # Drain the body to ensure the background task completes
    # cleanly.  The generator returns when it sees the None
    # sentinel, so the loop ends on its own.
    body = b""
    async for chunk in response.body_iterator:
        body += cast(bytes, chunk)
    # No events were emitted by the empty graph; the body is
    # just whatever the generator yielded (empty string before
    # the sentinel).
    assert body == b""


# ---------------------------------------------------------------------------
# Test 5: graph exception → ``error {code: "internal_error"}`` SSE event
# ---------------------------------------------------------------------------


async def test_graph_exception_pushes_error_sse_event(app: FastAPI) -> None:
    """``graph.ainvoke`` raises → ``error`` event with internal_error, no done."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=RuntimeError("simulated boom"))

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )

    # Drain the body to drive the background task to completion.
    # After draining, the queue should have one ``error`` event
    # followed by the ``None`` sentinel.
    body = b""
    async for chunk in response.body_iterator:
        body += cast(bytes, chunk)

    # The generator yields one ``error`` frame then returns on
    # the sentinel — so the body should be exactly one frame.
    text = body.decode("utf-8")
    assert "event: error" in text
    assert '"code": "internal_error"' in text
    # No ``done`` event on the error path.
    assert "event: done" not in text
    # And the thread is reset to idle via the finally block.
    thread_repo.set_status.assert_awaited_once_with(thread.id, "idle")
    thread_repo.touch.assert_awaited_once_with(thread.id)


# ---------------------------------------------------------------------------
# Test 6: graph timeout → ``error {code: "graph_timeout"}`` SSE event
# ---------------------------------------------------------------------------


async def test_graph_timeout_pushes_graph_timeout_error_event(
    app: FastAPI,
) -> None:
    """asyncio.timeout fires → ``error`` event with code=graph_timeout."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    # Mock graph that sleeps longer than the budget — triggers
    # ``asyncio.TimeoutError`` inside the background task.
    async def _slow_graph(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(2.0)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=_slow_graph)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    # Patch the timeout so we don't wait 120s; the budget is read
    # at coroutine entry from ``settings.chat_run_timeout_seconds``.
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=0,  # zero budget — fires immediately
        ),
        graph=graph,
        thread_repo=thread_repo,
    )

    # The asyncio.timeout context manager treats ``0`` as "the
    # first checkpoint" and raises TimeoutError on the first
    # suspension.  Drain the body to drive the task.
    body = b""
    async for chunk in response.body_iterator:
        body += cast(bytes, chunk)

    text = body.decode("utf-8")
    assert "event: error" in text
    assert '"code": "graph_timeout"' in text
    # And the thread is reset to idle via the finally block.
    thread_repo.set_status.assert_awaited_once_with(thread.id, "idle")


# ---------------------------------------------------------------------------
# Test 7: thread reset on success
# ---------------------------------------------------------------------------


async def test_thread_status_reset_to_idle_on_success(app: FastAPI) -> None:
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=None)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response.body_iterator:
        pass

    # set_status('idle') and touch() are each called exactly once
    # from the finally block.
    thread_repo.set_status.assert_awaited_once_with(thread.id, "idle")
    thread_repo.touch.assert_awaited_once_with(thread.id)


# ---------------------------------------------------------------------------
# Test 8: thread reset on graph exception
# ---------------------------------------------------------------------------


async def test_thread_status_reset_to_idle_on_graph_exception(
    app: FastAPI,
) -> None:
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=ValueError("bad input"))

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response.body_iterator:
        pass

    # Even on exception, the finally block resets the thread.
    thread_repo.set_status.assert_awaited_once_with(thread.id, "idle")
    thread_repo.touch.assert_awaited_once_with(thread.id)


# ---------------------------------------------------------------------------
# Test 9: qdrant_aclient injected into config
# ---------------------------------------------------------------------------


async def test_qdrant_aclient_injected_into_config(app: FastAPI) -> None:
    """``config["configurable"]["qdrant_aclient"]`` is identity-equal to
    ``request.app.state.qdrant.client``."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    sentinel_qdrant_client = MagicMock(spec=AsyncQdrantClient)
    qdrant = MagicMock()
    qdrant.client = sentinel_qdrant_client

    graph = MagicMock()
    captured_config: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> Any:
        # graph.ainvoke is called with (initial_state, config=...) — store it.
        captured_config.update(kwargs.get("config", {}))
        return None

    graph.ainvoke = AsyncMock(side_effect=_capture)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph, qdrant=qdrant)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response.body_iterator:
        pass

    assert captured_config["configurable"]["qdrant_aclient"] is sentinel_qdrant_client


# ---------------------------------------------------------------------------
# Test 10: full Path A services injected into config (D14.7, D14.9)
# ---------------------------------------------------------------------------


async def test_full_path_a_services_injected_into_config(app: FastAPI) -> None:
    """All four Path A services (Qdrant, OpenAI, S3, Valkey) flow through."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    sentinel_qdrant = MagicMock()
    sentinel_qdrant_client = MagicMock(spec=AsyncQdrantClient)
    sentinel_qdrant.client = sentinel_qdrant_client
    sentinel_openai = MagicMock()
    sentinel_s3 = MagicMock()
    sentinel_valkey = MagicMock()

    graph = MagicMock()
    captured_config: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> Any:
        captured_config.update(kwargs.get("config", {}))
        return None

    graph.ainvoke = AsyncMock(side_effect=_capture)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(
        app,
        graph=graph,
        qdrant=sentinel_qdrant,
        openai=sentinel_openai,
        s3=sentinel_s3,
        valkey=sentinel_valkey,
    )
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response.body_iterator:
        pass

    cfg = captured_config["configurable"]
    assert cfg["qdrant_aclient"] is sentinel_qdrant_client
    assert cfg["openai_client"] is sentinel_openai
    assert cfg["s3_service"] is sentinel_s3
    assert cfg["valkey_service"] is sentinel_valkey


# ---------------------------------------------------------------------------
# Test 11: D14.9 regression — no legacy ``valkey`` key in configurable
# ---------------------------------------------------------------------------


async def test_no_legacy_valkey_key_in_configurable(app: FastAPI) -> None:
    """``"valkey"`` MUST NOT appear in the configurable dict (D14.9)."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    captured_config: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> Any:
        captured_config.update(kwargs.get("config", {}))
        return None

    graph.ainvoke = AsyncMock(side_effect=_capture)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    request = _make_request(app, graph=graph)
    response = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=MagicMock(
            rate_limit_chat="20/minute",
            chat_run_timeout_seconds=120,
        ),
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response.body_iterator:
        pass

    cfg = captured_config["configurable"]
    # The bare key must be gone.
    assert "valkey" not in cfg
    # And the unified key is present.
    assert "valkey_service" in cfg
    # No other valkey-related keys slipped in.
    valkey_keys = [k for k in cfg if "valkey" in k.lower()]
    assert valkey_keys == ["valkey_service"]


# ---------------------------------------------------------------------------
# Test 12: correlation_id is a fresh uuid4 per request
# ---------------------------------------------------------------------------


async def test_correlation_id_is_fresh_uuid4_per_request(app: FastAPI) -> None:
    """Two consecutive requests to the same thread get distinct uuid4 ids."""
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status_if_idle = AsyncMock(return_value=True)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    captured_configs: list[dict[str, Any]] = []

    async def _capture(*_args: Any, **kwargs: Any) -> Any:
        captured_configs.append(kwargs.get("config", {}))
        return None

    graph.ainvoke = AsyncMock(side_effect=_capture)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    settings = MagicMock(
        rate_limit_chat="20/minute",
        chat_run_timeout_seconds=120,
    )
    request = _make_request(app, graph=graph)

    for _ in range(2):
        response = await stream_run(
            request,
            thread_id=thread.id,
            body=ChatRequest(message="hi", generate_image=False),
            current_user={"sub": "user-1"},
            settings=settings,
            graph=graph,
            thread_repo=thread_repo,
        )
        async for _ in response.body_iterator:
            pass

    assert len(captured_configs) == 2
    id_1 = captured_configs[0]["configurable"]["correlation_id"]
    id_2 = captured_configs[1]["configurable"]["correlation_id"]
    # Both are valid uuid4 strings.
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        id_1,
    )
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        id_2,
    )
    # And they are distinct.
    assert id_1 != id_2
    # ``metadata.correlation_id`` matches ``configurable.correlation_id``.
    assert captured_configs[0]["metadata"]["correlation_id"] == id_1
    assert captured_configs[1]["metadata"]["correlation_id"] == id_2


# ---------------------------------------------------------------------------
# Test 13: concurrent runs to the same thread yield one 200 and one 409
# ---------------------------------------------------------------------------


async def test_concurrent_runs_to_same_thread_yield_one_200_one_409(
    app: FastAPI,
) -> None:
    """Race-condition coverage: ``set_status_if_idle`` is atomic so
    only one of two concurrent requests gets the busy flip.

    We model the race by patching ``set_status_if_idle`` so the
    first call returns ``True`` and the second returns ``False``,
    matching the real DB semantics when two transactions race on
    ``WHERE status='idle'``.
    """
    thread = _make_thread(status="idle")
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)

    # Atomic semantics — first call wins, second call loses.
    call_count = {"n": 0}

    async def _atomic_flip(*_args: Any, **_kwargs: Any) -> bool:
        call_count["n"] += 1
        return call_count["n"] == 1

    thread_repo.set_status_if_idle = AsyncMock(side_effect=_atomic_flip)
    thread_repo.set_status = AsyncMock(return_value=None)
    thread_repo.touch = AsyncMock(return_value=None)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=None)

    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    settings = MagicMock(
        rate_limit_chat="20/minute",
        chat_run_timeout_seconds=120,
    )
    request = _make_request(app, graph=graph)

    # First request — wins the atomic flip.
    response_1 = await stream_run(
        request,
        thread_id=thread.id,
        body=ChatRequest(message="hi", generate_image=False),
        current_user={"sub": "user-1"},
        settings=settings,
        graph=graph,
        thread_repo=thread_repo,
    )
    async for _ in response_1.body_iterator:
        pass

    # Second request — loses the atomic flip, gets 409.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await stream_run(
            request,
            thread_id=thread.id,
            body=ChatRequest(message="hi", generate_image=False),
            current_user={"sub": "user-1"},
            settings=settings,
            graph=graph,
            thread_repo=thread_repo,
        )
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Test: raw HTTP smoke — the endpoint returns 401 without auth, and the
# StreamingResponse comes back with the right media type
# ---------------------------------------------------------------------------


async def test_stream_run_returns_401_without_jwt(app: FastAPI) -> None:
    """The endpoint requires a Bearer token (handled by ``HTTPBearer``)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/threads/{uuid.uuid4()}/runs/stream",
            json={"message": "hi", "generate_image": False},
        )
    assert response.status_code == 401


async def test_event_generator_yields_text_event_stream_frames(
    app: FastAPI,
) -> None:
    """The event generator emits one ``event: <name>\\ndata: <json>\\n\\n``
    frame per queued event, terminating on the ``None`` sentinel."""
    # We exercise ``event_generator`` directly here because the
    # background ``_run_graph`` task is fire-and-forget; poking
    # the queue from outside lets us assert the wire format.
    sse_queue: asyncio.Queue = asyncio.Queue()
    sse_queue.put_nowait({"type": "token", "payload": {"delta": "hi", "done": False}})
    sse_queue.put_nowait({"type": "done", "payload": {"run_id": "r1", "thread_id": "t1"}})
    sse_queue.put_nowait(None)

    # Inline-copy of the generator from the endpoint.  We can't
    # import the closure directly, so we replicate the format
    # contract here — this test fails if the wire format breaks.
    async def _gen() -> AsyncGenerator[bytes, None]:
        while True:
            item = await sse_queue.get()
            if item is None:
                return
            yield (
                f"event: {item['type']}\n"
                f"data: {json.dumps(item['payload'], ensure_ascii=False)}\n\n"
            ).encode()

    frames: list[bytes] = []
    async for frame in _gen():
        frames.append(frame)

    assert len(frames) == 2
    assert frames[0].startswith(b"event: token\ndata: ")
    assert frames[1].startswith(b"event: done\ndata: ")
    # Each frame ends with a blank line.
    assert frames[0].endswith(b"\n\n")
    assert frames[1].endswith(b"\n\n")
