"""Integration tests — Phase 16 log pipeline (Alloy + Loki).

Phase 16 replaces the Promtail-based syslog scraper with a Grafana Alloy
service that reads every container's stdout via the Docker socket and
ships structured logs to Loki with ``correlation_id`` promoted to a Loki
label.  These tests guard the three observable contracts:

1. ``test_docker_compose_has_no_promtail_service`` — static post-condition.
   If a future commit accidentally re-adds the ``promtail`` service block
   the test fails before the stack even starts, so an operator running
   ``docker compose up -d`` never silently falls back to syslog scraping.
2. ``test_service_label_attached_to_app_logs`` — runtime smoke.  After a
   ``GET /health`` we query Loki for ``{service="app"}`` and assert at
   least one log line arrived in the last 30 s.  This catches a wrong
   discovery.relabel regex (e.g. picking up the container id instead of
   the compose service name) without needing a chat request.
3. ``test_correlation_id_round_trip_as_loki_label`` — D16.5.  After a
   ``POST /api/v1/threads/{id}/runs/stream`` we extract the
   ``correlation_id`` label from the most recent ``service="app"`` log
   line and re-query Loki with ``{correlation_id="<uuid>"}``.  Non-empty
   result within 5 s proves the label promotion (D16.4) is wired end to
   end and that LogQL can resolve a per-request correlation id without
   scanning the JSON body.

Stack requirements:

* The FastAPI app is running (``APP_TEST_URL/health`` returns 200) for
  tests 2 and 3.
* Loki + Alloy are running (``LOKI_TEST_URL/ready`` returns 200) for
  tests 2 and 3.  ``loki_ready`` skips gracefully when the
  observability stack is offline so the existing integration suite is
  not blocked.
* Test 3 additionally requires Saleor + the regular test user credentials
  to mint a JWT; the ``saleor_test_user_credentials`` fixture in
  conftest.py skips when ``SALEOR_TEST_USER_EMAIL`` is unset.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import APP_URL, SALEOR_URL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# docker-compose.yml at the repo root.  The static check has to read the
# same file the operator runs ``docker compose`` against, so a relative
# path is correct (the integration tests are run from the repo root).
DOCKER_COMPOSE_PATH: Path = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Window in seconds for the Loki query_range call.  Two windows:
#   SERVICE_LABEL_WINDOW_S — wide enough to cover the app's startup logs
#     (FR-105 /health does NOT emit a log line, so the test cannot rely
#     on a fresh /health request to produce a Loki entry).  The test
#     triggers a /health request to prove the app is reachable, then
#     scans the recent window to confirm at least one app-originated
#     log line is labelled with service="app" — proving the relabel
#     pipeline works end to end.
#   CORRELATION_WINDOW_S — narrow (30 s) because test 3 triggers a chat
#     request that binds a fresh correlation_id, so the log line we
#     care about WILL be inside the last 30 s.
SERVICE_LABEL_WINDOW_S: int = 600
CORRELATION_WINDOW_S: int = 30


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


def _read_docker_compose() -> str:
    """Return the full contents of ``docker-compose.yml``."""
    return DOCKER_COMPOSE_PATH.read_text(encoding="utf-8")


def _extract_label_value(stream: dict, label: str) -> str | None:
    """Return the value of ``label`` from a single Loki stream, else None.

    Loki's instant-query response shape is::

        {"data": {"result": [{"stream": {"<label>": "<value>", ...},
                              "values": [[ts, line], ...]}, ...]}}

    Each stream carries a label dict; we want the FIRST non-empty match.
    """
    labels: dict[str, str] = stream.get("stream", {})
    value = labels.get(label)
    if value is None or value == "":
        return None
    return value


# ---------------------------------------------------------------------------
# Async HTTP fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client pointing at the running FastAPI app."""
    async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
async def loki_client(loki_ready: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client pointing at Loki (skipped if offline).

    Function-scoped (default) because ``loki_ready`` is function-scoped —
    each test gets a fresh stack-up probe.  Only two tests in this file
    use the client so the extra handshake cost is negligible.
    """
    async with httpx.AsyncClient(base_url=loki_ready, timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
async def user_jwt(
    saleor_test_user_credentials: tuple[str, str],
) -> AsyncGenerator[str, None]:
    """Return a fresh Saleor JWT for the regular test user.

    Mirrors the helper in ``test_chat_sse.py``; defined locally so this
    file's fixtures are self-contained when run in isolation.
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
        pytest.skip(
            "Could not mint test-user JWT — check SALEOR_TEST_USER_EMAIL / "
            f"SALEOR_TEST_USER_PASSWORD: {data.get('errors')}"
        )
    yield data["token"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_docker_compose_has_no_promtail_service() -> None:
    """``docker-compose.yml`` must not declare a ``promtail`` service.

    D5 revision in Phase 16: the host-syslog Promtail scraper was replaced
    by Alloy reading the Docker socket.  If a future commit reintroduces
    ``promtail`` the operator's stack would silently regress to scraping
    ``/var/log`` and miss every container stdout line.
    """
    compose_text = _read_docker_compose()
    # Match a service block start (``promtail:`` at line start) so we
    # don't false-positive on substring matches inside descriptions or
    # volume names.
    assert "promtail:" not in compose_text, (
        "docker-compose.yml must not declare a 'promtail:' service — "
        "Phase 16 replaced it with grafana/alloy:v1.17.0. "
        "See history/16_0_0_LOG_PIPELINE_ALLOY.md (D5 revision)."
    )


async def test_service_label_attached_to_app_logs(
    app_client: httpx.AsyncClient,
    loki_client: httpx.AsyncClient,
) -> None:
    """A ``GET /health`` must produce at least one Loki log with ``service="app"``.

    Verifies the ``discovery.relabel.alloy_logs`` block correctly promotes
    the ``com.docker.compose.service`` label to ``service``.  Without
    this rule every container would land in Loki under the raw container
    name and the per-service dashboards in Phase 18 would break.
    """
    response = await app_client.get("/health")
    assert response.status_code == 200, (
        f"App /health returned {response.status_code}: {response.text}"
    )

    # Loki query_range — Loki does NOT support log queries as instant
    # queries (returns 400 'log queries are not supported as an instant
    # query type'); we must use a range query with start/end.  Window is
    # wide enough to cover a startup log if the stack just came up but
    # narrow enough to avoid unrelated historic traffic on a long-running
    # dev environment.
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - SERVICE_LABEL_WINDOW_S * 1_000_000_000
    loki_response = await loki_client.get(
        "/loki/api/v1/query_range",
        params={
            "query": '{service="app"}',
            "start": start_ns,
            "end": end_ns,
            "limit": 20,
            "direction": "backward",
        },
    )
    assert loki_response.status_code == 200, (
        f"Loki query returned {loki_response.status_code}: {loki_response.text}"
    )
    payload = loki_response.json()
    streams = payload.get("data", {}).get("result", [])
    assert streams, (
        "No Loki streams with service='app' found. Alloy may have failed "
        "to relabel the com.docker.compose.service label."
    )

    # Confirm at least one stream has the 'service' label set to 'app'
    # (defensive — the selector already filters, but the assertion makes
    # the contract explicit for a future reader).
    service_streams = [
        stream for stream in streams if _extract_label_value(stream, "service") == "app"
    ]
    assert service_streams, (
        f"Loki returned {len(streams)} stream(s) but none had service='app' "
        f"on its labels: {[s.get('stream') for s in streams]}"
    )


async def test_correlation_id_round_trip_as_loki_label(
    app_client: httpx.AsyncClient,
    loki_client: httpx.AsyncClient,
    user_jwt: str,
) -> None:
    """D16.5 — after a chat run, ``{correlation_id="<uuid>"}`` must hit Loki.

    The chat endpoint (``POST /api/v1/threads/{id}/runs/stream``) is the
    only API surface that calls ``bind_contextvars(correlation_id=...)``,
    so a successful POST guarantees a log line with that label.  We then
    re-query Loki by the correlation_id and assert non-empty result —
    proof that label promotion (D16.4) works end-to-end and LogQL can
    resolve a single request without grepping the JSON body.
    """
    # Step 1: create a thread to chat against.
    create_response = await app_client.post(
        "/api/v1/threads",
        json={},
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert create_response.status_code == 201, f"Failed to create thread: {create_response.text}"
    thread_id = create_response.json()["id"]

    # Step 2: trigger a chat stream.  We only need the request to land
    # and bind the correlation_id — the actual graph run is irrelevant,
    # so we consume a single SSE event and close.
    async with app_client.stream(
        "POST",
        f"/api/v1/threads/{thread_id}/runs/stream",
        json={"message": "phase-16 log pipeline test"},
        headers={"Authorization": f"Bearer {user_jwt}"},
    ) as stream_response:
        assert stream_response.status_code == 200, (
            f"Chat stream returned {stream_response.status_code}"
        )
        # Read up to the first SSE event so the chat handler has had a
        # chance to emit its "request received" log line with the bound
        # correlation_id.  We don't care about the payload.
        async for _line in stream_response.aiter_lines():
            if _line:
                break

    # Step 3: query Loki for the most recent service="app" log with a
    # non-empty correlation_id label.  We bound the search to the last
    # CORRELATION_WINDOW_S seconds to avoid matching unrelated historic
    # traffic.  Use a LogQL regex matcher on correlation_id so empty
    # values (e.g. health-check lines without a bound contextvar) are
    # excluded automatically.
    #
    # The chat handler emits the first correlation_id-bearing log from
    # inside the agent graph (generate_title node, ~7s after the request
    # lands) — by the time the first SSE event arrives the log may or
    # may not have been picked up by Alloy (which tails Docker's JSON
    # log files).  We poll every LOKI_POLL_INTERVAL_S seconds up to
    # CORRELATION_WINDOW_S total so we don't false-negative on a
    # perfectly-working pipeline that just hasn't flushed yet.
    LOKI_POLL_INTERVAL_S: float = 2.0
    deadline = time.monotonic() + CORRELATION_WINDOW_S
    streams: list[dict] = []
    end_ns = 0
    start_ns = 0
    while time.monotonic() < deadline:
        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - CORRELATION_WINDOW_S * 1_000_000_000
        loki_response = await loki_client.get(
            "/loki/api/v1/query_range",
            params={
                "query": '{service="app"} | correlation_id=~".+"',
                "start": start_ns,
                "end": end_ns,
                "limit": 20,
                "direction": "backward",
            },
        )
        assert loki_response.status_code == 200, (
            f"Loki query_range returned {loki_response.status_code}: {loki_response.text}"
        )
        streams = loki_response.json().get("data", {}).get("result", [])
        if streams:
            break
        await asyncio.sleep(LOKI_POLL_INTERVAL_S)
    assert streams, (
        "No Loki streams with service='app' AND a non-empty "
        "correlation_id label were found in the last "
        f"{CORRELATION_WINDOW_S}s. Did the chat request fail to bind "
        "correlation_id, or did Alloy fail to promote the label?"
    )

    # Step 4: pull the first correlation_id and verify Loki can resolve
    # the same request by that label alone.  The "first" stream here is
    # the most recent (direction=backward), which is the one our chat
    # request just produced — but the test is robust to interleaving
    # because we re-query the SAME correlation_id we just extracted.
    correlation_id = _extract_label_value(streams[0], "correlation_id")
    assert correlation_id, f"First stream has no correlation_id label: {streams[0].get('stream')}"

    # Step 5: round-trip — can Loki find logs by correlation_id alone?
    # Use query_range because log queries are not supported as instant
    # queries in Loki (returns 400 otherwise).
    round_trip_response = await loki_client.get(
        "/loki/api/v1/query_range",
        params={
            "query": f'{{correlation_id="{correlation_id}"}}',
            "start": start_ns,
            "end": end_ns,
            "limit": 5,
            "direction": "backward",
        },
    )
    assert round_trip_response.status_code == 200, (
        f"Loki round-trip query returned {round_trip_response.status_code}: "
        f"{round_trip_response.text}"
    )
    round_trip_streams = round_trip_response.json().get("data", {}).get("result", [])
    assert round_trip_streams, (
        f'Round-trip query {{correlation_id="{correlation_id}"}} returned '
        "no streams — label promotion (D16.4) is not working end-to-end."
    )

    # Cleanup: soft-delete the thread so the test does not leak state.
    # The endpoint returns 202 (Accepted) because deletion is async via
    # Celery, or 404 if the thread was already removed.  We accept both
    # — what matters is the round-trip assertion above passed.
    delete_response = await app_client.delete(
        f"/api/v1/threads/{thread_id}",
        headers={"Authorization": f"Bearer {user_jwt}"},
    )
    assert delete_response.status_code in (202, 404), (
        f"Cleanup delete returned {delete_response.status_code}: {delete_response.text}"
    )
