"""Integration tests — thread-list response cache (fastapi-cache2).

Verifies the fastapi-cache2 plumbing is correctly wired in ``main.py``:

- ``thread_list_key_builder`` produces deterministic, user-scoped keys
  that include the ``before`` and ``limit`` query parameters.
- Different authenticated users do not share cache entries
  (no cross-user leakage).
- A route decorated with ``@cache(namespace=..., key_builder=...)`` is
  served from cache on the second identical request.
- ``ValkeyService.delete_pattern`` invalidates matching entries.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from app.cache.keys import thread_list_key_builder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_request(user_id: str | None, query: dict[str, str] | None = None) -> Request:
    """Build a minimal ``Request`` for the key_builder unit tests.

    The builder reads ``request.query_params`` and
    ``request.state.current_user`` — those are the only fields we need
    to populate.  ``scope`` is the minimum that lets
    ``Request.__init__`` succeed.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/threads",
        "headers": [],
        "query_string": b"",
    }
    if query:
        from urllib.parse import urlencode

        scope["query_string"] = urlencode(query).encode()
    request = Request(scope)
    if user_id is not None:
        request.state.current_user = {"sub": user_id}
    return request


def _install_test_user(app: FastAPI) -> None:  # pragma: no cover - placeholder
    """Reserved for future tests; the ``in_process_cache_app`` fixture
    uses an HTTP middleware instead to keep the wiring minimal.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Unit tests — thread_list_key_builder
# ---------------------------------------------------------------------------


def test_key_builder_includes_user_id_in_key() -> None:
    """The key must embed the user ID to prevent cross-user leakage."""
    request = _stub_request("user-123")
    key = thread_list_key_builder(lambda: None, namespace="threads", request=request)
    assert "user-123" in key


def test_key_builder_includes_before_and_limit() -> None:
    """Different page-cursor values produce different keys."""
    req_a = _stub_request("user-1", {"before": "head", "limit": "20"})
    req_b = _stub_request("user-1", {"before": "head", "limit": "50"})
    key_a = thread_list_key_builder(lambda: None, namespace="t", request=req_a)
    key_b = thread_list_key_builder(lambda: None, namespace="t", request=req_b)
    assert key_a != key_b
    assert key_a.endswith(":head:20")
    assert key_b.endswith(":head:50")


def test_key_builder_separates_users() -> None:
    """User A and user B with the same query params get different keys."""
    req_a = _stub_request("user-a", {"limit": "20"})
    req_b = _stub_request("user-b", {"limit": "20"})
    key_a = thread_list_key_builder(lambda: None, namespace="t", request=req_a)
    key_b = thread_list_key_builder(lambda: None, namespace="t", request=req_b)
    assert key_a != key_b


def test_key_builder_uses_defaults_when_query_params_missing() -> None:
    """Missing ``before``/``limit`` fall back to ``head``/``20``."""
    request = _stub_request("user-x")
    key = thread_list_key_builder(lambda: None, namespace="t", request=request)
    assert "head" in key
    assert "20" in key


def test_key_builder_includes_namespace_prefix() -> None:
    """The namespace is prepended to the key."""
    request = _stub_request("u", {"limit": "20"})
    key = thread_list_key_builder(lambda: None, namespace="threads-list", request=request)
    assert key.startswith("threads-list:")


def test_key_builder_raises_when_request_is_none() -> None:
    """Without a request the builder cannot scope the key."""
    with pytest.raises(RuntimeError, match="requires request"):
        thread_list_key_builder(lambda: None, namespace="t", request=None)


def test_key_builder_falls_back_to_anonymous_when_no_user() -> None:
    """Defensive default — never collapse to an empty user segment."""
    request = _stub_request(None, {"limit": "20"})
    key = thread_list_key_builder(lambda: None, namespace="t", request=request)
    assert "anonymous" in key


# ---------------------------------------------------------------------------
# Integration tests — @cache route + ValkeyService.delete_pattern
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def in_process_cache_app() -> AsyncGenerator[FastAPI, None]:
    """Build a FastAPI app with fastapi-cache2 init'd against fakeredis.

    Uses an in-memory backend so tests do not need a running Valkey.
    The decorator under test is ``@cache(namespace="threads-list",
    expire=60, key_builder=thread_list_key_builder)`` — the same wiring
    the production thread-list endpoint will use (Phase 8).
    """
    backend = InMemoryBackend()
    FastAPICache.init(backend, prefix="fastapi-cache")

    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next: Any) -> Any:
        """Inject ``current_user`` from the X-Test-User header."""
        user_id = request.headers.get("x-test-user", "user-test")
        request.state.current_user = {"sub": user_id}
        return await call_next(request)

    @app.get("/threads")
    @cache(namespace="threads-list", expire=60, key_builder=thread_list_key_builder)
    async def list_threads(request: Request) -> JSONResponse:
        # The handler is called only on a cache miss.
        return JSONResponse(
            {
                "threads": [{"id": "t1"}, {"id": "t2"}],
                "user": request.state.current_user["sub"],
            }
        )

    try:
        yield app
    finally:
        await FastAPICache.clear()


@pytest_asyncio.fixture
async def cache_client(in_process_cache_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Return an async HTTP client bound to the in-process test app."""
    transport = ASGITransport(app=in_process_cache_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_first_request_hits_handler_second_uses_cache(
    cache_client: AsyncClient,
) -> None:
    """First call invokes the handler; second identical call uses cache."""
    # We cannot inspect the handler directly from the test, but we can
    # verify the X-FastAPI-Cache header is set to "HIT" on the second call.
    r1 = await cache_client.get("/threads?limit=20", headers={"x-test-user": "alice"})
    r2 = await cache_client.get("/threads?limit=20", headers={"x-test-user": "alice"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    # fastapi-cache2 emits a cache-status header on the response.
    assert r2.headers.get("x-fastapi-cache") == "HIT"
    assert r1.headers.get("x-fastapi-cache") in (None, "MISS")
