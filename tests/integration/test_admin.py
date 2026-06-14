"""Integration tests — Phase 9 admin endpoints.

Exercises the new ``GET /api/v1/admin/threads`` and
``GET /api/v1/admin/reindex`` list endpoints against the running
FastAPI app with a real Saleor JWT.  The Phase 6 reindex trigger
and detail endpoints are covered in ``tests/integration/test_reindex.py``.

If the Docker stack or Saleor test users are not available, the
tests skip with a clear message (same pattern as
``tests/integration/test_threads.py``).

Test-user credentials live in ``.env.local`` (git-ignored) — see
``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 6.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import APP_URL, SALEOR_URL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------


def _stack_available() -> bool:
    """Return ``True`` when the app URL responds to ``GET /health``."""
    try:
        response = httpx.get(f"{APP_URL}/health", timeout=2.0)
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


def _saleor_available() -> bool:
    """Return ``True`` when Saleor's ``/graphql/`` GraphQL endpoint responds."""
    try:
        response = httpx.post(
            f"{SALEOR_URL}/graphql/",
            json={"query": "{ shop { name } }"},
            timeout=2.0,
        )
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


_SKIP_NO_STACK = pytest.mark.skipif(
    not _stack_available(),
    reason="Docker stack not running — skipping Phase 9 admin integration tests",
)

_SKIP_NO_SALEOR = pytest.mark.skipif(
    not _saleor_available(),
    reason="Saleor not reachable — skipping JWT-minting admin tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_jwt() -> AsyncGenerator[str, None]:
    """Mint a Saleor JWT for the regular (non-staff) test user.

    Asserts the account is non-staff so the test exercises the 403
    path on admin endpoints.
    """
    email = os.environ.get("SALEOR_TEST_USER_EMAIL", "")
    password = os.environ.get("SALEOR_TEST_USER_PASSWORD", "")
    if not email or not password:
        pytest.skip(
            "SALEOR_TEST_USER_EMAIL / SALEOR_TEST_USER_PASSWORD not set — "
            "see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6."
        )
    async with httpx.AsyncClient(base_url=SALEOR_URL, timeout=5.0) as client:
        response = await client.post(
            "/graphql/",
            json={
                "query": (
                    'mutation { tokenCreate(email: "'
                    + email
                    + '", password: "'
                    + password
                    + '") { token user { isStaff } errors { field message } } }'
                ),
            },
        )
    data = response.json()["data"]["tokenCreate"]
    if not data.get("token"):
        pytest.fail(
            "Could not mint test-user JWT — check SALEOR_TEST_USER_EMAIL / "
            f"SALEOR_TEST_USER_PASSWORD: {data.get('errors')}"
        )
    assert data["user"]["isStaff"] is False, (
        "SALEOR_TEST_USER_EMAIL must point to a non-staff account"
    )
    yield data["token"]


@pytest_asyncio.fixture
async def staff_jwt() -> AsyncGenerator[str, None]:
    """Mint a Saleor JWT for the staff test user.

    Asserts the account is staff so the test exercises the 200 path
    on admin endpoints.
    """
    email = os.environ.get("SALEOR_TEST_STAFF_EMAIL", "")
    password = os.environ.get("SALEOR_TEST_STAFF_PASSWORD", "")
    if not email or not password:
        pytest.skip(
            "SALEOR_TEST_STAFF_EMAIL / SALEOR_TEST_STAFF_PASSWORD not set — "
            "see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6."
        )
    async with httpx.AsyncClient(base_url=SALEOR_URL, timeout=5.0) as client:
        response = await client.post(
            "/graphql/",
            json={
                "query": (
                    'mutation { tokenCreate(email: "'
                    + email
                    + '", password: "'
                    + password
                    + '") { token user { isStaff } errors { field message } } }'
                ),
            },
        )
    data = response.json()["data"]["tokenCreate"]
    if not data.get("token"):
        pytest.fail(
            "Could not mint test-staff JWT — check SALEOR_TEST_STAFF_EMAIL / "
            f"SALEOR_TEST_STAFF_PASSWORD: {data.get('errors')}"
        )
    assert data["user"]["isStaff"] is True, "SALEOR_TEST_STAFF_EMAIL must point to a staff account"
    yield data["token"]


@pytest_asyncio.fixture
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an ``httpx.AsyncClient`` bound to the running FastAPI app."""
    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# GET /api/v1/admin/threads  (Phase 9, D9.4)
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
async def test_list_admin_threads_without_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    """No ``Authorization`` header → 401 (bearer scheme)."""
    response = await api_client.get("/api/v1/admin/threads")
    assert response.status_code == 401


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_admin_threads_for_non_admin_returns_403(
    api_client: httpx.AsyncClient,
    user_jwt: str,
) -> None:
    """A regular customer JWT (is_staff=false) gets 403, not 200."""
    response = await api_client.get(
        "/api/v1/admin/threads",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 403


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_admin_threads_returns_paginated_envelope(
    api_client: httpx.AsyncClient,
    staff_jwt: str,
) -> None:
    """The staff JWT sees a ``{items, next_cursor}`` envelope with
    real thread rows from the DB (newest first)."""
    response = await api_client.get(
        "/api/v1/admin/threads",
        params={"limit": 5},
        headers={"Authorization": f"Bearer {staff_jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "next_cursor" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 5
    # Each item is a ThreadResponse shape.
    if body["items"]:
        first = body["items"][0]
        assert {"id", "title", "status", "created_at"} <= set(first.keys())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/reindex  (Phase 9, D9.5' + D9.6')
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
async def test_list_reindex_jobs_without_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.get("/api/v1/admin/reindex")
    assert response.status_code == 401


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_reindex_jobs_for_non_admin_returns_403(
    api_client: httpx.AsyncClient,
    user_jwt: str,
) -> None:
    response = await api_client.get(
        "/api/v1/admin/reindex",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 403


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_reindex_jobs_returns_paginated_envelope(
    api_client: httpx.AsyncClient,
    staff_jwt: str,
) -> None:
    """The staff JWT sees a ``{items, next_cursor}`` envelope with the
    ``job_id`` field rename (D9.6') and no ``batches[]`` array."""
    response = await api_client.get(
        "/api/v1/admin/reindex",
        params={"limit": 5},
        headers={"Authorization": f"Bearer {staff_jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "next_cursor" in body
    assert isinstance(body["items"], list)
    # Each item is an IngestionJobSummary shape — `job_id` alias, no
    # `batches[]`.
    for item in body["items"]:
        assert "job_id" in item, "D9.6' — wire format must use job_id"
        assert "batches" not in item, "D9.6' — summary must omit batches[]"
        assert "id" not in item, "D9.6' — wire format must NOT use id"


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_reindex_jobs_with_unknown_cursor_returns_empty(
    api_client: httpx.AsyncClient,
    staff_jwt: str,
) -> None:
    """An unknown cursor UUID short-circuits to an empty page (matches
    the per-user thread list contract)."""
    response = await api_client.get(
        "/api/v1/admin/reindex",
        params={"limit": 5, "before": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {staff_jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None
