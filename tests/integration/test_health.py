"""Integration tests — /health endpoint against the running app.

Verifies that:
- GET /health returns HTTP 200 when all services are up.
- The response body contains ``status: ok``.
- All three dependency checks (postgres, qdrant, valkey) are True.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio

from tests.integration.conftest import APP_URL


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client pointing at the running FastAPI app."""
    async with httpx.AsyncClient(base_url=APP_URL, timeout=10) as client:
        yield client


async def test_health_returns_200(app_client: httpx.AsyncClient) -> None:
    """GET /health must return HTTP 200 when all dependencies are healthy."""
    response = await app_client.get("/health")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}. Response: " + response.text
    )


async def test_health_status_ok(app_client: httpx.AsyncClient) -> None:
    """Response body must contain status='ok'."""
    response = await app_client.get("/health")
    body = response.json()
    assert body.get("status") == "ok", f"Unexpected status in response: {body}"


async def test_health_all_checks_true(app_client: httpx.AsyncClient) -> None:
    """All dependency checks (postgres, qdrant, valkey) must be True."""
    response = await app_client.get("/health")
    checks: dict[str, bool] = response.json().get("checks", {})
    assert checks.get("postgres") is True, f"postgres check failed: {checks}"
    assert checks.get("qdrant") is True, f"qdrant check failed: {checks}"
    assert checks.get("valkey") is True, f"valkey check failed: {checks}"


async def test_health_response_schema(app_client: httpx.AsyncClient) -> None:
    """Response must include both 'status' and 'checks' keys."""
    response = await app_client.get("/health")
    body = response.json()
    assert "status" in body, f"'status' key missing from response: {body}"
    assert "checks" in body, f"'checks' key missing from response: {body}"
    assert isinstance(body["checks"], dict)
