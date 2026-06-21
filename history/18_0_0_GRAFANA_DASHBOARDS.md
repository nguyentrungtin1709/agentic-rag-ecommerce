# Grafana Dashboards (Non-AI Focus) — Phase 18

**Version**: 18.0.0
**Date**: 2026-06-21
**Status**: Active

## What

Ship the visualization layer of the observability rollout. Four
Grafana dashboards under a `POD Stylist` folder, all provisioned
from JSON via Grafana's file-based provider. No application code,
no new Python dependencies, no new environment variables, no new
exporters.

| Dashboard | Datasource mix | Panels |
|---|---|---|
| `System Overview` | Prometheus + Loki | HTTP rate, error rate, p50/p95/p99 latency, in-flight requests, FastAPI process CPU + memory, app log volume |
| `Infrastructure` | Prometheus | 5x `up` stats (one per scrape job), Postgres conns + tx/s, Valkey memory + hit rate, Qdrant vector count, RabbitMQ queue depth + consumers |
| `Logs Explorer` | Loki | 6 template variables (`correlation_id`, `thread_id`, `user_id`, `level`, `service`, `endpoint`) + 6 panels (logs by level, by service, errors list, warnings list, correlation detail, thread history) |
| `Business Metrics` | Loki | Chat activity, chat failures, image generation failures, SSE emit failures, thread lifecycle anomalies, ingestion events |

Provisioning: `docker/grafana/dashboards/default.yaml` (folder
`POD Stylist`, folder UID `pod-stylist`, four JSON files picked up
automatically by Grafana's file provider from the existing
`/etc/grafana/provisioning/dashboards` mount).

## Why

Observability Phases 15 (LangSmith tracing), 16 (Alloy log
pipeline) and 17 (Alloy metrics scraping) shipped 2026-06-20.
After those phases the system produces AI traces, structured logs
with `correlation_id` as a Loki label, and 5-source Prometheus
metrics — but Grafana ships with an empty dashboards folder.

Without dashboards, an operator has to remember and hand-type
PromQL / LogQL queries for every debugging session. Dashboards
are the small, cheap, last-mile investment that turns the
existing observability data into something operators actually
use on a daily basis. Per `07-OBSERVABILITY-DESIGN.md §2`,
Grafana is the canonical query surface for both logs and
metrics; the design doc also calls out alerting as a follow-up
*after* Phase 4 ships.

Phase 18 is the final phase of the observability rollout. After
this ships, the system has full traces + logs + metrics + a
visualization surface, all unified on `correlation_id` as the
cross-pillar join key.

## How

- Create four JSON dashboard files under `docker/grafana/dashboards/`:
  - `system-overview.json`
  - `infrastructure.json`
  - `logs-explorer.json`
  - `business-metrics.json`
- Create one provisioning file:
  - `docker/grafana/dashboards/default.yaml`
- Create four integration tests in
  `tests/integration/test_grafana_dashboards.py`:
  1. `test_all_four_dashboards_exist` — static file-presence
  2. `test_dashboards_are_valid_json` — JSON parses + has Grafana
     required top-level keys
  3. `test_provisioning_yaml_declares_pod_stylist_folder` — YAML
     parses + folder + folder UID match
  4. `test_grafana_api_lists_four_dashboards` — runtime check
     that Grafana actually loaded all four (skips cleanly when
     Grafana is offline)
- Update `docs/analysis/05-IMPLEMENTATION-PLAN.md` with one line
  marking Phase 18 status.
- No `src/` changes, no `pyproject.toml` changes, no `.env`
  changes.

## Key Decisions

### Carried forward from `07-OBSERVABILITY-IMPLEMENTATION-PLAN.md §2`

- **D1** — LangSmith owns AI traces; this phase deliberately
  does not duplicate AI observability in Grafana.
- **D2, D7** — Alloy owns metrics scraping; Phase 18 is read-only
  over the data Alloy produces. No `prometheus.scrape` blocks are
  touched.
- **D9** — `correlation_id` is a Loki label. Phase 18's Logs
  Explorer leverages this for O(1) per-request log retrieval.

### New tactical decisions (D18.x)

- **D18.1** — Business Metrics dashboard uses Loki-only. No custom
  Prometheus metrics are added to `src/app/observability/`. The
  "Chat turns (24h)" panel documents an approximation in its
  panel description. Rationale: honours `07 §7.6` ("Heavy custom
  application metrics out of scope"), keeps `src/` untouched for
  what is fundamentally a configuration-only phase. Confirmed with
  user 2026-06-21.
- **D18.2** — Folder name = `POD Stylist`, folder UID =
  `pod-stylist`. Matches `07 §7.2` suggestion and the FastAPI
  `title=...` in `src/app/main.py`. UID chosen lowercase-dash to
  match Grafana's URL convention (`/d/<folder-uid>/<dashboard-uid>`)
  and to enable clean drill-down URLs.
- **D18.3** — Drill-down enabled: System Overview and
  Infrastructure panels have outbound `links[]` entries pointing
  to Logs Explorer with `var-service` and (where appropriate)
  `var-level` chips pre-filled. Logs Explorer is the leaf — no
  outbound links. Confirmed with user 2026-06-21.
- **D18.4** — Provisioning uses
  `disableDeletion: false`, `allowUiUpdates: false`. Operators can
  delete a broken dashboard from the UI to recover; UI edits are
  ephemeral so the JSON in git is always canonical. Matches the
  rest of the project's "git is source of truth" stance.
- **D18.5** — "Chat turns (24h)" panel approximation is documented
  in the panel `description` field, not hidden. The current
  observable signal in Loki is `count_over_time({service="app",
  endpoint="stream_run"}[24h])`, which over-counts because each
  chat turn emits multiple log lines with that label. A future
  follow-up (D18.10) may add a single `logger.info("chat_run_completed", ...)`
  in `src/app/api/chat.py:270` to make the count exact.
- **D18.6** — Dashboard UIDs are hard-coded:
  `system-overview`, `infrastructure`, `logs-explorer`,
  `business-metrics`. Stable URLs for drill-down and for any
  external links from README / docs.
- **D18.7** — All dashboards use `schemaVersion: 38` (Grafana
  13.x current, matching `docker-compose.yml:202` image pin
  `grafana/grafana:13.0.2`).
- **D18.9** — System Overview panel S4 uses `process_open_fds`
  (file descriptor count) as a saturation indicator instead of
  `http_requests_inprogress`. Reason: `prometheus-fastapi-instrumentator==8.0.0`
  only emits the in-progress gauge when the caller passes
  `should_instrument_requests_inprogress=True` to
  `Instrumentator().instrument()`, which the current
  `src/app/main.py:165` does not (defaults to `False`). Phase 18 is
  config-only, so we substitute a metric the default process
  collector already exposes. The gauge thresholds (500/1000) are
  conservative for a single-worker uvicorn process; tune against
  `process_max_fds` in the field.
- **D18.8** — All dashboards carry `tags: ["pod-stylist"]` plus a
  category tag (`logs`, `infra`, `business`, `system`). Enables
  filter-by-tag on the Grafana home page.

## Impact

Files affected (8 total, no application code):

| Action | File | Approx. lines |
|---|---|---|
| Create | `docker/grafana/dashboards/system-overview.json` | ~250 |
| Create | `docker/grafana/dashboards/infrastructure.json` | ~280 |
| Create | `docker/grafana/dashboards/logs-explorer.json` | ~300 |
| Create | `docker/grafana/dashboards/business-metrics.json` | ~220 |
| Create | `docker/grafana/dashboards/default.yaml` | ~15 |
| Create | `tests/integration/test_grafana_dashboards.py` | ~200 |
| Modify | `docs/analysis/05-IMPLEMENTATION-PLAN.md` | 1 line |
| Create | `history/18_0_0_GRAFANA_DASHBOARDS.md` | this file |
| Create | `temp/phase-18-grafana-dashboards.md` | (after ship) |

No `src/` files touched. No `pyproject.toml` changes. No `.env` or
`.env.example` changes. No new Docker services. No new exporter
images.

## Unblocked follow-up

- **Alerting** — after Phase 18 ships, Alertmanager rules can be
  layered on top of the same queries that power the dashboards:
  `up{job="..."} == 0` for scrape health, `pg_stat_activity_count > N`
  for Postgres saturation, `rabbitmq_queue_messages_ready > M` for
  webhook backlog, `histogram_quantile(0.99, ...) > X` for latency
  SLOs. Recording as the next phase after this one (post-Phase 18).
- **Exact chat turn count** — D18.10 (deferred). One `logger.info`
  call in `src/app/api/chat.py:270` makes Business Metrics B1
  exact. Structlog contextvars already carry `correlation_id`,
  `thread_id`, `user_id` into that finally-block scope.
- **Container-level CPU/memory** — the System Overview process
  panel only shows the FastAPI app process. A `cAdvisor` sidecar
  (or per-service metrics) would close the gap for Qdrant /
  Postgres / Valkey / RabbitMQ.