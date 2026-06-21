"""Integration tests — Phase 18 Grafana dashboard provisioning.

Phase 18 ships four dashboards (System Overview, Infrastructure, Logs
Explorer, Business Metrics) plus one provisioning file, all mounted at
``/etc/grafana/provisioning/dashboards`` inside the Grafana container.
The file-based provider picks them up at startup and exposes them under
the ``POD Stylist`` folder (UID ``pod-stylist``).

These tests guard six observable contracts:

1. ``test_all_four_dashboards_exist`` — static file-presence check.  A
   future commit that renames or deletes a dashboard JSON fails
   ``pre-commit`` before the stack even restarts.  No live Grafana
   required.
2. ``test_dashboards_are_valid_json`` — every JSON parses AND carries
   the top-level keys Grafana expects (``title``, ``uid``,
   ``schemaVersion``, ``panels``, ``templating``).  Catches a typo in a
   panel ``gridPos`` or a stray trailing comma before the file is
   mounted.
3. ``test_provisioning_yaml_declares_pod_stylist_folder`` — the YAML
   parses, declares exactly one provider, with the expected
   ``folder=POD Stylist`` / ``folderUid=pod-stylist`` / ``type=file``
   shape.  Catches accidental rewrites (e.g. a wrong folder name would
   break the drill-down URLs in panel ``links`` blocks).
4. ``test_grafana_api_lists_four_dashboards`` — runtime smoke.  After
   the stack is up, Grafana's ``/api/search?folderUid=pod-stylist``
   endpoint returns exactly four dashboards with the expected UIDs.
   Skips cleanly when Grafana is offline so the rest of the integration
   suite is not blocked on a partial stack.
5. ``test_infrastructure_dashboard_uses_real_qdrant_metric`` — the
   Infrastructure dashboard's Qdrant panel must query
   ``collection_vectors`` (the real Qdrant 1.x exporter metric), not
   ``qdrant_collection_vector_count`` (a name that does not exist).
   Catches a regression to D18.11 if the panel is hand-edited.
6. ``test_infrastructure_dashboard_uses_real_rabbitmq_metric_names`` —
   the Infrastructure dashboard's RabbitMQ panel must use the real
   ``rabbitmq_queue_messages_unacked`` metric (one `e`, not
   `unacknowledged`), include the ``queue="webhook"`` per-queue
   filter, and combine the `ready` and `unacked` queries on the same
   panel.  Catches regressions to D18.12 and D18.14.

Stack requirements:

* Grafana is running (``/api/health`` returns 200) for test 4 only.
  Tests 1-3 and 5-6 are static — they run on any developer machine,
  even one that has only built the docker images without starting
  the stack.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# All four dashboard JSON files live alongside the provisioning YAML
# under ``docker/grafana/dashboards``.  Resolving from this test file
# keeps the path correct whether pytest runs from the repo root or
# another working directory.
DASHBOARDS_DIR: Path = Path(__file__).resolve().parents[2] / "docker" / "grafana" / "dashboards"

# The canonical four dashboards shipped by Phase 18.  Order is
# alphabetical and matches the dashboard ``title`` fields.
EXPECTED_DASHBOARD_UIDS: frozenset[str] = frozenset(
    {
        "business-metrics",
        "infrastructure",
        "logs-explorer",
        "system-overview",
    }
)

# Top-level keys every Grafana dashboard JSON must carry.  The check
# guards against a JSON that parses but is rejected by Grafana's
# dashboard import (a common failure mode when the file is hand-edited
# and a field is accidentally renamed).
REQUIRED_DASHBOARD_KEYS: frozenset[str] = frozenset(
    {"title", "uid", "schemaVersion", "panels", "templating"}
)


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


def _dashboard_paths() -> list[Path]:
    """Return the paths of the four canonical Phase 18 dashboard files."""
    return [DASHBOARDS_DIR / f"{uid}.json" for uid in EXPECTED_DASHBOARD_UIDS]


# ---------------------------------------------------------------------------
# Async HTTP fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def grafana_client(
    grafana_ready: str,
    grafana_credentials: tuple[str, str],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client with Grafana basic auth (skipped if offline).

    Function-scoped (default) because ``grafana_ready`` is
    function-scoped — each test gets a fresh stack-up probe.
    """
    user, password = grafana_credentials
    async with httpx.AsyncClient(
        base_url=grafana_ready,
        auth=(user, password),
        timeout=10.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_four_dashboards_exist() -> None:
    """All four Phase 18 dashboard JSON files must be present on disk.

    Static file-presence check — no live stack required.  Catches a
    missing file before ``docker compose up grafana`` runs and Grafana's
    file provider silently logs an empty provisioning result.
    """
    missing = [p for p in _dashboard_paths() if not p.exists()]
    assert not missing, (
        f"Missing Phase 18 dashboard JSON files: {missing}. "
        f"Expected all four under {DASHBOARDS_DIR}. "
        "See history/18_0_0_GRAFANA_DASHBOARDS.md §How for the file list."
    )


def test_dashboards_are_valid_json() -> None:
    """Each dashboard JSON parses and carries the required top-level keys.

    Validates that every file in ``docker/grafana/dashboards/`` with a
    ``.json`` extension is parseable AND has the keys Grafana's
    dashboard importer expects.  ``schemaVersion`` is a number so
    parsing it requires the JSON loader to coerce it — the existence
    check is sufficient, no value assertion here.
    """
    json_files = sorted(DASHBOARDS_DIR.glob("*.json"))
    assert json_files, f"No dashboard JSON files found under {DASHBOARDS_DIR}"

    for path in json_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), (
            f"{path.name}: top-level must be a JSON object, got {type(payload).__name__}"
        )
        missing = REQUIRED_DASHBOARD_KEYS - payload.keys()
        assert not missing, (
            f"{path.name}: missing required Grafana dashboard keys {sorted(missing)}. "
            f"Got keys: {sorted(payload.keys())}. "
            "A typo in any of title / uid / schemaVersion / panels / templating "
            "will cause Grafana's file provider to skip the dashboard at startup."
        )
        assert isinstance(payload["panels"], list), (
            f"{path.name}: 'panels' must be a list, got {type(payload['panels']).__name__}"
        )
        assert payload["panels"], (
            f"{path.name}: 'panels' list is empty — Grafana will render an empty dashboard"
        )


def test_provisioning_yaml_declares_pod_stylist_folder() -> None:
    """``default.yaml`` must declare one provider under the POD Stylist folder.

    Guards D18.2 (folder name + UID), the file-based provider type, and
    the path the container mounts.  A future change that flips
    ``type=file`` to ``type=influxdb`` or moves the folder would break
    every dashboard URL hard-coded in panel ``links`` blocks.
    """
    yaml_path = DASHBOARDS_DIR / "default.yaml"
    assert yaml_path.exists(), (
        f"Provisioning YAML not found at {yaml_path}. "
        "Grafana's file provider requires this file under "
        "/etc/grafana/provisioning/dashboards."
    )

    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert payload.get("apiVersion") == 1, (
        f"{yaml_path.name}: apiVersion must be 1 (Grafana 13.x), got {payload.get('apiVersion')}"
    )

    providers = payload.get("providers")
    assert isinstance(providers, list) and providers, (
        f"{yaml_path.name}: 'providers' must be a non-empty list"
    )
    assert len(providers) == 1, (
        f"{yaml_path.name}: expected exactly one provider, got {len(providers)}. "
        "Multiple providers would split the dashboards across folders and break "
        "the hard-coded /d/pod-stylist/<dashboard-uid> drill-down URLs."
    )

    provider = providers[0]
    assert provider.get("name") == "pod-stylist", (
        f"{yaml_path.name}: provider.name must be 'pod-stylist', got {provider.get('name')}"
    )
    assert provider.get("folder") == "POD Stylist", (
        f"{yaml_path.name}: provider.folder must be 'POD Stylist' (D18.2), "
        f"got {provider.get('folder')!r}"
    )
    assert provider.get("folderUid") == "pod-stylist", (
        f"{yaml_path.name}: provider.folderUid must be 'pod-stylist' (D18.2), "
        f"got {provider.get('folderUid')!r}"
    )
    assert provider.get("type") == "file", (
        f"{yaml_path.name}: provider.type must be 'file' (Grafana file provider), "
        f"got {provider.get('type')!r}"
    )

    options = provider.get("options") or {}
    assert options.get("path") == "/etc/grafana/provisioning/dashboards", (
        f"{yaml_path.name}: provider.options.path must match the in-container "
        f"mount path, got {options.get('path')!r}"
    )


def test_infrastructure_dashboard_uses_real_qdrant_metric() -> None:
    """Infrastructure I11 panel must query the real Qdrant 1.x exporter metric.

    Guards D18.11 / history/18_0_1_DEPLOYMENT_FIXES.md.  The
    Qdrant Prometheus exporter emits ``collection_vectors`` (per-
    collection, per-vector-type gauge) and ``collections_vector_total``
    (aggregate).  It does NOT emit ``qdrant_collection_vector_count``
    — that was a guess from the docs that returned no data on the
    live stack.  A future hand-edit that reverts the panel to the
    wrong name should fail here before the operator notices the
    empty graph.
    """
    infra_path = DASHBOARDS_DIR / "infrastructure.json"
    assert infra_path.exists(), f"Infrastructure dashboard JSON not found at {infra_path}"

    payload = json.loads(infra_path.read_text(encoding="utf-8"))
    panels = payload.get("panels", [])
    qdrant_panels = [
        p
        for p in panels
        if isinstance(p, dict) and p.get("title") == "Qdrant collection vector count"
    ]
    assert len(qdrant_panels) == 1, (
        f"Expected exactly one panel titled 'Qdrant collection vector count' "
        f"in infrastructure.json, found {len(qdrant_panels)}"
    )

    qdrant_panel = qdrant_panels[0]
    targets = qdrant_panel.get("targets", [])
    assert targets, "Qdrant panel must have at least one target"

    exprs = [t.get("expr", "") for t in targets if isinstance(t, dict)]
    joined = " | ".join(exprs)

    # The bad metric name must not appear anywhere in the panel exprs.
    assert "qdrant_collection_vector_count" not in joined, (
        "Infrastructure I11 panel still references the non-existent metric "
        "`qdrant_collection_vector_count`. The real Qdrant 1.x exporter metric "
        "is `collection_vectors`. See history/18_0_1_DEPLOYMENT_FIXES.md D18.11."
    )

    # The real metric name must appear in at least one target.
    assert "collection_vectors" in joined, (
        f"Infrastructure I11 panel does not reference `collection_vectors`. Found exprs: {exprs}"
    )


def test_infrastructure_dashboard_uses_real_rabbitmq_metric_names() -> None:
    """Infrastructure I12 panel must use the real RabbitMQ per-queue metric names.

    Guards D18.12 / D18.14 / history/18_0_1_DEPLOYMENT_FIXES.md.  Three
    failure modes this test catches:

    1. ``rabbitmq_queue_messages_unacknowledged`` (with extra `e`) is
       NOT the real metric name.  The actual exporter emits
       ``rabbitmq_queue_messages_unacked`` (one `e`).  The wrong name
       was the original Phase 18 typo — the panel returned empty until
       D18.14 fixed it.
    2. The per-queue filter ``queue="webhook"`` must be present, since
       D18.12 made the ``queue`` label available via
       ``prometheus.return_per_object_metrics = true`` in rabbitmq.conf.
    3. The ``rabbitmq_queue_messages_ready`` and
       ``rabbitmq_queue_messages_unacked`` queries must coexist on the
       same panel (one for `ready`, one for `unacked`).
    """
    infra_path = DASHBOARDS_DIR / "infrastructure.json"
    payload = json.loads(infra_path.read_text(encoding="utf-8"))
    panels = payload.get("panels", [])
    queue_panels = [
        p
        for p in panels
        if isinstance(p, dict) and p.get("title") == "RabbitMQ webhook queue depth"
    ]
    assert len(queue_panels) == 1, (
        f"Expected exactly one panel titled 'RabbitMQ webhook queue depth' "
        f"in infrastructure.json, found {len(queue_panels)}"
    )

    queue_panel = queue_panels[0]
    targets = queue_panel.get("targets", [])
    assert len(targets) >= 2, (
        f"RabbitMQ queue depth panel must have at least 2 targets (ready + unacked), "
        f"got {len(targets)}"
    )

    exprs = [t.get("expr", "") for t in targets if isinstance(t, dict)]
    joined = " | ".join(exprs)

    # 1. The typo `unacknowledged` (with extra e) must not appear.
    assert "rabbitmq_queue_messages_unacknowledged" not in joined, (
        "Infrastructure I12 panel references the misspelled metric "
        "`rabbitmq_queue_messages_unacknowledged`. The real metric name is "
        "`rabbitmq_queue_messages_unacked` (one `e`). "
        "See history/18_0_1_DEPLOYMENT_FIXES.md D18.14."
    )

    # 2. The correct metric name must appear.
    assert "rabbitmq_queue_messages_unacked" in joined, (
        f"Infrastructure I12 panel does not reference the correct metric "
        f"`rabbitmq_queue_messages_unacked`. Found exprs: {exprs}"
    )

    # 3. The per-queue filter must be present (D18.12 — enabled by
    # `prometheus.return_per_object_metrics = true`).
    assert 'queue="webhook"' in joined, (
        f'Infrastructure I12 panel is missing the `queue="webhook"` filter. '
        f"Per-queue labels require `prometheus.return_per_object_metrics = true` "
        f"in docker/rabbitmq/rabbitmq.conf. Found exprs: {exprs}"
    )

    # 4. The ready messages query must also be present.
    assert "rabbitmq_queue_messages_ready" in joined, (
        f"Infrastructure I12 panel is missing `rabbitmq_queue_messages_ready`. Found exprs: {exprs}"
    )


@pytest.mark.asyncio
async def test_grafana_api_lists_four_dashboards(
    grafana_client: httpx.AsyncClient,
) -> None:
    """Grafana's search API must return all four provisioned dashboards.

    Runtime smoke test — queries ``/api/search?folderUid=pod-stylist``
    and asserts the response contains exactly the four Phase 18
    dashboard UIDs.  Catches a typo in a JSON file that parses cleanly
    but fails Grafana's own loader (e.g. an invalid ``gridPos``).
    Skips cleanly when Grafana is offline.
    """
    response = await grafana_client.get(
        "/api/search",
        params={"folderUid": "pod-stylist", "type": "dash-db"},
    )
    assert response.status_code == 200, (
        f"Grafana /api/search returned {response.status_code}: {response.text}. "
        "Check that the grafana container is healthy and that "
        "docker/grafana/dashboards/default.yaml was mounted correctly."
    )

    results = response.json()
    assert isinstance(results, list), (
        f"Grafana /api/search returned a non-list payload: {type(results).__name__}"
    )

    observed_uids = {
        uid
        for item in results
        if isinstance(item, dict) and isinstance(uid := item.get("uid"), str)
    }
    missing = EXPECTED_DASHBOARD_UIDS - observed_uids
    assert not missing, (
        f"Grafana is missing Phase 18 dashboards {sorted(missing)}. "
        f"Observed UIDs: {sorted(observed_uids)}. "
        "Check docker/grafana/dashboards/default.yaml was mounted and "
        "Grafana's file provider ran the provisioning pass — search the "
        "Grafana container logs for 'Provisioning' messages."
    )
    extra = observed_uids - EXPECTED_DASHBOARD_UIDS
    assert not extra, (
        f"Grafana returned unexpected dashboards under folderUid=pod-stylist: "
        f"{sorted(extra)}. Phase 18 expects exactly the four canonical UIDs "
        f"{sorted(EXPECTED_DASHBOARD_UIDS)}."
    )
