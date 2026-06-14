"""Integration tests — ``/api/v1/threads`` end-to-end with real Saleor JWTs.

These tests exercise the threads endpoints against the **running FastAPI
app** (``APP_TEST_URL``) and the **real Saleor instance** at
``SALEOR_TEST_URL``.  Each test mints a fresh Saleor JWT via
``tokenCreate`` using the credentials loaded from
``.env.local`` (git-ignored) — see
``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 6 for the full
``accountRegister`` / ``staffCreate`` / ``confirmAccount`` /
``setPassword`` flow that creates the two test users.

Test users (env vars):

* ``SALEOR_TEST_USER_EMAIL`` / ``SALEOR_TEST_USER_PASSWORD`` — regular
  customer (``is_staff: false``), exercises the thread-owner path.
* ``SALEOR_TEST_STAFF_EMAIL`` / ``SALEOR_TEST_STAFF_PASSWORD`` —
  staff account (``is_staff: true``), exercises the admin paths.

Skip policy: every test in this module is skipped when the Docker
stack (or Saleor) is unreachable so a missing environment fails
loudly with a single, descriptive message rather than producing
flaky test failures.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import asyncpg
import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import APP_URL, POSTGRES_DSN, SALEOR_URL

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Stack-availability helpers
# ---------------------------------------------------------------------------


def _app_available() -> bool:
    """Return ``True`` when the FastAPI app responds to ``GET /health``."""
    try:
        response = httpx.get(f"{APP_URL}/health", timeout=2.0)
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


def _saleor_available() -> bool:
    """Return ``True`` when Saleor GraphQL responds with 200 OK."""
    try:
        response = httpx.post(
            f"{SALEOR_URL}/graphql/",
            json={"query": "{ __typename }"},
            timeout=2.0,
        )
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


_SKIP_NO_STACK = pytest.mark.skipif(
    not _app_available(),
    reason="FastAPI app not running at APP_TEST_URL — start with 'docker compose up -d'",
)
_SKIP_NO_SALEOR = pytest.mark.skipif(
    not _saleor_available(),
    reason="Saleor not running at SALEOR_TEST_URL — start with 'docker compose up -d'",
)


# ---------------------------------------------------------------------------
# Fixtures — Saleor JWTs minted from the test-user env vars
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_jwt(
    saleor_test_user_credentials: tuple[str, str],
) -> AsyncGenerator[str, None]:
    """Return a fresh Saleor JWT for the regular test user.

    Minted via ``tokenCreate`` against the live Saleor instance.
    Short-lived by design (Saleor issues 5-minute access tokens);
    the test that consumes this fixture is expected to finish
    well before the token expires.
    """
    email, password = saleor_test_user_credentials
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
async def staff_jwt(
    saleor_test_staff_credentials: tuple[str, str],
) -> AsyncGenerator[str, None]:
    """Return a fresh Saleor JWT for the staff test user."""
    email, password = saleor_test_staff_credentials
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


@pytest_asyncio.fixture
async def pg_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Open a short-lived asyncpg pool for direct DB assertions."""
    pool: asyncpg.Pool = await asyncpg.create_pool(
        POSTGRES_DSN,
        min_size=1,
        max_size=2,
    )
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def created_thread(
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> AsyncGenerator[dict, None]:
    """Create a thread via the API and clean it up afterwards.

    Yields the JSON body of the created thread (which carries the
    ``id``) so the test can re-use it.  Cleanup runs unconditionally
    (best effort) so the test database is not littered with rows
    after the suite finishes.
    """
    response = await api_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    try:
        yield body
    finally:
        # Soft-delete so cleanup is symmetric with the production
        # path.  We don't assert status here — a 410 just means
        # the test already deleted it.
        await api_client.delete(
            f"/api/v1/threads/{body['id']}",
            headers={"Authorization": f"Bearer {user_jwt}"},
        )


# ---------------------------------------------------------------------------
# 1-3. Auth — every endpoint requires a valid JWT
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
async def test_post_threads_without_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    """``POST /api/v1/threads`` rejects requests without a Bearer token."""
    response = await api_client.post("/api/v1/threads", json={})
    assert response.status_code == 401


@_SKIP_NO_STACK
async def test_get_thread_without_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    """``GET /api/v1/threads/{id}`` rejects requests without a Bearer token."""
    response = await api_client.get(f"/api/v1/threads/{uuid.uuid4()}")
    assert response.status_code == 401


@_SKIP_NO_STACK
async def test_history_without_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    """``GET /api/v1/threads/{id}/history`` rejects requests without a Bearer token."""
    response = await api_client.get(f"/api/v1/threads/{uuid.uuid4()}/history")
    assert response.status_code == 401


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_get_thread_with_invalid_jwt_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    """A syntactically-valid but signature-invalid Bearer token is rejected."""
    response = await api_client.get(
        f"/api/v1/threads/{uuid.uuid4()}",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 4-7. Happy path — thread CRUD with a real customer JWT
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_post_threads_creates_thread_for_authenticated_user(
    user_jwt: str,
    api_client: httpx.AsyncClient,
    pg_pool: asyncpg.Pool,
) -> None:
    """``POST /api/v1/threads`` inserts a row owned by the JWT subject."""
    response = await api_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "idle"
    assert body["title_generated"] is False
    thread_id = uuid.UUID(body["id"])

    # Verify the row was actually written to the live database.
    row = await pg_pool.fetchrow(
        "SELECT user_id, status, title FROM threads WHERE id = $1",
        thread_id,
    )
    assert row is not None
    assert row["status"] == "idle"
    # The user_id is the Saleor base64 user id (e.g. "VXNlcjo0Ng==");
    # we only assert non-empty so the test stays decoupled from the
    # specific test-user id.
    assert row["user_id"], "user_id must be persisted from the JWT subject"

    # Best-effort cleanup so the test database is not littered.
    await api_client.delete(
        f"/api/v1/threads/{thread_id}",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_get_thread_returns_404_for_random_uuid(
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """``GET /api/v1/threads/{random}`` returns 404 even with a valid JWT."""
    response = await api_client.get(
        f"/api/v1/threads/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 404
    assert "not found" in response.text.lower()


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_get_thread_returns_200_for_owner(
    created_thread: dict,
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """The owner of a thread can read it back with 200 OK."""
    response = await api_client.get(
        f"/api/v1/threads/{created_thread['id']}",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created_thread["id"]
    assert body["status"] == "idle"


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_list_threads_includes_newly_created_thread(
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """A freshly created thread must appear in the user's list response."""
    create_resp = await api_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert create_resp.status_code == 201
    new_id = create_resp.json()["id"]
    try:
        list_resp = await api_client.get(
            "/api/v1/threads",
            headers={"Authorization": f"Bearer {user_jwt}"},
        )
        assert list_resp.status_code == 200
        ids = [t["id"] for t in list_resp.json()["items"]]
        assert new_id in ids
    finally:
        await api_client.delete(
            f"/api/v1/threads/{new_id}",
            headers={"Authorization": f"Bearer {user_jwt}"},
        )


# ---------------------------------------------------------------------------
# 8. Cross-user isolation — staff JWT cannot see another user's thread
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_staff_cannot_read_other_users_thread_via_get(
    user_jwt: str,
    staff_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """A staff JWT on another user's thread returns 404 (D8.4).

    The endpoint does not leak existence — staff members get the
    same 404 a stranger would, so the contract is identical for
    all non-owner callers.
    """
    create_resp = await api_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert create_resp.status_code == 201
    new_id = create_resp.json()["id"]
    try:
        response = await api_client.get(
            f"/api/v1/threads/{new_id}",
            headers={"Authorization": f"Bearer {staff_jwt}"},
        )
        assert response.status_code == 404
    finally:
        await api_client.delete(
            f"/api/v1/threads/{new_id}",
            headers={"Authorization": f"Bearer {user_jwt}"},
        )


# ---------------------------------------------------------------------------
# 9. Soft-delete lifecycle — second DELETE returns 410
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_double_delete_returns_410(
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """A duplicate ``DELETE`` on an already-deleting thread is rejected with 410."""
    create_resp = await api_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert create_resp.status_code == 201
    new_id = create_resp.json()["id"]
    headers = {"Authorization": f"Bearer {user_jwt}"}

    first = await api_client.delete(f"/api/v1/threads/{new_id}", headers=headers)
    assert first.status_code == 202
    second = await api_client.delete(f"/api/v1/threads/{new_id}", headers=headers)
    assert second.status_code == 410


# ---------------------------------------------------------------------------
# 10. History — empty when the LangGraph checkpointer has no state
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_history_returns_empty_for_brand_new_thread(
    created_thread: dict,
    user_jwt: str,
    api_client: httpx.AsyncClient,
) -> None:
    """A thread with no chat runs has an empty history payload.

    The checkpointer is mounted by ``app.main.lifespan`` and starts
    empty for every new thread, so the response is the
    ``{"messages": [], "next_cursor": None}`` sentinel.
    """
    response = await api_client.get(
        f"/api/v1/threads/{created_thread['id']}/history",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["messages"] == []
    assert body["next_cursor"] is None
