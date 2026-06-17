"""Integration tests — /health and /ready endpoints against the running app.

After the /health vs /ready split (FR-105 liveness, FR-106 readiness):

- ``GET /health`` is the liveness probe.  It returns 200 with
  ``status='ok'`` as long as the FastAPI process is alive.  No
  external dependency checks are performed (so the ``checks`` field
  is always empty in the response).
- ``GET /ready`` is the readiness probe.  It returns 200 only when
  PostgreSQL, Qdrant, and Valkey are all reachable.  The ``checks``
  field reports per-dependency status.

This file covers both endpoints, scoped to their current role.
Degraded / failing-dependency cases for ``/ready`` are covered by
``test_ready_degraded.py``.
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
    """GET /health must return HTTP 200 when the process is alive (FR-105)."""
    response = await app_client.get("/health")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}. Response: " + response.text
    )


async def test_health_status_ok(app_client: httpx.AsyncClient) -> None:
    """GET /health body must contain status='ok' and an empty checks dict.

    The liveness probe intentionally reports no dependency checks; the
    response shape is fixed so Kubernetes liveness probes can rely on
    it.  The ``checks`` field is present but always empty.
    """
    response = await app_client.get("/health")
    body = response.json()
    assert body.get("status") == "ok", f"Unexpected status in response: {body}"
    assert body.get("checks") == {}, f"Liveness probe must not report checks: {body}"


async def test_health_response_schema(app_client: httpx.AsyncClient) -> None:
    """GET /health must include both 'status' and 'checks' keys."""
    response = await app_client.get("/health")
    body = response.json()
    assert "status" in body, f"'status' key missing from response: {body}"
    assert "checks" in body, f"'checks' key missing from response: {body}"
    assert isinstance(body["checks"], dict)


async def test_ready_returns_200_when_all_deps_healthy(
    app_client: httpx.AsyncClient,
) -> None:
    """GET /ready must return 200 with all dependency checks True (FR-106)."""
    response = await app_client.get("/ready")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}. Response: " + response.text
    )
    body = response.json()
    assert body.get("status") == "ok", f"Unexpected status: {body}"
    checks: dict[str, bool] = body.get("checks", {})
    assert checks.get("postgres") is True, f"postgres check failed: {checks}"
    assert checks.get("qdrant") is True, f"qdrant check failed: {checks}"
    assert checks.get("valkey") is True, f"valkey check failed: {checks}"
