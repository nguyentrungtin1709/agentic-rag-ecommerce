# Phase 18 Post-Deployment Fixes

**Version**: 18.0.1
**Date**: 2026-06-21
**Status**: Active
**Parent**: `18_0_0_GRAFANA_DASHBOARDS.md`

## What

Three post-deployment fixes discovered when the Phase 18 dashboards
were opened against a live stack:

1. **Infrastructure dashboard, Qdrant panel (I11)** — query
   `qdrant_collection_vector_count` referenced a metric that does not
   exist on the Qdrant 1.x exporter. The real metric is
   `collection_vectors` (per-collection, per-vector) plus
   `collections_vector_total` (aggregate). Query is rewritten to
   `sum by (collection) (collection_vectors)`.

2. **Infrastructure dashboard, RabbitMQ panels (I12, I13)** — the
   filters `queue="webhook"` returned empty because
   `rabbitmq_prometheus` 4.x exports aggregate metrics in totals mode
   by default (no `queue` label). Root cause fix: enable
   `prometheus.return_per_object_metrics = true` in
   `docker/rabbitmq/rabbitmq.conf` and restart the RabbitMQ container.
   The flag is RabbitMQ-internal and is the documented way to expose
   per-object labels (queue, exchange, vhost) on the `/metrics`
   endpoint.

   **Additional finding (D18.14)**: After D18.12 enabled the per-object
   labels, panel I12 still showed empty for the `unacked` series.  The
   Phase 18 query used `rabbitmq_queue_messages_unacknowledged` (with
   an extra `e`) which is not the actual metric name.  The real
   exporter emits `rabbitmq_queue_messages_unacked` (one `e`).  This
   was a pre-existing typo from the original Phase 18 panel design
   that only became visible once the `queue` label started returning
   data.

3. **Logs Explorer — external services leaking in** — Alloy's
   `discovery.docker "all_containers"` block picks up every container
   on the Docker host, including containers from a sibling Compose
   project (`saleor-platform`). Loki now sees `jaeger` and `mailpit`
   as `service` labels. Root cause fix: filter discovery to
   containers that carry the
   `com.docker.compose.project=agentic-rag-ecommerce` label. One
   `relabel` block, no other Alloy changes.

## Why

These were surfaced by the user on 2026-06-21 after looking at the
Grafana UI against the running stack. All three are first-day
discoveries, not latent issues:

- I11's metric name was guessed from Qdrant's docs, not verified
  against the live exporter. The exporter's actual metric naming is
  different.
- I12 / I13 are an opt-in feature of the RabbitMQ Prometheus plugin
  that the original Phase 17 wiring did not enable. It is off by
  default in 4.x.
- The Alloy discovery block was written to be project-agnostic in
  Phase 16 (D16.3) because at the time the Docker host only ran the
  `agentic-rag-ecommerce` compose project. A second compose project
  (`saleor-platform`) was added later and the discovery block was
  not updated.

## How

- `history/` first: this file, plus an addendum at the end of
  `18_0_0_GRAFANA_DASHBOARDS.md` noting the issue → fix trail.
- `docker/rabbitmq/rabbitmq.conf`: add one line below the existing
  `management_metrics_collection` block.
- `docker/alloy/config.alloy`: add a `relabel` filter step before
  the existing `discovery.relabel "alloy_logs"`. Targets that lack
  the `agentic-rag-ecommerce` compose project label are dropped.
- `docker/grafana/dashboards/infrastructure.json`: rewrite panel I11
  expression.
- Restart order: `docker restart agentic-rag-rabbitmq` (config-file
  change) then `docker restart agentic-rag-alloy` (config-file
  change). Grafana dashboard reloads via the file provider
  automatically; no restart required.
- `tests/integration/test_grafana_dashboards.py`: add a 5th test
  asserting that the Qdrant panel I11 expression no longer contains
  the nonexistent metric name. Catches regression if the panel is
  hand-edited back to the old name.

## Key Decisions

### D18.11 — Qdrant metric rewrite (I11)

- **Old**: `qdrant_collection_vector_count` (does not exist)
- **New**: `sum by (collection) (collection_vectors)`
- **Rationale**: `collection_vectors` is the per-collection,
  per-vector-type gauge that the Qdrant 1.x Prometheus exporter emits.
  Summing it groups dense + sparse vectors under the same
  `collection` label, which matches the panel title "Qdrant
  collection vector count" and gives one line per collection.
- **Alternatives considered**:
  - `collections_vector_total` (the aggregate gauge) — chosen
    against because it would be a single line with no per-collection
    breakdown.
  - `sum by (collection, vector) (collection_vectors)` — rejected
    because the panel's gridPos width (w=12) is too narrow for two
    series per collection to be readable, and the panel description
    talks about collection-level regression, not vector-type
    regression.

### D18.12 — RabbitMQ per-object metrics (I12, I13)

- **Chosen fix**: add `prometheus.return_per_object_metrics = true`
  to `docker/rabbitmq/rabbitmq.conf` and restart RabbitMQ.
- **Why not the alternative (Alloy `metrics_path = /metrics/per-object`)**:
  That would let us avoid touching the RabbitMQ config but it would
  also break the standard `/metrics` scrape contract. The Phase 18
  design uses `/metrics` end-to-end, and switching one of the five
  jobs to a non-standard endpoint adds operational surface area for
  no benefit on a 6-queue system. The flag is RabbitMQ-internal,
  well-documented, and the response size on a 6-queue broker is
  measured in single-digit KB per scrape (15s interval) — irrelevant.
- **Restart impact**: RabbitMQ restart costs ~5-10s of broker
  downtime. All connections drop, the Celery worker reconnects
  automatically, and no in-flight messages are lost (durable queues).

### D18.14 — Unacked metric name typo (I12)

- **Old**: `rabbitmq_queue_messages_unacknowledged{queue="webhook"}`
  (typo: extra `e`)
- **New**: `rabbitmq_queue_messages_unacked{queue="webhook"}` (one `e`)
- **Rationale**: confirmed by `curl http://localhost:15692/metrics |
  grep '^rabbitmq_queue_messages_unacked'` — the exporter emits
  `unacked`, not `unacknowledged`.  Both spellings are sometimes
  seen in older RabbitMQ docs; the 4.x Prometheus plugin uses
  `unacked`.
- **Why the typo survived Phase 18**: the `queue` label was missing
  in totals mode (D18.12), so the query
  `rabbitmq_queue_messages_unacknowledged{queue="webhook"}` returned
  empty even though the metric itself existed in aggregate form.  An
  empty panel looks the same as a typo to a casual operator, so the
  bug was not caught during initial verification.
- **Regression test**: a new test
  `test_infrastructure_dashboard_uses_real_rabbitmq_metric_names`
  asserts the panel contains the correct `unacked` spelling, the
  `queue="webhook"` filter, and both the `ready` and `unacked`
  targets.

### D18.13 — Compose-project filter in Alloy (Logs Explorer)

- **Chosen fix**: add a `discovery.relabel "compose_filter"` block
  that drops any target whose
  `__meta_docker_container_label_com_docker_compose_project` label
  is not `agentic-rag-ecommerce`.
- **Why not the alternative (filter at dashboard level via explicit
  service list)**: that would be Phase 18's responsibility, but every
  time a new service is added to `docker-compose.yml` the dashboard
  would need a corresponding edit. The discovery-level filter is a
  one-line config change that applies to logs, metrics, and any
  future Alloy pipeline; it is the correct layer for the fix.
- **Operational impact**: container discovery goes from
  "all containers on the host" to "all containers in the
  `agentic-rag-ecommerce` compose project". When new compose
  services are added, they appear in Loki / Prometheus
  automatically, but containers from other projects no longer do.

## Impact

| Action | File | Approx. lines |
|---|---|---|
| Create | `history/18_0_1_DEPLOYMENT_FIXES.md` | this file |
| Modify | `docker/rabbitmq/rabbitmq.conf` | +4 lines |
| Modify | `docker/alloy/config.alloy` | +8 lines (one relabel block) |
| Modify | `docker/grafana/dashboards/infrastructure.json` | 4 lines (I11 expr + desc, I12 expr + desc) |
| Modify | `tests/integration/test_grafana_dashboards.py` | +2 tests (Qdrant + RabbitMQ) |
| Modify | `temp/phase-18-grafana-dashboards.md` | follow-up section |

No `src/` changes. No `pyproject.toml` changes. No `.env` changes.

## Verification (post-deploy)

- **V11a** (Qdrant): `curl -s -u admin:admin -G
  'http://localhost:3000/api/ds/query' --data-urlencode
  'ds=Prometheus' --data-urlencode
  'expr=sum by (collection) (collection_vectors)'` returns ≥1
  stream.
- **V12a** (RabbitMQ): `rabbitmq_queue_messages_ready{queue="webhook"}`
  is defined in `/metrics` post-restart.
- **V12b** (RabbitMQ typo fix): `rabbitmq_queue_messages_unacked`
  (one `e`) is the real metric name; the dashboard no longer
  references the misspelled `unacknowledged` form.
- **V13a** (Alloy filter): `curl -s http://loki:3100/loki/api/v1/label/service/values`
  still shows old `jaeger` and `mailpit` streams from before the
  filter was applied.  New log streams (last 5m) return empty for
  those services — the filter is working, the historical data will
  age out via Loki's retention.
- **V14a**: dashboard `infrastructure.json` is valid JSON and the
  I11 + I12 panel exprs match the new forms.
- **V15a**: `pytest tests/integration/test_grafana_dashboards.py`
  passes (6 tests now: 4 original + 2 added by 18.0.1).

## Unblocked

No new phases unblocked. The fixes are pre-conditions for
upcoming Phase 19 (Alerting) to produce meaningful alerts: a
`rabbitmq_queue_messages_ready{queue="webhook"} > N` alert only
works if the `queue` label exists.
