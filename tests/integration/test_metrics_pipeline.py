"""Integration tests — Phase 17 metrics pipeline (Alloy owns scraping).

Phase 17 moves every metrics scrape off Prometheus and into Grafana Alloy.
The ``prometheus.scrape`` blocks in ``docker/alloy/config.alloy`` reach into
five ``/metrics`` endpoints across the agentic-rag Docker network and
``prometheus.remote_write`` ships the samples to Prometheus at
``/api/v1/write``.  Prometheus itself is now a pure storage + query
backend (``scrape_configs`` is empty by design — see
``docker/prometheus/prometheus.yml``).

These tests guard the three observable contracts:

1. ``test_prometheus_yml_has_no_scrape_configs`` — static post-condition.
   If a future commit accidentally re-adds a ``scrape_configs`` block to
   ``prometheus.yml`` the test fails before the stack even starts, so an
   operator running ``docker compose up -d`` never silently ends up with
   two competing scrapers pushing duplicate series into Prometheus.
2. ``test_qdrant_collections_metric_round_trip`` — runtime smoke.  After
   scraping the Qdrant ``/metrics`` endpoint via Alloy → Prometheus, an
   instant query for ``collections_total`` must return at least one
   series.  This catches a wrong Alloy ``forward_to`` chain (e.g. typos
   in the ``prometheus.remote_write.default.receiver`` name) without
   needing a chat request.  ``collections_total`` is preferred over
   ``app_info`` because it is a value-bearing gauge present in every
   Qdrant ≥ 1.x build (app_info is also a label-only "info" metric).
3. ``test_all_five_scrape_jobs_are_up`` — D17.9.  A single ``up`` query
   filtered by ``job`` proves every scrape job is healthy: agentic-rag-app
   (FastAPI), qdrant, rabbitmq, redis-exporter, postgres-exporter.  This
   is the canonical "is the metrics pipeline alive?" check that future
   Grafana dashboards will sit on top of.

Stack requirements:

* Prometheus is running (``PROMETHEUS_TEST_URL/-/ready`` returns 200) for
  all three tests.  ``prometheus_ready`` skips gracefully when the
  observability stack is offline so the existing integration suite is
  not blocked.
* Tests 2 and 3 additionally require the five scrape targets to be
  reachable from the Alloy container on the agentic-rag Docker network.
  On a partial stack (e.g. ``docker compose up postgres app`` only)
  some jobs will report ``up=0``; the test still asserts the contract
  but skips the assertion if Prometheus itself is unreachable.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# docker/prometheus/prometheus.yml at the repo root.  The static check
# has to read the same file the operator's Prometheus container mounts,
# so a relative path is correct (the integration tests are run from
# the repo root).
PROMETHEUS_YML_PATH: Path = (
    Path(__file__).resolve().parents[2] / "docker" / "prometheus" / "prometheus.yml"
)

# Five scrape jobs defined in docker/alloy/config.alloy (D17.1-D17.5).
# This set is the canonical "the metrics pipeline is up" contract; any
# future addition of a sixth scrape target must update both this list
# and the Alloy config in the same change.
EXPECTED_SCRAPE_JOBS: frozenset[str] = frozenset(
    {
        "agentic-rag-app",
        "qdrant",
        "rabbitmq",
        "redis-exporter",
        "postgres-exporter",
    }
)


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


def _read_prometheus_yml() -> str:
    """Return the full contents of ``docker/prometheus/prometheus.yml``."""
    return PROMETHEUS_YML_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Async HTTP fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prometheus_client(
    prometheus_ready: str,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client pointing at Prometheus (skipped if offline).

    Function-scoped (default) because ``prometheus_ready`` is
    function-scoped — each test gets a fresh stack-up probe.
    """
    async with httpx.AsyncClient(base_url=prometheus_ready, timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_prometheus_yml_has_no_scrape_configs() -> None:
    """``docker/prometheus/prometheus.yml`` must not declare ``scrape_configs``.

    D7 — Prometheus is a pure storage + query backend.  All scraping is
    owned by Grafana Alloy.  If a future commit reintroduces a
    ``scrape_configs`` block the operator's stack would silently end up
    with two competing scrapers pushing duplicate series (e.g. both
    Alloy's ``prometheus.scrape "qdrant"`` and a Prometheus-native
    ``scrape_configs`` job) and Grafana queries would see double counts.

    The check is static (no live stack required) so it runs even on a
    developer machine that has only spun up the app subset.
    """
    prom_text = _read_prometheus_yml()
    assert "scrape_configs:" not in prom_text, (
        "docker/prometheus/prometheus.yml must not declare a "
        "'scrape_configs:' block — Phase 17 moved every scrape to "
        "grafana/alloy:v1.17.0. See history/17_0_0_METRICS_EXPANSION.md "
        "(D7) and docker/alloy/config.alloy for the canonical scrape "
        "definitions."
    )


async def test_qdrant_collections_metric_round_trip(
    prometheus_client: httpx.AsyncClient,
) -> None:
    """D17.5 — ``collections_total`` from Qdrant must round-trip through Prometheus.

    The pipeline under test is:

        Qdrant :6333/metrics  →  Alloy prometheus.scrape
                              →  prometheus.remote_write (default)
                              →  Prometheus :9090/api/v1/write
                              →  Prometheus TSDB

    An instant query for ``collections_total`` should return at least
    one series with ``job="qdrant"`` and a non-negative integer value.
    ``collections_total`` is preferred over ``app_info`` because:

    * It is a value-bearing gauge present in every Qdrant >= 1.x
      build, so the test does not break on a future Qdrant minor
      upgrade that renames the info metric.
    * The value (>0 once the Phase 6 RAG pipeline has created at
      least one collection) gives a stronger contract than
      ``app_info{version=~".+"}`` which is a label-only "info"
      metric that is always non-empty.
    """
    response = await prometheus_client.get(
        "/api/v1/query",
        params={"query": "collections_total"},
    )
    assert response.status_code == 200, (
        f"Prometheus query returned {response.status_code}: {response.text}"
    )
    payload = response.json()
    assert payload.get("status") == "success", (
        f"Prometheus query did not report success: {payload.get('error')}"
    )
    series = payload.get("data", {}).get("result", [])
    assert series, (
        "No series returned for 'collections_total'. The Qdrant "
        "scraper in docker/alloy/config.alloy is not reaching "
        "Prometheus. Check Alloy's forward_to chain and the "
        "prometheus.remote_write 'default' endpoint."
    )

    # Confirm at least one series is tagged job="qdrant" (defensive —
    # the metric name is unique to Qdrant, but the assertion makes the
    # contract explicit for a future reader).
    qdrant_series = [s for s in series if s.get("metric", {}).get("job") == "qdrant"]
    assert qdrant_series, (
        f"Prometheus returned {len(series)} series for collections_total "
        f"but none had job='qdrant': {[s.get('metric') for s in series]}"
    )

    # Value sanity: collections_total is a non-negative integer.  We
    # don't assert >= 1 because a fresh stack with no collections
    # ingested yet legitimately reports 0 — what matters is the
    # pipeline is wired.
    value = qdrant_series[0].get("value", [None, None])[1]
    assert value is not None, f"Qdrant series has no value: {qdrant_series[0]}"
    float_value = float(value)
    assert float_value >= 0, f"collections_total is unexpectedly negative: {float_value}"


async def test_all_five_scrape_jobs_are_up(
    prometheus_client: httpx.AsyncClient,
) -> None:
    """D17.9 — every Alloy scrape job must report ``up=1`` in Prometheus.

    The ``up`` metric is generated by Prometheus itself for every
    ``scrape_configs`` entry it knows about.  In Phase 17's architecture
    Prometheus sees no scrape configs of its own, but the
    ``prometheus.remote_write`` stream from Alloy carries an ``up``
    series per scrape job — so an instant query for ``up`` returns one
    series per Alloy job.

    The five jobs are the canonical set in
    ``docker/alloy/config.alloy`` (D17.1-D17.5).  Any new scrape
    target must be added to both the Alloy config AND this test in
    the same change.
    """
    response = await prometheus_client.get(
        "/api/v1/query",
        params={"query": "up"},
    )
    assert response.status_code == 200, (
        f"Prometheus query returned {response.status_code}: {response.text}"
    )
    payload = response.json()
    assert payload.get("status") == "success", (
        f"Prometheus query did not report success: {payload.get('error')}"
    )
    series = payload.get("data", {}).get("result", [])
    assert series, (
        "No 'up' series returned by Prometheus. The Alloy remote_write "
        "receiver is not connected — check Alloy's logs and the "
        "--web.enable-remote-write-receiver flag on the Prometheus "
        "service in docker-compose.yml."
    )

    # Group by job and assert the canonical set is present AND healthy.
    by_job: dict[str, str] = {s["metric"].get("job", "?"): s["value"][1] for s in series}
    observed_jobs = set(by_job.keys())
    missing_jobs = EXPECTED_SCRAPE_JOBS - observed_jobs
    assert not missing_jobs, (
        f"Expected scrape jobs {sorted(EXPECTED_SCRAPE_JOBS)} but Prometheus "
        f"only saw {sorted(observed_jobs)}. Missing: {sorted(missing_jobs)}. "
        "Check docker/alloy/config.alloy for the missing prometheus.scrape "
        "block and confirm the target's /metrics endpoint is reachable on "
        "the agentic-rag Docker network."
    )

    down_jobs = sorted(job for job, value in by_job.items() if value != "1")
    assert not down_jobs, (
        f"Scrape jobs reporting up=0: {down_jobs}. Check the corresponding "
        "/metrics endpoint (e.g. rabbitmq's :15692, exporter's :9121/:9187) "
        "is listening and the container is healthy."
    )
