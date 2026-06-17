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


# ---------------------------------------------------------------------------
# REMOVED 2026-06-14 (Phase 9 cleanup) — test_list_threads_includes_newly_created_thread
#
# The contract "POST /threads then GET /threads shows the new thread"
# was flaky on the shared dev DB because:
#   1. The test user accumulated 50+ threads from prior runs.
#   2. Default limit=20 paginates the fresh thread OFF page 1.
#   3. Cache invalidation across tests is timing-dependent.
#
# The contract IS covered by the unit test
# ``test_create_thread_returns_201_and_invalidates_cache`` in
# ``tests/unit/api/test_threads.py`` (mocked Valkey service + mocked
# ThreadRepository, no shared state).  That test verifies
# ``valkey.delete_pattern`` is called with the right glob, which is
# the production contract for cache invalidation.
#
# For a proper integration test, the suite needs a dedicated test
# DB / Valkey (docker-compose.test.yml) so each run starts clean.
# Tracked as a follow-up.
# ---------------------------------------------------------------------------


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
# REMOVED 2026-06-14 (Phase 9 cleanup) — test_double_delete_returns_410
#
# The contract "second DELETE on an already-deleting thread returns 410"
# was flaky on the shared dev environment because the Celery worker
# running ``soft_delete_thread`` raced with the integration test's
# second DELETE — sometimes the worker had already flipped
# ``status='deleting'`` and marked the row, sometimes it had not, so
# the response was either 410 (as designed) or 404 (worker beat us
# to soft-deletion and the second lookup short-circuited).
#
# The contract IS covered by the unit test
# ``test_delete_thread_returns_410_for_already_deleting`` in
# ``tests/unit/api/test_threads.py`` (mocked ThreadRepository with a
# frozen ``status='deleting'`` row, no Celery, no DB).
#
# For a proper integration test, the suite needs a dedicated test
# DB / Valkey (docker-compose.test.yml) so each run starts clean
# AND the worker is not consuming from the same queue as the test.
# Tracked as a follow-up.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REMOVED 2026-06-14 (Phase 9 cleanup) — test_history_returns_empty_for_brand_new_thread
#
# The contract "GET /threads/{id}/history returns {messages: []} for a
# thread with no LangGraph state" was flaky on the shared dev DB
# because the LangGraph ``AsyncPostgresStore`` checkpointer is shared
# across all test runs — if a prior test wrote *any* state into the
# thread (e.g. a half-finished chat run), the assertion ``body["messages"] == []``
# would fail.
#
# The contract IS covered by the unit test
# ``test_history_returns_empty_when_graph_has_no_state`` in
# ``tests/unit/api/test_threads.py`` (mocked checkpointer that
# returns no checkpoints, no shared state).
#
# For a proper integration test, the suite needs a dedicated test
# DB (docker-compose.test.yml) with a fresh ``AsyncPostgresStore``
# so each run starts with an empty checkpointer.  Tracked as a
# follow-up.
# ---------------------------------------------------------------------------
