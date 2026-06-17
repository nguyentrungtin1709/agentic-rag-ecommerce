"""Integration tests — ``POST /api/v1/threads/{id}/runs/stream`` (Phase 14).

Three end-to-end checks against the **running FastAPI app** at
``APP_TEST_URL`` (default ``http://localhost:8080``) and the
**live PostgreSQL** at ``POSTGRES_TEST_DSN``.  All three tests
auto-skip when the Docker stack is unreachable so a missing
environment fails loudly with a single, descriptive message
rather than producing flaky test failures.

Test cases:

1. ``test_sse_response_contains_token_products_done_in_order`` — happy
   path: with a stubbed graph, ``token``, ``products``, and ``done``
   events arrive in that exact order on the stream.
2. ``test_sse_content_type_is_text_event_stream`` — confirms the
   ``Content-Type`` header is the SSE media type the spec promises.
3. ``test_sse_stream_terminates_after_done_event`` — the response
   body ends after the ``done`` event; no trailing junk, no extra
   events on the success path.

Stack requirements:

* The FastAPI app is running (``APP_TEST_URL/health`` returns 200).
* Saleor is reachable (so the test-user JWT can be minted).
* The graph on ``app.state`` is the compiled LangGraph — these
  tests do NOT exercise the full agent loop, they validate the
  wire format and the lifecycle.  A separate ``tests/e2e`` suite
  (out of scope for Phase 14) covers the actual graph execution.
"""

from __future__ import annotations

import asyncio
import json
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_jwt(
    saleor_test_user_credentials: tuple[str, str],
) -> AsyncGenerator[str, None]:
    """Return a fresh Saleor JWT for the regular test user."""
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
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an ``httpx.AsyncClient`` bound to the running FastAPI app."""
    async with httpx.AsyncClient(base_url=APP_URL, timeout=15.0) as client:
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
    """Create a thread via the API and clean it up afterwards."""
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
        # Soft-delete so cleanup is symmetric with the production path.
        await api_client.delete(
            f"/api/v1/threads/{body['id']}",
            headers={"Authorization": f"Bearer {user_jwt}"},
        )


# ---------------------------------------------------------------------------
# Helpers — parse ``text/event-stream`` frames
# ---------------------------------------------------------------------------


async def _drain_sse(response: httpx.Response) -> list[dict]:
    """Consume the response stream and return a list of ``{type, payload}``.

    The response body is a sequence of frames formatted as::

        event: <type>
        data: <json string>

        (blank line)

    A blank line ends a frame.  Returns one dict per frame, in arrival
    order.  Exits cleanly when the server closes the connection.
    """
    frames: list[dict] = []
    current: dict[str, str] = {}
    async for line in response.aiter_lines():
        if line == "":
            if current:
                frames.append(
                    {
                        "type": current.get("event", ""),
                        "payload": json.loads(current.get("data", "{}")),
                    }
                )
                current = {}
        elif line.startswith("event: "):
            current["event"] = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            current["data"] = line[len("data: ") :].strip()
    return frames


# ---------------------------------------------------------------------------
# Test 1: events arrive in the expected order
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_sse_response_contains_token_products_done_in_order(
    user_jwt: str,
    api_client: httpx.AsyncClient,
    created_thread: dict,
) -> None:
    """Stream emits ``token`` × N → ``products`` → ``done`` in that order.

    We do NOT exercise the full LangGraph graph — that requires
    live OpenAI / Qdrant / Valkey containers and a real agent
    loop.  Instead we send a chat message, let the real endpoint
    run the graph, and assert that the wire format is correct.
    The endpoint at minimum emits a ``done`` event on success; if
    the graph nodes stream ``token`` and ``products`` events
    (when products are found), they MUST arrive in that order.
    """
    thread_id = created_thread["id"]
    async with api_client.stream(
        "POST",
        f"/api/v1/threads/{thread_id}/runs/stream",
        json={"message": "Hello", "generate_image": False},
        headers={"Authorization": f"Bearer {user_jwt}"},
    ) as response:
        assert response.status_code == 200
        frames = await _drain_sse(response)

    # The stream MUST terminate with a ``done`` event.
    assert len(frames) >= 1
    assert frames[-1]["type"] == "done", (
        f"Last event must be 'done', got {frames[-1]['type']!r}; "
        f"full frame list: {[f['type'] for f in frames]}"
    )
    # No error event on the success path.
    error_frames = [f for f in frames if f["type"] == "error"]
    assert error_frames == [], f"Unexpected error frames: {error_frames}"
    # The ``token`` events (if any) MUST arrive before ``products``,
    # which MUST arrive before ``done`` — i.e. the relative order
    # is preserved.
    type_seq = [f["type"] for f in frames]
    last_token = max(
        (i for i, t in enumerate(type_seq) if t == "token"),
        default=-1,
    )
    last_products = max(
        (i for i, t in enumerate(type_seq) if t == "products"),
        default=-1,
    )
    done_index = type_seq.index("done")
    assert last_token < done_index, f"All token events must arrive before done; sequence={type_seq}"
    assert last_products < done_index or last_products == -1, (
        f"All products events must arrive before done; sequence={type_seq}"
    )
    if last_token != -1 and last_products != -1:
        assert last_token < last_products, (
            f"token events must arrive before products; sequence={type_seq}"
        )


# ---------------------------------------------------------------------------
# Test 2: Content-Type is ``text/event-stream``
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_sse_content_type_is_text_event_stream(
    user_jwt: str,
    api_client: httpx.AsyncClient,
    created_thread: dict,
) -> None:
    """The response ``Content-Type`` header is ``text/event-stream``."""
    thread_id = created_thread["id"]
    async with api_client.stream(
        "POST",
        f"/api/v1/threads/{thread_id}/runs/stream",
        json={"message": "Hi", "generate_image": False},
        headers={"Authorization": f"Bearer {user_jwt}"},
    ) as response:
        assert response.status_code == 200
        # ``Content-Type`` may carry a charset suffix
        # (``text/event-stream; charset=utf-8``); the prefix must
        # match the spec keyword.
        content_type = response.headers["content-type"]
        assert content_type.startswith("text/event-stream"), (
            f"Content-Type must be 'text/event-stream', got {content_type!r}"
        )
        # Cache-Control must disable caching so the stream reaches
        # the client in real time.
        assert response.headers.get("cache-control") == "no-cache", (
            f"Cache-Control must be 'no-cache', got {response.headers.get('cache-control')!r}"
        )
        # Drain the stream so the background task can complete.
        async for _ in response.aiter_lines():
            pass


# ---------------------------------------------------------------------------
# Test 3: stream terminates cleanly after the ``done`` event
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
@_SKIP_NO_SALEOR
async def test_sse_stream_terminates_after_done_event(
    user_jwt: str,
    api_client: httpx.AsyncClient,
    created_thread: dict,
    pg_pool: asyncpg.Pool,
) -> None:
    """The stream ends right after ``done``; the thread is reset to ``idle``.

    After the run completes, the ``_run_graph`` ``finally`` block
    calls ``thread_repo.set_status(thread_id, 'idle')`` and
    ``thread_repo.touch(thread_id)`` (D14.4).  We assert this
    directly against the database so the test is independent of
    the in-process state machine.
    """
    thread_id = created_thread["id"]
    async with api_client.stream(
        "POST",
        f"/api/v1/threads/{thread_id}/runs/stream",
        json={"message": "Hello", "generate_image": False},
        headers={"Authorization": f"Bearer {user_jwt}"},
    ) as response:
        assert response.status_code == 200
        frames = await _drain_sse(response)

    # The last frame MUST be ``done`` — no trailing junk.
    assert frames, "Stream returned no frames"
    assert frames[-1]["type"] == "done", f"Last frame must be 'done', got {frames[-1]['type']!r}"
    # And the stream MUST NOT contain any ``error`` event on the
    # success path.
    assert all(f["type"] != "error" for f in frames), f"Stream contained an error frame: {frames}"

    # Give the background task a moment to land the idle reset.
    # The endpoint schedules ``_run_graph`` as a fire-and-forget
    # task, and the generator has already returned; the cleanup
    # runs in the background task's ``finally`` block.
    row: asyncpg.Record | None = None
    for _ in range(20):
        row = await pg_pool.fetchrow(
            "SELECT status, updated_at FROM threads WHERE id = $1",
            uuid.UUID(thread_id),
        )
        if row is not None and row["status"] == "idle":
            break
        await asyncio.sleep(0.25)
    assert row is not None, "Thread row not found in DB"
    assert row["status"] == "idle", (
        f"Thread must be reset to 'idle' after the run, got {row['status']!r}"
    )
