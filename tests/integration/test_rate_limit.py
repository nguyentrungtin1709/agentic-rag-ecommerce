"""Integration tests — rate limiting (slowapi) wiring.

Verifies the slowapi ``Limiter`` is correctly wired in ``main.py``:

- ``get_jwt_user_id_or_ip`` reads the ``sub`` claim from a Bearer
  token; falls back to client IP when no token is present.
- The ``@limiter.exempt`` decoration on health, ready, and webhook
  routes marks them as bypassed by the middleware.
- The rate limiter returns HTTP 429 with a ``Retry-After`` header
  once the per-user budget is exhausted.
- Different authenticated users maintain independent buckets.

These tests build a small FastAPI app in-process — they do NOT need
the full Docker Compose stack to be running.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.types import Scope

# Importing these modules triggers the @_limiter.exempt decorators
# to register the routes on the project's process-wide Limiter.
from app.api import health as _health  # noqa: F401
from app.api import webhooks as _webhooks  # noqa: F401
from app.auth.jwt_verifier import get_jwt_user_id_or_ip
from app.rate_limit import get_limiter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 64-char secret satisfies the RFC 7518 minimum (32 bytes for SHA256).
_JWT_SECRET = "a" * 64


def _make_jwt(user_id: str, *, expired: bool = False) -> str:
    """Build a minimal HS256 JWT for the rate-limit key tests.

    The token does NOT need to be a real Saleor JWT — the rate-limit
    key function only reads the ``sub`` claim and uses unverified
    decode. The signature is irrelevant.
    """
    now = datetime.now(UTC)
    exp = now - timedelta(minutes=1) if expired else now + timedelta(hours=1)
    payload = {"sub": user_id, "iat": now, "exp": exp, "iss": "http://test.saleor"}
    return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


def _make_request(headers: dict[str, str]) -> Request:
    """Build a real Starlette ``Request`` for key_func unit tests.

    The rate-limit key function only reads ``request.headers`` and
    ``request.client.host``; a real ``Request`` (instead of a duck
    type) lets pyright verify the call without ``# type: ignore``.
    """
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }
    return cast(Request, StarletteRequest(scope=scope))


# ---------------------------------------------------------------------------
# Unit tests — get_jwt_user_id_or_ip
# ---------------------------------------------------------------------------


def test_get_jwt_user_id_or_ip_with_bearer_returns_sub_claim() -> None:
    """When a Bearer token is present, the sub claim is the key."""
    request = _make_request({"Authorization": f"Bearer {_make_jwt('user-123')}"})
    assert get_jwt_user_id_or_ip(request) == "user-123"


def test_get_jwt_user_id_or_ip_without_auth_returns_remote_address() -> None:
    """No Authorization header → fall back to client IP."""
    request = _make_request({})
    assert get_jwt_user_id_or_ip(request) == "127.0.0.1"


def test_get_jwt_user_id_or_ip_with_malformed_token_falls_back_to_ip() -> None:
    """A malformed token must not raise — fall back to client IP."""
    request = _make_request({"Authorization": "Bearer not-a-jwt"})
    assert get_jwt_user_id_or_ip(request) == "127.0.0.1"


def test_get_jwt_user_id_or_ip_with_token_missing_sub_falls_back_to_ip() -> None:
    """A well-formed token with no ``sub`` claim falls back to IP."""
    bad = jwt.encode(
        {"iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)},
        "secret",
        algorithm="HS256",
    )
    request = _make_request({"Authorization": f"Bearer {bad}"})
    assert get_jwt_user_id_or_ip(request) == "127.0.0.1"


# ---------------------------------------------------------------------------
# Exempt-route decoration — verified via the limiter's _exempt_routes set
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project_limiter() -> Limiter:
    """Return the project's process-wide Limiter, with routes registered.

    The ``app.api.health`` and ``app.api.webhooks`` modules are imported
    at module level (see top of file) and their ``@_limiter.exempt``
    decorators populate ``Limiter._exempt_routes`` at import time.

    We use the real singleton (no reset) because the decorator wraps
    the function only at the moment ``get_limiter()`` is first called
    in each module — resetting the singleton would create a fresh
    limiter that no longer has these routes registered.
    """
    return get_limiter()


def test_health_route_is_registered_as_exempt(project_limiter: Limiter) -> None:
    """``GET /health`` must be in the limiter's exempt set."""
    assert "app.api.health.health" in project_limiter._exempt_routes


def test_ready_route_is_registered_as_exempt(project_limiter: Limiter) -> None:
    """``GET /ready`` must be in the limiter's exempt set."""
    assert "app.api.health.ready" in project_limiter._exempt_routes


def test_saleor_webhook_route_is_registered_as_exempt(
    project_limiter: Limiter,
) -> None:
    """``POST /webhooks/saleor`` must be in the limiter's exempt set (FR-094)."""
    assert "app.api.webhooks.receive_saleor_webhook" in project_limiter._exempt_routes


# ---------------------------------------------------------------------------
# Integration tests — slowapi rate limit behavior (in-process)
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limited_app() -> FastAPI:
    """Build a fresh FastAPI app with a fresh in-memory limiter.

    Uses ``memory://`` storage so tests do not need a running Valkey.
    The 2/minute limit is intentionally tight to keep the test fast.
    """
    limiter = Limiter(
        key_func=lambda: "test-key",
        storage_uri="memory://",
        headers_enabled=True,
    )

    app = FastAPI()
    app.state.limiter = limiter

    # slowapi's handler has a narrower signature (request, RateLimitExceeded)
    # than FastAPI's ExceptionHandler (request, Exception).  Wrap to widen
    # the second param so pyright accepts the registration.
    async def _on_rate_limit_exceeded(request: Request, exc: Exception) -> Response:
        return _rate_limit_exceeded_handler(request, exc)  # type: ignore[arg-type]

    app.add_exception_handler(RateLimitExceeded, _on_rate_limit_exceeded)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/limited")
    @limiter.limit("2/minute")
    async def limited(request: Request, response: Response) -> Response:
        return Response(content='{"ok": true}', media_type="application/json")

    @app.get("/exempt")
    @limiter.exempt
    async def exempt() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest_asyncio.fixture
async def rate_limited_client(rate_limited_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Return an async HTTP client bound to the in-process test app."""
    transport = ASGITransport(app=rate_limited_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_3rd_request_returns_429(rate_limited_client: AsyncClient) -> None:
    """After 2 allowed requests, the 3rd must be rate-limited (429)."""
    r1 = await rate_limited_client.get("/limited")
    r2 = await rate_limited_client.get("/limited")
    r3 = await rate_limited_client.get("/limited")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


async def test_exempt_route_never_returns_429(rate_limited_client: AsyncClient) -> None:
    """An exempt route can be hit many times without rate-limiting."""
    for _ in range(5):
        r = await rate_limited_client.get("/exempt")
        assert r.status_code == 200


async def test_429_response_includes_retry_after_header(
    rate_limited_client: AsyncClient,
) -> None:
    """A 429 response must include ``Retry-After`` for clients."""
    await rate_limited_client.get("/limited")
    await rate_limited_client.get("/limited")
    blocked = await rate_limited_client.get("/limited")

    assert blocked.status_code == 429
    assert "retry-after" in {k.lower() for k in blocked.headers}


# ---------------------------------------------------------------------------
# Per-user isolation — build a key-aware test app
# ---------------------------------------------------------------------------


@pytest.fixture
def per_user_app() -> FastAPI:
    """Build a test app that keys the limiter on the ``sub`` claim.

    Mirrors the production wiring: ``get_jwt_user_id_or_ip`` is the
    key_func, and the test client sends a different Bearer token for
    each user.
    """
    limiter = Limiter(
        key_func=get_jwt_user_id_or_ip,
        storage_uri="memory://",
        headers_enabled=True,
    )

    app = FastAPI()
    app.state.limiter = limiter

    async def _on_rate_limit_exceeded(request: Request, exc: Exception) -> Response:
        return _rate_limit_exceeded_handler(request, exc)  # type: ignore[arg-type]

    app.add_exception_handler(RateLimitExceeded, _on_rate_limit_exceeded)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/limited")
    @limiter.limit("2/minute")
    async def limited(request: Request, response: Response) -> Response:
        return Response(content='{"ok": true}', media_type="application/json")

    return app


@pytest_asyncio.fixture
async def per_user_client(per_user_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Return an async HTTP client bound to the per-user test app."""
    transport = ASGITransport(app=per_user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_rate_limit_buckets_are_per_user(per_user_client: AsyncClient) -> None:
    """User A exhausting their budget must not block user B."""
    headers_a = {"Authorization": f"Bearer {_make_jwt('user-a')}"}
    headers_b = {"Authorization": f"Bearer {_make_jwt('user-b')}"}

    # User A: 2 successes then 1 failure
    assert (await per_user_client.get("/limited", headers=headers_a)).status_code == 200
    assert (await per_user_client.get("/limited", headers=headers_a)).status_code == 200
    assert (await per_user_client.get("/limited", headers=headers_a)).status_code == 429

    # User B: independent bucket — still allowed
    assert (await per_user_client.get("/limited", headers=headers_b)).status_code == 200
    assert (await per_user_client.get("/limited", headers=headers_b)).status_code == 200
    assert (await per_user_client.get("/limited", headers=headers_b)).status_code == 429
