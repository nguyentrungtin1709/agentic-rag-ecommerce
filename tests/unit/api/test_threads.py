"""Unit tests — ``GET /api/v1/threads`` endpoint family.

The tests build a minimal in-process FastAPI app that mounts only
the threads router and uses ``app.dependency_overrides`` to swap
the repositories, the graph state, and the Valkey service with
``AsyncMock`` instances.  This mirrors the
``tests/integration/test_webhook_dispatch.py`` pattern and keeps
the tests free of the full Docker stack.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.api.threads import (
    _format_message,
    _invalidate_thread_list_cache,
    _is_ai,
    _is_human,
)
from app.api.threads import (
    router as threads_router,
)
from app.dependencies import (
    PoolDep,
    SettingsDep,
    get_current_user,
    get_db_pool,
    get_graph,
    get_image_repo,
    get_thread_repo,
    get_valkey_service,
)
from app.models.image import GeneratedImage
from app.models.thread import Thread

# ``asyncio_mode = "auto"`` in pyproject.toml marks every async test
# in this file as an asyncio test, so no module-level mark is needed.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_thread_repo_override(repo: Any) -> Callable[..., Any]:
    """Return a callable that mimics ``get_thread_repo``'s signature.

    The returned function is given the ``PoolDep`` annotation on
    its ``pool`` parameter so FastAPI routes the sub-dependency
    through the dependency tree (rather than treating ``pool`` as
    a required query parameter).  The function ignores the pool
    value and returns the pre-built ``repo`` mock.
    """

    def _override(pool: PoolDep) -> Any:  # type: ignore[no-untyped-def]
        return repo

    return _override


def _make_image_repo_override(repo: Any) -> Callable[..., Any]:
    """Same idea as ``_make_thread_repo_override`` for the image repo."""

    def _override(pool: PoolDep) -> Any:  # type: ignore[no-untyped-def]
        return repo

    return _override


def _make_request_override(value: Any) -> Callable[..., Any]:
    """Return a callable that mimics the ``get_*_service`` ``(request)``
    signature so FastAPI injects the live request rather than
    treating it as a query parameter.
    """

    def _override(request: Request) -> Any:  # type: ignore[no-untyped-def]
        return value

    return _override


def _make_auth_override(claims: dict[str, Any]) -> Callable[..., Any]:
    """Return a callable that mimics ``get_current_user`` but
    skips JWT verification entirely.

    FastAPI builds a ``Dependant`` from the override's signature
    and resolves each parameter (whether it has a ``Depends`` or
    not) before calling the override.  The original
    ``get_current_user`` declares ``credentials: Annotated[...,
    Depends(_bearer_scheme)]``, and the bearer scheme raises
    ``HTTPException(401)`` when no ``Authorization`` header is
    present — so any override that re-declares that parameter
    would re-trigger the same auth failure.

    The fix is to keep only the parameters we actually need
    (``Request`` for the framework-injected request and
    ``SettingsDep`` for the same annotation as the original —
    the latter lets the override be reached for callers that
    still want settings in scope).  No JWT, no bearer scheme.
    """

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
    title: str | None = "Test thread",
) -> Thread:
    """Return a fully-populated ``Thread`` model for assertion."""
    now = datetime(2026, 6, 14, tzinfo=UTC)
    return Thread(
        id=thread_id or uuid.uuid4(),
        user_id=user_id,
        title=title,
        status=status,
        title_generated=True,
        title_generation_attempts=0,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )


def _make_image(
    request_message_id: str,
    url: str = "https://cdn.example.com/img.png",
    prompt: str = "a test design",
) -> GeneratedImage:
    """Return a ``GeneratedImage`` model for tests."""
    return GeneratedImage(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        user_id="user-1",
        prompt=prompt,
        s3_key=f"images/{request_message_id}.png",
        s3_url=url,
        model="dall-e-3",
        request_message_id=request_message_id,
        created_at=datetime(2026, 6, 14, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# _is_human / _is_ai / _format_message
# ---------------------------------------------------------------------------


def test_is_human_returns_true_for_human_message() -> None:
    msg = HumanMessage(content="hi", id="h1")
    assert _is_human(msg) is True
    assert _is_ai(msg) is False


def test_is_ai_returns_true_for_ai_message() -> None:
    msg = AIMessage(content="hello", id="a1")
    assert _is_ai(msg) is True
    assert _is_human(msg) is False


def test_is_human_returns_false_for_system_or_tool() -> None:
    assert _is_human(SystemMessage(content="sys", id="s1")) is False
    assert _is_ai(SystemMessage(content="sys", id="s1")) is False
    assert _is_human(ToolMessage(content="t", id="t1", tool_call_id="c1")) is False


def test_format_message_returns_none_for_system_message() -> None:
    sys = SystemMessage(content="system prompt", id="s1")
    assert _format_message(sys, {}) is None


def test_format_message_returns_none_for_tool_message() -> None:
    tool = ToolMessage(content="tool output", id="t1", tool_call_id="call-1")
    assert _format_message(tool, {}) is None


def test_format_message_human_serialises_content_and_empty_images() -> None:
    msg = HumanMessage(content="I want a t-shirt", id="h1")
    result = _format_message(msg, {})
    assert result is not None
    assert result.id == "h1"
    assert result.type == "human"
    assert result.content == "I want a t-shirt"
    assert result.images == []


def test_format_message_ai_looks_up_images_by_id() -> None:
    """AIMessage picks up its images from the batch lookup dict."""
    img = _make_image("a1", url="https://cdn/x.png", prompt="a dragon")
    msg = AIMessage(content="here you go", id="a1")
    result = _format_message(msg, {"a1": [img]})
    assert result is not None
    assert result.type == "ai"
    assert len(result.images) == 1
    assert result.images[0].url == "https://cdn/x.png"
    assert result.images[0].prompt == "a dragon"


def test_format_message_ai_with_no_images_returns_empty_list() -> None:
    msg = AIMessage(content="just text", id="a2")
    result = _format_message(msg, {})
    assert result is not None
    assert result.images == []


def test_format_message_stringifies_non_string_content() -> None:
    """LangChain stores structured content as list-of-blocks; we coerce to str."""
    msg = HumanMessage(content=[{"type": "text", "text": "block"}], id="h2")
    result = _format_message(msg, {})
    assert result is not None
    assert "block" in result.content


# ---------------------------------------------------------------------------
# _invalidate_thread_list_cache
# ---------------------------------------------------------------------------


async def test_invalidate_thread_list_cache_calls_delete_pattern() -> None:
    """The helper must call ``delete_pattern`` with the right glob."""
    valkey = MagicMock()
    valkey.delete_pattern = AsyncMock(return_value=3)
    deleted = await _invalidate_thread_list_cache(valkey, "user-123")
    assert deleted == 3
    valkey.delete_pattern.assert_awaited_once_with("threads-list:threads:user-123:*")


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[FastAPI, None]:
    """Build a minimal FastAPI app that mounts only the threads router.

    The app wires an in-memory ``Limiter`` (so the
    ``@_limiter.limit(...)`` decorators run without a live Valkey)
    and initialises ``FastAPICache`` against an
    ``InMemoryBackend`` so the ``@cache(...)`` decorator on the
    list endpoint works without Redis.

    The fixture pre-registers harmless defaults for the deep
    dependencies (``get_db_pool``, ``get_valkey_service``,
    ``get_graph``) so the real ``get_asyncpg_pool()`` and
    ``request.app.state.*`` are never read.  Each test then swaps
    the per-endpoint providers (``get_thread_repo``,
    ``get_image_repo``, ``get_graph``, ``get_current_user``) with
    ``AsyncMock``/``MagicMock`` instances via
    ``app.dependency_overrides``.
    """
    from slowapi import Limiter

    # Reset the rate limiter so we get a fresh instance bound to
    # the ``memory://`` storage URI — the production singleton
    # points at Valkey, which the unit-test app cannot reach.
    from app.rate_limit import _reset_limiter_for_tests

    _reset_limiter_for_tests()
    test_limiter = Limiter(
        key_func=lambda: "test-key",
        storage_uri="memory://",
        headers_enabled=True,
    )
    # The threads router captured ``_limiter = get_limiter()`` at
    # import time AND each ``@_limiter.limit(...)`` wrapper binds
    # the Limiter instance to its closure.  Re-pointing the module
    # attribute alone leaves the wrappers pointing at the Valkey
    # singleton — so we additionally swap the storage on the
    # original limiter to a memory backend.  That keeps the
    # decorator closures (which already reference the production
    # instance) operational without ever touching Valkey.
    from limits.storage import MemoryStorage
    from limits.strategies import STRATEGIES

    production_limiter = Limiter.__module__  # noqa: F841  (for debugging)
    import app.api.threads as threads_module

    captured = threads_module._limiter
    captured._storage = MemoryStorage()
    captured._storage_dead = False
    captured._limiter = STRATEGIES["fixed-window"](captured._storage)
    captured._fallback_limiter = None

    # Init the cache against an in-memory backend so the @cache
    # decorator is functional in-process.
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    app: FastAPI | None = None
    try:
        app = FastAPI()
        app.state.limiter = test_limiter
        # The threads router registers its paths with relative
        # segments (``""``, ``"/{thread_id}"`` …) so the
        # production-style prefix is ``/api/v1/threads``.
        app.include_router(threads_router, prefix="/api/v1/threads")
        # Pre-register the deep providers with harmless defaults so
        # the real ``get_asyncpg_pool()`` and ``request.app.state``
        # are never touched.  Per-test overrides (see below) layer
        # on top of these.
        app.dependency_overrides[get_db_pool] = lambda: MagicMock()
        # ``get_valkey_service`` reads ``request.app.state.valkey``;
        # set a sentinel so the real service is never instantiated.
        app.state.valkey = MagicMock()
        # ``get_graph`` reads ``request.app.state.graph``; ditto.
        app.state.graph = MagicMock()
        yield app
    finally:
        await FastAPICache.clear()
        if app is not None:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth — every endpoint requires a valid JWT
# ---------------------------------------------------------------------------


async def test_create_thread_without_jwt_returns_401(app: FastAPI) -> None:
    """No ``Authorization`` header → ``verify_token`` rejects with 401.

    The dependency chain (``HTTPBearer`` → ``verify_token``) bubbles
    the missing-credential failure up to ``HTTPException(401)``.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/threads", json={})
    assert response.status_code == 401


async def test_list_threads_without_jwt_returns_401(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/threads")
    assert response.status_code == 401


async def test_get_thread_without_jwt_returns_401(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/threads/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_delete_thread_without_jwt_returns_401(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/threads/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_history_without_jwt_returns_401(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/threads/{uuid.uuid4()}/history")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /threads
# ---------------------------------------------------------------------------


async def test_create_thread_returns_201_and_invalidates_cache(
    app: FastAPI,
) -> None:
    new_thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.create = AsyncMock(return_value=new_thread)
    valkey = MagicMock()
    valkey.delete_pattern = AsyncMock(return_value=1)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_valkey_service] = _make_request_override(valkey)
    # current_user dep — return a fake claims dict
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/threads", json={})
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == str(new_thread.id)
        assert body["status"] == "idle"
        thread_repo.create.assert_awaited_once_with("user-1")
        valkey.delete_pattern.assert_awaited_once_with("threads-list:threads:user-1:*")
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /threads/{id}
# ---------------------------------------------------------------------------


async def test_get_thread_returns_200_for_owned(app: FastAPI) -> None:
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(thread.id)
        assert body["title"] == thread.title
        thread_repo.get.assert_awaited_once_with(thread.id, "user-1")
    finally:
        app.dependency_overrides.clear()


async def test_get_thread_returns_404_for_not_found(app: FastAPI) -> None:
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "not found" in response.text.lower()
    finally:
        app.dependency_overrides.clear()


async def test_get_thread_returns_404_for_other_user(app: FastAPI) -> None:
    """``ThreadRepository.get`` returns ``None`` for not-owned threads,
    so the 404 path is the same as the not-found path (D8.4)."""
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{uuid.uuid4()}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_get_thread_returns_410_for_deleting(app: FastAPI) -> None:
    thread = _make_thread(status="deleting", title=None)
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}")
        assert response.status_code == 410
        assert "deleted" in response.text.lower()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DELETE /threads/{id}
# ---------------------------------------------------------------------------


async def test_delete_thread_returns_202_and_dispatches_celery(
    app: FastAPI,
) -> None:
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status = AsyncMock(return_value=None)
    valkey = MagicMock()
    valkey.delete_pattern = AsyncMock(return_value=2)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_valkey_service] = _make_request_override(valkey)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})

    with patch("app.api.threads.delete_thread_task") as fake_task:
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.delete(f"/api/v1/threads/{thread.id}")
            assert response.status_code == 202
            body = response.json()
            assert body["thread_id"] == str(thread.id)
            assert body["status"] == "deleting"
            thread_repo.set_status.assert_awaited_once_with(thread.id, "deleting")
            fake_task.delay.assert_called_once_with(str(thread.id), "user-1")  # type: ignore[attr-defined]
            valkey.delete_pattern.assert_awaited_once_with("threads-list:threads:user-1:*")
        finally:
            app.dependency_overrides.clear()


async def test_delete_thread_returns_404_for_not_found(app: FastAPI) -> None:
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/threads/{uuid.uuid4()}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_delete_thread_returns_410_for_already_deleting(
    app: FastAPI,
) -> None:
    thread = _make_thread(status="deleting", title=None)
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    thread_repo.set_status = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    with patch("app.api.threads.delete_thread_task") as fake_task:
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.delete(f"/api/v1/threads/{thread.id}")
            assert response.status_code == 410
            thread_repo.set_status.assert_not_called()
            fake_task.delay.assert_not_called()  # type: ignore[attr-defined]
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /threads/{id}/history  — Option C (D8.8) and batch image lookup
# ---------------------------------------------------------------------------


def _state_snapshot(messages: list[Any]) -> MagicMock:
    """Build a fake ``StateSnapshot`` carrying the given messages list."""
    snap = MagicMock()
    snap.values = {"messages": messages}
    snap.next = ()
    snap.config = {"configurable": {"thread_id": "x"}}
    return snap


def _human(msg_id: str, content: str = "hi") -> HumanMessage:
    return HumanMessage(content=content, id=msg_id)


def _ai(msg_id: str, content: str = "ok") -> AIMessage:
    return AIMessage(content=content, id=msg_id)


async def test_history_returns_empty_when_graph_has_no_state(
    app: FastAPI,
) -> None:
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}/history")
        assert response.status_code == 200
        assert response.json() == {"messages": [], "next_cursor": None}
    finally:
        app.dependency_overrides.clear()


async def test_history_filters_system_and_tool_messages(
    app: FastAPI,
) -> None:
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=_state_snapshot(
            [
                SystemMessage(content="system prompt", id="s1"),
                _human("h1"),
                _ai("a1"),
                ToolMessage(content="tool", id="t1", tool_call_id="c1"),
                _human("h2"),
                _ai("a2"),
            ]
        )
    )
    image_repo = MagicMock()
    image_repo.list_by_message_ids = AsyncMock(return_value={"h1": [], "h2": []})
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_image_repo] = _make_image_repo_override(image_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}/history")
        assert response.status_code == 200
        body = response.json()
        assert [m["type"] for m in body["messages"]] == ["human", "ai", "human", "ai"]
        assert [m["id"] for m in body["messages"]] == ["h1", "a1", "h2", "a2"]
        # Only the two human ids should have been batched.
        image_repo.list_by_message_ids.assert_awaited_once()
        called_ids = image_repo.list_by_message_ids.await_args.args[0]
        assert sorted(called_ids) == ["h1", "h2"]
    finally:
        app.dependency_overrides.clear()


async def test_history_uses_batch_image_lookup(app: FastAPI) -> None:
    """Images are looked up in a single batch query, not N+1."""
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=_state_snapshot(
            [
                _human("h1"),
                _ai("a1"),
                _human("h2"),
                _ai("a2"),
            ]
        )
    )
    img1 = _make_image("a1", url="https://x/1.png", prompt="one")
    img2a = _make_image("a2", url="https://x/2a.png", prompt="two-a")
    img2b = _make_image("a2", url="https://x/2b.png", prompt="two-b")
    image_repo = MagicMock()
    image_repo.list_by_message_ids = AsyncMock(
        return_value={"h1": [], "h2": [], "a1": [img1], "a2": [img2a, img2b]}
    )
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_image_repo] = _make_image_repo_override(image_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}/history")
        assert response.status_code == 200
        body = response.json()
        # The first AI message has one image, the second has two.
        assert body["messages"][1]["images"] == [{"url": "https://x/1.png", "prompt": "one"}]
        assert body["messages"][3]["images"] == [
            {"url": "https://x/2a.png", "prompt": "two-a"},
            {"url": "https://x/2b.png", "prompt": "two-b"},
        ]
        # Critical: the batch query was called exactly once (not 2+).
        image_repo.list_by_message_ids.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


async def test_history_cursor_not_found_returns_empty(app: FastAPI) -> None:
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_state_snapshot([_human("h1"), _ai("a1")]))
    image_repo = MagicMock()
    image_repo.list_by_message_ids = AsyncMock(return_value={})
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_image_repo] = _make_image_repo_override(image_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/threads/{thread.id}/history",
                params={"before": "not-a-real-id"},
            )
        assert response.status_code == 200
        assert response.json() == {"messages": [], "next_cursor": None}
    finally:
        app.dependency_overrides.clear()


async def test_history_first_page_returns_newest_with_next_cursor(
    app: FastAPI,
) -> None:
    """``before=null`` returns the most recent ``limit`` messages and
    a non-null ``next_cursor`` when older messages still exist."""
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    msgs = []
    for i in range(1, 4):
        msgs.append(_human(f"h{i}"))
        msgs.append(_ai(f"a{i}"))
    graph.aget_state = AsyncMock(return_value=_state_snapshot(msgs))
    image_repo = MagicMock()
    image_repo.list_by_message_ids = AsyncMock(return_value={})
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_image_repo] = _make_image_repo_override(image_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/threads/{thread.id}/history",
                params={"limit": 2},
            )
        assert response.status_code == 200
        body = response.json()
        # Newest page of size 2 = [h3, a3] (assuming state is oldest-first).
        assert [m["id"] for m in body["messages"]] == ["h3", "a3"]
        # Older messages exist (h1, a1, h2, a2), so next_cursor is set.
        assert body["next_cursor"] == "a3"
    finally:
        app.dependency_overrides.clear()


async def test_history_cursor_on_ai_rounds_back_to_human_message(
    app: FastAPI,
) -> None:
    """Option C (D8.8): cursor on AIMessage rounds the page start
    back to the most recent HumanMessage.  The response is
    GUARANTEED to start with a ``human`` message even when the
    raw slice would start on an ``ai`` message."""
    thread = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=_state_snapshot(
            [_human("h1"), _ai("a1"), _human("h2"), _ai("a2"), _human("h3"), _ai("a3")]
        )
    )
    image_repo = MagicMock()
    image_repo.list_by_message_ids = AsyncMock(return_value={})
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_image_repo] = _make_image_repo_override(image_repo)
    app.dependency_overrides[get_graph] = _make_request_override(graph)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/threads/{thread.id}/history",
                params={"before": "a3", "limit": 2},
            )
        assert response.status_code == 200
        body = response.json()
        # Page must start on a HumanMessage — the Option C rounding
        # extends the raw ``[h2, a2]`` slice or the ``[a1, h2]``
        # slice back to ``[h2, a2]`` depending on how the slice
        # math lands.  Either way, the first message is human.
        assert body["messages"][0]["type"] == "human"
        assert body["messages"][0]["id"] in {"h2", "h3"}
    finally:
        app.dependency_overrides.clear()


async def test_history_returns_404_for_not_found(app: FastAPI) -> None:
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{uuid.uuid4()}/history")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_history_returns_410_for_deleting(app: FastAPI) -> None:
    thread = _make_thread(status="deleting", title=None)
    thread_repo = AsyncMock()
    thread_repo.get = AsyncMock(return_value=thread)
    app.dependency_overrides[get_thread_repo] = _make_thread_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override({"sub": "user-1"})
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/threads/{thread.id}/history")
        assert response.status_code == 410
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ``app.dependency_overrides`` looks up the *same callable object*
# that FastAPI registered with ``Depends(...)``.  We import the
# real providers from ``app.dependencies`` at the top of the file
# and use them as the keys here.
# ---------------------------------------------------------------------------
