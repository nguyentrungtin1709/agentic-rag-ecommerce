# Metrics Expansion (Alloy owns scraping) — Phase 17

**Version**: 17.0.0
**Date**: 2026-06-20
**Status**: Active

## What

Move metrics scraping from Prometheus to Grafana Alloy so all 5
infrastructure targets are visible. Drop every `scrape_configs` entry
in `docker/prometheus/prometheus.yml`; Prometheus becomes a pure storage
+ query backend that accepts only `remote_write` from Alloy. Add two
new exporter sidecars (`redis-exporter`, `postgres-exporter`) and enable
the `rabbitmq_prometheus` plugin via a new `enabled_plugins` file
mount.

The 5 targets (D7):

| # | Target | Endpoint | Source |
|---|---|---|---|
| 1 | FastAPI app | `app:8080/metrics` | existing `prometheus-fastapi-instrumentator` (Phase 1) |
| 2 | Qdrant | `qdrant:6333/metrics` | Qdrant native exporter, on by default in v1.18.1 |
| 3 | RabbitMQ | `rabbitmq:15692/metrics` | new `rabbitmq_prometheus` plugin mount |
| 4 | Valkey | `redis-exporter:9121/metrics` | new sidecar `oliver006/redis_exporter:v1.67.0` |
| 5 | Postgres | `postgres-exporter:9187/metrics` | new sidecar `quay.io/prometheuscommunity/postgres-exporter:v0.15.0` |

## Why

Phases 15 (LangSmith) and 16 (Alloy logs) shipped 2026-06-18 and
2026-06-20. The observability stack now covers AI traces (LangSmith)
and structured logs (Loki), but metrics are still the gap: Prometheus
scrapes **only** `app:8080/metrics`. Postgres connections, Valkey memory
pressure, Qdrant vector counts, and RabbitMQ queue depth are all
invisible. The Phase 17 audit (2026-06-17) confirmed that Prometheus'
single scrape job misses every other running service.

Per D2 (locked 2026-06-17), Alloy is the single collector for both
logs and metrics. Phase 16 shipped the log half; Phase 17 ships the
metric half. Per D7, scrape ownership moves to Alloy and Prometheus
becomes pure storage + query.

The five targets map directly to the four infrastructure health goals
in `docs/analysis/06-OBSERVABILITY-DESIGN.md` §1.1: Qdrant vector count
dropping, Valkey memory pressure, RabbitMQ queue depth growing, and
general HTTP latency (already covered by target 1 since Phase 1).
Postgres connection saturation is the new dimension.

## How

- Rewrite `docker/prometheus/prometheus.yml` to drop every `scrape_configs`
  block. Keep `global.scrape_interval: 15s` and
  `evaluation_interval: 15s` (D17.9). The file becomes 4 lines of YAML
  + a comment block explaining the empty `scrape_configs`.
- Extend `docker/alloy/config.alloy` with five `prometheus.scrape`
  blocks (one per target) and one `prometheus.remote_write` block
  pointing at `http://prometheus:9090/api/v1/write`. The existing
  `loki.*` blocks are untouched.
- Add `docker/rabbitmq/enabled_plugins` with content
  `[rabbitmq_management,rabbitmq_prometheus,].`. Mount it into the
  `rabbitmq:` service as `/etc/rabbitmq/enabled_plugins:ro`. Add
  `15692:15692` to the `rabbitmq:` `ports:` block.
- Add `redis-exporter` and `postgres-exporter` services to
  `docker-compose.yml`. Both are default-on, on the `agentic-rag`
  network, with `restart: always` and `depends_on` on their respective
  upstream service. `redis-exporter` uses only `REDIS_ADDR`; the
  Valkey container has no `--requirepass` so no password is needed
  (D17.4). `postgres-exporter` reads `POSTGRES_*` from `.env` via the
  same `${...:-default}` pattern the `postgres` service already uses
  (D17.3).
- Add `prometheus_ready` fixture to `tests/integration/conftest.py`
  mirroring the existing `loki_ready` pattern. Add
  `PROMETHEUS_TEST_URL` to the docstring.
- Add `tests/integration/test_metrics_pipeline.py` with three tests
  (D17.8): static post-condition that `scrape_configs` is empty,
  Qdrant round-trip, all-5-targets `up` check.
- Decision record (this file) and phase log
  `temp/phase-17-metrics-expansion.md` ship with the implementation.

## Key Decisions

### Carried forward from `07-OBSERVABILITY-IMPLEMENTATION-PLAN.md` §2

- **D2** — Grafana Alloy is the single unified collector for BOTH logs
  AND metrics. Phase 17 ships the metrics half on top of the Phase 16
  log half.
- **D7** — Alloy scrapes all `/metrics` endpoints and `remote_write`s
  to Prometheus. Prometheus becomes pure storage + query; zero
  `scrape_configs`.
- **D10** — No `celery-exporter`. Celery health is observed via
  structured logs in Loki (Phase 16) + RabbitMQ queue depth in
  Prometheus (this phase). Decision locked 2026-06-17; this phase
  honours it.

### New tactical decisions (D17.x)

- **D17.1** — Pin `oliver006/redis_exporter:v1.67.0` exactly. Chosen
  over `latest` for reproducibility; matches the D8 reasoning that
  pinned Alloy to v1.17.0.
- **D17.2** — Pin `quay.io/prometheuscommunity/postgres-exporter:v0.15.0`
  exactly. Same reasoning as D17.1.
- **D17.3** — postgres-exporter reads `POSTGRES_USER`, `POSTGRES_PASSWORD`,
  `POSTGRES_DB` from `.env` via the same `${POSTGRES_USER:-app}` /
  `${POSTGRES_PASSWORD:-changeme}` / `${POSTGRES_DB:-app}` pattern the
  `postgres` service already uses. Chosen over a separate
  `POSTGRES_EXPORTER_PASSWORD` env var so credential rotation is a
  single-source change. No new env vars are added.
- **D17.4** — redis-exporter talks to Valkey without a password. The
  current `valkey:9.1.0-alpine` container has no `--requirepass`, so
  only `REDIS_ADDR=redis://valkey:6379` is passed. Chosen over adding
  a Valkey password because the dev stack has always been auth-free;
  the value of adding a password just to give the exporter something
  to send is zero.
- **D17.5** — Qdrant metrics endpoint is on `qdrant:6333/metrics` per
  `07` §6.2. Chosen over the alternative `6334` (used in older
  Qdrant versions) because v1.18.x exposes `/metrics` on the same
  port as the REST API. The plan §6.2 already commits to 6333; the
  integration test will verify at run time. If v1.18.1 has reverted to
  a dedicated port, this is a one-line alloy target change recorded
  as D17.5'.
- **D17.6** — `docker/rabbitmq/enabled_plugins` content is the Erlang
  list `[rabbitmq_management,rabbitmq_prometheus,].`. Both plugins are
  listed explicitly even though the `rabbitmq:4.3.1-management-alpine`
  image already includes `rabbitmq_management` by default. Chosen over
  omitting `rabbitmq_management` from the file so a future image swap
  to the non-management variant does not silently break the metrics
  plugin (which depends on `management_agent` being present).
- **D17.7** — Prometheus image tag stays at `v3.4.0`; CLI flags
  `--storage.tsdb.retention.time=15d` and `--web.enable-lifecycle`
  are kept. Chosen over bumping the image or changing retention
  because the existing dev stack has stable behaviour at v3.4.0 and
  the plan does not call for a Prometheus upgrade.
- **D17.8** — Integration test scope is 3 tests, matching the Phase
  16 test trio. Chosen over 1 minimal test (insufficient coverage of
  the static + 5-target contracts) and over 6 comprehensive tests
  (each target tested individually is redundant — the
  `all_five_targets_are_up` test already proves all 5 are reachable).
  The three tests are: (a) static post-condition on
  `prometheus.yml`, (b) Qdrant metric round-trip, (c) all-5-targets
  `up` check.
- **D17.9** — Alloy scrape interval is `15s` for all 5 targets.
  Chosen to match the old Prometheus interval (the `global.scrape_interval`
  in the previous `prometheus.yml`). No deviation from existing
  cadence keeps the on-call experience consistent and the Prometheus
  series count predictable.

## Impact

Files affected (8 total, ~440 lines of diff):

| Action | File |
|---|---|
| Modify | `docker/prometheus/prometheus.yml` |
| Modify | `docker/alloy/config.alloy` |
| Modify | `docker-compose.yml` |
| Create | `docker/rabbitmq/enabled_plugins` |
| Modify | `tests/integration/conftest.py` |
| Create | `tests/integration/test_metrics_pipeline.py` |
| Create | `history/17_0_0_METRICS_EXPANSION.md` (this file) |
| Create | `temp/phase-17-metrics-expansion.md` (after ship) |

No application code touched. No `.env` or `.env.example` changes
(D17.3 reuses existing `POSTGRES_*` vars; D17.4 needs no new var).
No breaking changes to the API or the agent graph.

The only manual operator action is `docker compose restart rabbitmq`
after the new `enabled_plugins` mount lands, so the plugin is loaded
on next start. This is a one-time migration step and is documented
in the phase log.

## Unblocked follow-up

- Phase 18 (Grafana dashboards) can now build the **Infrastructure**
  dashboard with 5 panels (Postgres conns, Valkey memory, Qdrant
  vectors, RabbitMQ queue depth, app HTTP rate). Each panel is a
  single PromQL query against a known metric.
- Operators can now alert on infrastructure thresholds (Postgres
  connection saturation, Valkey memory, Qdrant collection drift)
  without instrumenting the application. Alerting itself is out of
  scope for Phase 17 and is a follow-up phase.
