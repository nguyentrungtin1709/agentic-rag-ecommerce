"""Integration tests — /health degraded behaviour.

Tests that the health endpoint returns HTTP 503 and ``status: degraded``
when one or more dependency checks fail.  Uses a minimal in-process
FastAPI application with mocked services to avoid depending on the full
application lifespan while still exercising the actual health-check logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router


def _make_app(
    *,
    postgres_ok: bool = True,
    qdrant_ok: bool = True,
    valkey_ok: bool = True,
) -> FastAPI:
    """Return a minimal FastAPI app with the health router and mocked state.

    Args:
        postgres_ok: Whether the mock asyncpg pool returns successfully.
        qdrant_ok: Whether the mock Qdrant client returns successfully.
        valkey_ok: Whether the mock Valkey client ping returns True.
    """
    app = FastAPI()

    # Qdrant mock
    qdrant_mock = MagicMock()
    qdrant_mock.collection_name = "products"
    if qdrant_ok:
        qdrant_mock.client.get_collection = AsyncMock(return_value=MagicMock())
    else:
        qdrant_mock.client.get_collection = AsyncMock(side_effect=ConnectionError("qdrant down"))

    # Valkey mock
    valkey_mock = MagicMock()
    valkey_mock.ping = AsyncMock(return_value=valkey_ok)

    app.state.qdrant = qdrant_mock
    app.state.valkey = valkey_mock

    app.include_router(router)
    return app


def _mock_pg_pool(success: bool) -> MagicMock:
    """Return a mock asyncpg pool that either succeeds or raises."""
    pool = MagicMock()
    conn = AsyncMock()
    if success:
        conn.fetchval = AsyncMock(return_value=1)
    else:
        conn.fetchval = AsyncMock(side_effect=ConnectionError("postgres down"))
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


# ---------------------------------------------------------------------------
# Tests — individual dependency failures
# ---------------------------------------------------------------------------


def test_health_degraded_when_valkey_fails() -> None:
    """503 + status='degraded' when Valkey ping returns False."""
    app = _make_app(valkey_ok=False)
    with (
        patch("app.api.health.get_asyncpg_pool", return_value=_mock_pg_pool(True)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["valkey"] is False
    assert body["checks"]["postgres"] is True
    assert body["checks"]["qdrant"] is True


def test_health_degraded_when_qdrant_fails() -> None:
    """503 + status='degraded' when Qdrant raises a connection error."""
    app = _make_app(qdrant_ok=False)
    with (
        patch("app.api.health.get_asyncpg_pool", return_value=_mock_pg_pool(True)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["qdrant"] is False
    assert body["checks"]["postgres"] is True
    assert body["checks"]["valkey"] is True


def test_health_degraded_when_postgres_fails() -> None:
    """503 + status='degraded' when the asyncpg pool raises a connection error."""
    app = _make_app()
    with (
        patch("app.api.health.get_asyncpg_pool", return_value=_mock_pg_pool(False)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] is False
    assert body["checks"]["qdrant"] is True
    assert body["checks"]["valkey"] is True


def test_health_degraded_when_all_fail() -> None:
    """503 + all checks False when every dependency is down."""
    app = _make_app(qdrant_ok=False, valkey_ok=False)
    with (
        patch("app.api.health.get_asyncpg_pool", return_value=_mock_pg_pool(False)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert all(v is False for v in body["checks"].values())


def test_health_ok_when_all_pass() -> None:
    """200 + status='ok' when all mock services succeed (sanity check)."""
    app = _make_app()
    with (
        patch("app.api.health.get_asyncpg_pool", return_value=_mock_pg_pool(True)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert all(v is True for v in body["checks"].values())
