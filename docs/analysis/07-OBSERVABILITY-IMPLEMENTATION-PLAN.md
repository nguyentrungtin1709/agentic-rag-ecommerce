# Observability Implementation Plan

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 1.1
- **Date**: 2026-06-17 (updated 2026-06-20)
- **Status**: Active — Phase 1 (LangSmith tracing) shipped 2026-06-20, Phase 2 (log pipeline) shipped 2026-06-20, Phase 3 (metrics expansion) shipped 2026-06-20, Phase 4 (Grafana dashboards) shipped 2026-06-21. Observability rollout complete: traces + logs + metrics + visualization, all unified on `correlation_id`.

> **Scope of this document** — the phased rollout plan for the observability
> stack. Locked decisions D1–D10, per-phase scope, files touched, task
> checklists, and definition of done. For the system architecture (what each
> component is and how data flows) see
> [06-OBSERVABILITY-DESIGN.md](06-OBSERVABILITY-DESIGN.md). For the working
> draft that produced this plan see
> [temp/observability-redesign.md](../../temp/observability-redesign.md).
> For project-wide implementation status see
> [05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md).

---

## 1. Background — Current State Audit

Audit performed 2026-06-17, one line per pillar:

| Pillar | Current State |
|---|---|
| AI tracing (LangChain / LangGraph / LlamaIndex) | `LANGSMITH_TRACING=false`. LangChain/LangGraph SDK has env-vars + deps but the flag is off. LlamaIndex has OpenInference instrumentation but no OTLP exporter, so per-call LlamaIndex spans go nowhere (revised 2026-06-18 — LlamaIndex path is now out of scope; only the LangGraph path is shipped). |
| Log pipeline | Promtail scrapes `/var/log/*log` (host syslog). Containers use `logging: driver: local` so app/celery stdout never reaches Promtail. Loki is empty of app logs. |
| Metrics | Prometheus scrapes only `app:8080/metrics` (15s interval). Qdrant, Postgres, Valkey, RabbitMQ, Celery are not scraped. |
| Dashboards | `docker/grafana/dashboards/` is empty. Datasources (Loki, Prometheus) are provisioned. |
| Collector | No Grafana Alloy. No OTel collector. No Tempo (intentional — LangSmith SaaS is the only trace backend). |

**AI/LLM traces target**:
- SDK path: `https://aws.api.smith.langchain.com`
- OTLP path: `https://aws.api.smith.langchain.com/otel/v1/traces`
- Project name: `agentic-rag-ecommerce`

---

## 2. Decisions Locked (D1–D10)

| ID | Decision | Locked |
|---|---|---|
| D1 | LangSmith is the canonical trace store for all AI/LLM/RAG calls (LangChain, LangGraph, LlamaIndex). All AI traces land in `smith.langchain.com`, project `agentic-rag-ecommerce`. | 2026-06-17 |
| D2 | Grafana Alloy is the single unified collector for BOTH logs AND metrics. Alloy replaces Promtail for log scraping AND owns all `/metrics` scraping via `prometheus.scrape` + `prometheus.remote_write`. Prometheus becomes a pure storage + query backend (no scrape jobs of its own). | 2026-06-17 |
| D3 | Phased delivery. 4 independent shippable phases, each with a clear visible result. | 2026-06-17 |
| D4 | Self-hosted Tempo / Jaeger is out of scope. LangSmith SaaS is the only trace backend. | 2026-06-17 |
| D5 | Container logging driver stays at default `json-file`. Alloy reads the Docker socket. Preserves `docker logs` for operator debugging. | 2026-06-17 |
| D6 | Single ingestion path to LangSmith — LangChain / LangGraph via `langsmith` SDK auto-trace, enabled by `LANGSMITH_TRACING=true` and the companion `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_ENDPOINT` env vars. (Originally locked 2026-06-17 as dual-path with an OpenInference + OTLP path for LlamaIndex; revised 2026-06-18 to single-path after evaluation — see Phase 1 §4 and `history/15_0_0_LANGSMITH_TRACING.md` revision history.) | 2026-06-17 (revised 2026-06-18) |
| D7 | Alloy scrapes all `/metrics` endpoints and `remote_write`s to Prometheus. Prometheus becomes a pure storage + query backend; scraping is owned by Alloy. | 2026-06-17 |
| D8 | Pin `grafana/alloy:v1.17.0`. | 2026-06-17 |
| D9 | Promote `correlation_id` to a Loki label via `loki.process` JSON extraction. Accepted cardinality risk; bounded by Loki `retention_period`. | 2026-06-17 |
| D10 | No `celery-exporter`. Celery health is observed via structured logs (Phase 2) + RabbitMQ queue depth (Phase 3). | 2026-06-17 |

---

## 3. Phase Overview

| # | Name | Solves | Visible Result | Depends On | Status |
|---|---|---|---|---|---|
| 1 | LangSmith tracing | AI/RAG traces not visible anywhere | One chat turn produces a `LangGraph` root run in `smith.langchain.com` covering the orchestrator + every LangGraph node, with `correlation_id` on the root run metadata | — | SHIPPED 2026-06-20 |
| 2 | Log pipeline (Alloy) | App/celery logs never reach Loki | Grafana Explore → Loki → `{service="app"}` returns the latest chat-turn JSON logs filterable by `correlation_id` | — (independent of Phase 1) | SHIPPED 2026-06-20 |
| 3 | Metrics expansion (Alloy-scraped) | Only the FastAPI app is scraped; Prometheus owns all scraping | Alloy owns scraping via `prometheus.scrape` and `prometheus.remote_write` to Prometheus. Qdrant, Postgres, Valkey, RabbitMQ metrics visible in Grafana Explore | — (independent of Phase 1, 2) | SHIPPED 2026-06-20 |
| 4 | Grafana dashboards (non-AI focus) | Dashboards folder empty | Grafana home shows 4 provisioned JSON dashboards: System Overview, Infrastructure, Logs Explorer, Business Metrics. AI/agent observability lives in LangSmith, not in Grafana. | 2, 3 (depends on log and metric data being queryable) | SHIPPED 2026-06-21 |

Each phase is **independent** — the system stays operational after any phase.

---

## 4. Phase 1 — LangSmith Tracing

### 4.1 Problem

All LLM/RAG traces are off. The user has chatted with the system but nothing shows up in `smith.langchain.com`. Debugging the orchestrator and the RAG pipeline is essentially blind.

### 4.2 Scope

Implements D6 (single-path ingestion to LangSmith via the `langsmith` SDK). Originally locked as dual-path (with an OpenInference + OTLP path for LlamaIndex); revised 2026-06-18 to single-path after evaluation.

**Environment variables** (add to `.env` and `.env.example`):

| Variable | Value | Purpose |
|---|---|---|
| `LANGSMITH_TRACING` | `true` | Enables `langsmith` SDK auto-trace for LangChain/LangGraph |
| `LANGSMITH_ENDPOINT` | `https://aws.api.smith.langchain.com` | SDK ingestion endpoint — NO `/otel` suffix |
| `LANGSMITH_API_KEY` | `lsv2_pt_*` | API key (already in `.env`) |
| `LANGSMITH_PROJECT` | `agentic-rag-ecommerce` | LangSmith project name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://aws.api.smith.langchain.com/otel/v1/traces` | (Reserved for future OTel spans) OTLP ingestion endpoint — WITH `/otel/v1/traces` suffix |
| `OTEL_EXPORTER_OTLP_HEADERS` | `x-api-key=${LANGSMITH_API_KEY},Langsmith-Project=agentic-rag-ecommerce` | (Reserved for future OTel spans) OTLP auth header + project routing |
| `DEPLOYMENT_ENVIRONMENT` | `development` | OTel `deployment.environment` resource attribute |
| `APP_VERSION` | `1.0.0` | OTel `service.version` resource attribute |

**Dependencies** (add to `pyproject.toml`):

- `opentelemetry-api`
- `opentelemetry-sdk`
- `opentelemetry-exporter-otlp-proto-http` (reserved for future OTel spans)
- `opentelemetry-instrumentation` (if not already present)

**Code changes** (extend `src/app/observability/tracing.py::configure_tracing`):

- Build a `TracerProvider` with resource attributes: `service.name=agentic-rag-ecommerce`, `service.version=1.0.0`, `deployment.environment=development`.
- Attach a `BatchSpanProcessor` wrapping `OTLPSpanExporter` (HTTP/protobuf) configured from env. **The provider is currently idle** — no library instrumentor is wired. Custom spans added in the future (`trace.get_tracer(__name__).start_as_current_span(...)`) will flow to LangSmith automatically.
- Write the `LANGSMITH_*` env vars to `os.environ` from validated settings. The `langsmith` SDK auto-trace path is enabled by env alone; no `LangChainTracer` callback is needed.
- The OpenInference LlamaIndex path (previously included) was removed in the 2026-06-18 revision. Re-enabling it is a one-line restore in `configure_tracing` plus restoring the two deleted tests.

**Verify** `correlation_id` lands on the root LangSmith run (auto-mapped from `metadata.correlation_id` by the SDK — already wired in `src/app/api/chat.py`).

### 4.3 Tasks

1. Add the 4 `LANGSMITH_*` env vars (plus the 4 OTel-side vars kept as future-use) to `.env` and `.env.example`. Flip `LANGSMITH_TRACING=true`.
2. Add OTel dependencies to `pyproject.toml` and run `uv sync`.
3. Extend `configure_tracing` to build the `TracerProvider` + OTLP exporter and write the `LANGSMITH_*` env vars.
4. Confirm in `src/app/main.py` lifespan that `configure_tracing` is called early enough (before any LLM call).
5. Write `tests/unit/observability/test_tracing.py` covering: TracerProvider construction, OTLP exporter endpoint + headers, resource attributes, `LANGSMITH_*` env var writes (12 cases).
6. Create decision record `history/15_0_0_LANGSMITH_TRACING.md` capturing D6 (single-path revision).
7. Add a phase log `temp/phase-15-langsmith-tracing.md` (per project workflow).
8. Verify end-to-end: run `docker-compose up -d`, post one chat turn, confirm a root `LangGraph` run appears in `smith.langchain.com` for project `agentic-rag-ecommerce` with `correlation_id` on metadata.

### 4.4 Test Plan

- **Unit**: `test_tracing.py` verifies provider config, exporter endpoint + headers, resource attributes, `LANGSMITH_*` env var writes, and `setenv`-vs-`setdefault` semantics.
- **Integration**: post a chat request, assert that a run appears in LangSmith with the expected `correlation_id` tag within 30 seconds.
- **Manual smoke**: open `smith.langchain.com`, expand the trace, verify orchestrator + every LangGraph node is visible with token usage and latency. (Per-call LlamaIndex spans like `OpenAIEmbedding.aget_text_embedding` are NOT produced by Phase 1 — they were removed in the revision.)

### 4.5 Definition of Done

- [ ] `LANGSMITH_TRACING=true` and the 3 companion `LANGSMITH_*` vars are present in `.env`. `.env.example` documents the same shape with safe defaults.
- [ ] The 4 OTel-side vars (`OTEL_EXPORTER_OTLP_*`, `DEPLOYMENT_ENVIRONMENT`, `APP_VERSION`) are present in both `.env` and `.env.example`.
- [ ] `pyproject.toml` has all 4 OTel dependencies; `uv sync` succeeds.
- [ ] `configure_tracing` builds the OTel `TracerProvider`, attaches the OTLP exporter, and writes `LANGSMITH_*` env vars.
- [ ] Unit tests pass with `pytest` (12 new observability cases; full suite at 81% coverage or above).
- [ ] Decision record `history/15_0_0_LANGSMITH_TRACING.md` exists with the 2026-06-18 revision captured.
- [ ] One end-to-end chat turn produces a `LangGraph` run in `smith.langchain.com` with `correlation_id` on the root run.

### 4.6 Out of Scope (Phase 1)

- Custom OTel span attributes (semantic conventions only — only `service.*` and `deployment.*` resource attributes).
- Self-hosted OTel collector sidecar (Alloy is not used for traces in this phase).
- Trace sampling / quotas.
- Sending traces to multiple backends (LangSmith is the only trace backend per D4).
- Per-call LlamaIndex spans (OpenInference + OTLP path). Reserved as a future hook — the OTel infrastructure is built and idle, ready to receive spans if the path is restored.

---

## 5. Phase 2 — Log Pipeline (Alloy Replaces Promtail)

### 5.1 Problem

Promtail scrapes host syslog only. App/celery stdout never reaches Loki. Grafana Explore → Loki returns nothing useful for the app.

### 5.2 Scope

Implements D2 (Alloy as log collector), D5 (default `json-file` driver), D8 (pin `v1.17.0`), D9 (`correlation_id` as Loki label).

**Docker Compose changes**:

- Add `grafana/alloy:v1.17.0` service.
- Mount `/var/run/docker.sock:/var/run/docker.sock:ro` and `/var/lib/docker/containers:/var/lib/docker/containers:ro`.
- Remove the `promtail` service and the `promtail_data` volume.
- Drop the `logging: driver: local` override from every service in `docker-compose.yml` — default `json-file` is in effect per D5.

**Alloy config** (`docker/alloy/config.alloy`, new file):

- `loki.source.docker` — scrape container stdout from Docker socket.
- Relabel rules for `service` and `compose_service`.
- `loki.process` stage to parse JSON logs and **promote `correlation_id` to a Loki label** per D9.
- `loki.write` to `http://loki:3100/loki/api/v1/push`.

**Loki config** (`docker/loki/local-config.yaml`, new file):

- Set `limits_config.retention_period` to `168h` (7 days) to bound storage growth from the new `correlation_id` label.
- Single-process mode is fine; default config shipped with `grafana/loki:3.7.2` is the baseline.

**Files removed**:

- `docker/promtail/config.yaml` — delete.
- `docker/promtail/` directory — delete.

### 5.3 Tasks

1. Add `alloy` service to `docker-compose.yml` with volume mounts and `v1.17.0` pin.
2. Write `docker/alloy/config.alloy` with the full pipeline (`loki.source.docker` → `loki.process` → `loki.write`).
3. Write `docker/loki/local-config.yaml` with `retention_period: 168h`.
4. Remove `promtail` service + `promtail_data` volume from `docker-compose.yml`.
5. Drop `logging: driver: local` overrides from every service in `docker-compose.yml`.
6. Delete `docker/promtail/` directory and its contents.
7. Write `tests/integration/test_log_pipeline.py` — verify a log line emitted by the app appears in Loki within 5 seconds (asserted via Loki HTTP query API).
8. Create decision record `history/16_0_0_LOG_PIPELINE_ALLOY.md` capturing D5, D8, D9.
9. Add a phase log `temp/phase-16-log-pipeline.md`.
10. Verify end-to-end: run `docker-compose up -d`, post one chat turn, query Loki for `{service="app", correlation_id="<uuid>"}` and confirm every LangGraph node's log line is returned.

### 5.4 Test Plan

- **Integration**: `test_log_pipeline.py` — start the stack, trigger a request, query Loki over HTTP for `{service="app", correlation_id="<expected>"}`, assert non-empty result.
- **Manual smoke**: open Grafana → Explore → Loki → `{service="app"}` and confirm JSON log lines stream in real time. Filter by `correlation_id` (now a label) and confirm O(1) response time.

### 5.5 Definition of Done

- [ ] `alloy` container running, logs show scrape intervals firing.
- [ ] `promtail` container is gone, `promtail_data` volume is gone.
- [ ] No `logging: driver: local` overrides remain in `docker-compose.yml`.
- [ ] `docker/alloy/config.alloy` parses (`alloy fmt --check` passes).
- [ ] `docker/loki/local-config.yaml` is mounted; Loki starts cleanly.
- [ ] Integration test passes within 5 seconds.
- [ ] Decision record `history/16_0_0_LOG_PIPELINE_ALLOY.md` exists.

### 5.6 Out of Scope (Phase 2)

- Multi-tenant log routing.
- Log archival to S3 / long-term retention.
- Per-tenant RBAC on logs.
- Loki clustering / microservices mode.
- Cardinality limiting for the `correlation_id` label (accepted cost; revisit if Loki storage grows unexpectedly).

---

## 6. Phase 3 — Metrics Expansion (Alloy Owns Scraping)

### 6.1 Problem

Prometheus only scrapes `app:8080/metrics`. Postgres connections, Valkey memory, Qdrant vector counts, RabbitMQ queue depth, Celery worker health are all invisible. Per D2/D7, scrape ownership moves from Prometheus to Alloy.

### 6.2 Scope

Implements D2 (Alloy as metrics collector), D7 (Alloy owns all scraping), D10 (no `celery-exporter`).

**Alloy config additions** (extend `docker/alloy/config.alloy`):

- `prometheus.scrape` jobs for:
  - `app:8080/metrics` (FastAPI app — moved from Prometheus)
  - `qdrant:6333/metrics` (Qdrant native exporter, on by default in v1.18.1)
  - `rabbitmq:15692/metrics` (RabbitMQ Prometheus plugin; requires `rabbitmq_prometheus` plugin enabled)
  - `redis-exporter:9121/metrics` (new sidecar, Valkey-compatible)
  - `postgres-exporter:9187/metrics` (new sidecar)
- `prometheus.remote_write` to `http://prometheus:9090/api/v1/write`.

**Prometheus config** (`docker/prometheus/prometheus.yml`):

- Remove all scrape jobs.
- Add `remote_write: []` — incoming only. Prometheus is a pure storage + query backend.

**Sidecars added to `docker-compose.yml`**:

- `redis-exporter` (Valkey-compatible, `oliver006/redis_exporter`).
- `postgres-exporter` (`prometheuscommunity/postgres-exporter`).

**RabbitMQ config** (`docker/rabbitmq/enabled_plugins`, new file):

- `[rabbitmq_management,rabbitmq_prometheus]`.

**No `celery-exporter`** per D10. Celery observability is covered by:
- Structured JSON logs in Loki (Phase 2) — task started/succeeded/failed events.
- RabbitMQ queue depth (visible via the `rabbitmq` scrape job in this phase).

### 6.3 Tasks

1. Extend `docker/alloy/config.alloy` with `prometheus.scrape` + `prometheus.remote_write` blocks.
2. Update `docker/prometheus/prometheus.yml` to remove all scrape jobs and accept remote_write.
3. Add `redis-exporter` and `postgres-exporter` services to `docker-compose.yml`.
4. Create `docker/rabbitmq/enabled_plugins` with `rabbitmq_management` + `rabbitmq_prometheus`.
5. Write `tests/integration/test_metrics_pipeline.py` — verify a Qdrant metric is visible in Prometheus within 30 seconds (asserted via Prometheus HTTP query API).
6. Create decision record `history/17_0_0_METRICS_EXPANSION.md` capturing D2, D7, D10.
7. Add a phase log `temp/phase-17-metrics-expansion.md`.
8. Verify end-to-end: confirm in Prometheus UI → Status → Targets that all 5 scrape jobs are `up`. Query `qdrant_collection_info{...}`, `pg_stat_activity_count`, `redis_memory_used_bytes`, `rabbitmq_queue_messages_ready{queue="webhook"}` — all return values.

### 6.4 Test Plan

- **Integration**: `test_metrics_pipeline.py` — start the stack, wait one scrape interval, query Prometheus over HTTP for a known metric (e.g. `qdrant_collections`), assert non-empty result.
- **Manual smoke**: open Prometheus → Status → Targets, verify all 5 jobs are `up`. Open Grafana → Explore → Prometheus, run `up{job="qdrant"}` — expect `1`.

### 6.5 Definition of Done

- [x] `prometheus.scrape` and `prometheus.remote_write` blocks in Alloy config.
- [x] `prometheus.yml` has zero `scrape_configs` entries.
- [x] `redis-exporter` and `postgres-exporter` containers running, metrics exposed.
- [x] `enabled_plugins` file mounted; RabbitMQ exposes `:15692/metrics`.
- [x] All 5 targets are `up` in Prometheus UI.
- [x] Integration test passes within 30 seconds.
- [x] Decision record `history/17_0_0_METRICS_EXPANSION.md` exists.

**Phase 3 shipped 2026-06-20** — see
[`temp/phase-17-metrics-expansion.md`](../../temp/phase-17-metrics-expansion.md)
for the full phase log (verification table, decisions, operational
notes, follow-ups). The Grafana datasource provisioning for
Prometheus is the one prerequisite for Phase 4 — flagged in the
phase log under "Follow-ups".

### 6.6 Out of Scope (Phase 3)

- Prometheus HA / sharding.
- Remote write to long-term storage (Thanos / Mimir).
- Custom application metrics (LLM token counter, retrieval hit rate) — non-trivial to extract from spans; not on the critical path.
- `celery-exporter` (out of scope per D10).

---

## 7. Phase 4 — Grafana Dashboards (Non-AI Focus)

### 7.1 Problem

`docker/grafana/dashboards/` is empty. Grafana home page is blank after first login. AI/LLM observability lives in LangSmith per D1; Grafana focuses on infrastructure, system health, logs, and business metrics.

### 7.2 Scope

4 separate JSON dashboards in `docker/grafana/dashboards/`:

| # | Dashboard | Contents |
|---|---|---|
| 1 | **System Overview** | HTTP request rate, error rate (4xx/5xx), p50/p95/p99 latency, active threads, recent log count, FastAPI process metrics |
| 2 | **Infrastructure** | Postgres connections + tx/s, Valkey memory + hit rate, Qdrant vectors + search QPS, RabbitMQ queue depth + consumers — pulled from Phase 3 Alloy-scraped metrics |
| 3 | **Logs Explorer** | Preset filter chips: `correlation_id`, `thread_id`, `user_id`, `level`, `service`. Uses the Loki label `correlation_id` from D9 for fast filtering |
| 4 | **Business Metrics** | Chat turn count per user, image generation count, SSE event distribution, thread lifecycle events |

**Provisioning**:

- `docker/grafana/dashboards/default.yaml` — provisioning config pointing at the JSON files.
- Folder structure in Grafana: `POD Stylist / System`, `POD Stylist / Infrastructure`, `POD Stylist / Logs`, `POD Stylist / Business`.
- Cross-dashboard links where natural (e.g., System Overview → Logs Explorer for the same time range with `correlation_id` pre-filled).

### 7.3 Tasks

1. Create `docker/grafana/dashboards/system-overview.json` (≥ 5 panels).
2. Create `docker/grafana/dashboards/infrastructure.json` (≥ 5 panels).
3. Create `docker/grafana/dashboards/logs-explorer.json` (≥ 5 panels).
4. Create `docker/grafana/dashboards/business-metrics.json` (≥ 5 panels).
5. Create `docker/grafana/dashboards/default.yaml` provisioning file.
6. Verify each dashboard renders by hand in Grafana after a few requests have hit the system.
7. Create decision record `history/18_0_0_GRAFANA_DASHBOARDS.md` capturing the dashboard structure.
8. Add a phase log `temp/phase-18-grafana-dashboards.md`.
9. Verify end-to-end: open Grafana home, confirm 4 tiles under `POD Stylist`. Drill into each dashboard and confirm panels render with live data.

### 7.4 Test Plan

- **Manual smoke**: each dashboard is loaded from JSON via provisioning; verify in the Grafana UI that all 4 tiles appear and that panels show live data after a few requests.
- **JSON validation**: `python -c "import json; json.load(open('<file>.json'))"` for each dashboard file.
- **Provisioning verification**: after `docker-compose up -d`, the Grafana container logs should show the provisioning loader successfully importing each dashboard.

### 7.5 Definition of Done

- [ ] 4 dashboard JSON files in `docker/grafana/dashboards/`, each with ≥ 5 panels.
- [ ] `default.yaml` provisioning file present and points at all 4 files.
- [ ] Grafana home shows 4 tiles under `POD Stylist` folder.
- [ ] All panels render with live data after a few requests.
- [ ] Logs Explorer can filter by `correlation_id` as a Loki label and return per-request log trails.
- [ ] Decision record `history/18_0_0_GRAFANA_DASHBOARDS.md` exists.

### 7.6 Out of Scope (Phase 4)

- Alerting rules.
- SLO tracking.
- Dashboard export/import workflow.
- LangSmith data source plugin for Grafana (use the LangSmith UI directly for AI inspection).
- Cost / spend dashboards (no LLM cost data in Prometheus by design).
- Heavy custom application metrics — we rely on `prometheus_fastapi_instrumentator` for HTTP metrics and on Loki logs for everything else. A minimal `Gauge` (e.g. `generated_images` row count) may be added if the Business Metrics dashboard is too sparse.

---

## 8. Out of Scope (All Phases, Overall)

- Self-hosted Tempo or Jaeger (D4).
- Distributed tracing across multiple FastAPI replicas.
- Log shipping to S3 / Glacier for long-term retention.
- Cost tracking dashboard (LLM spend per day / per user).
- PII redaction in Loki / log shipping pipeline (we consume what the app produces; PII redaction is the app's responsibility).
- Grafana SSO / multi-tenant.
- Alertmanager / PagerDuty integration (deferred to a follow-up phase).

---

## 9. Phase Ordering Rationale

1. **Phase 1 first** — the highest-value, highest-urgency gap. Without traces, debugging the orchestrator and RAG behavior is blind. The user explicitly asked about LangSmith.
2. **Phase 2 second** — once traces are in, log correlation by `correlation_id` is the natural next step. An operator pivots from a slow span in LangSmith to the full structured log trail in Loki.
3. **Phase 3 third** — metrics expansion is useful but not blocking any debugging flow that Phases 1 and 2 unlock.
4. **Phase 4 last** — dashboards are a visualization layer over the data that must exist first. Doing them last means we build against the real shape of the data and can iterate freely.

Each phase has a **clear definition of done** — a user can run the verification step in the phase's "Definition of Done" section and confirm the phase shipped.

---

## 10. Open Questions (Overall)

- (RESOLVED) LangSmith endpoint URLs — dual endpoint approach (SDK + OTLP) confirmed.
- (RESOLVED) LangSmith API key supports both SDK and OTLP ingestion with default permissions.
- (RESOLVED) Pin Grafana Alloy to `v1.17.0` per D8.
- (RESOLVED) `correlation_id` is a Loki label per D9.
- (RESOLVED) `celery-exporter` is out of scope per D10.
- (OPEN, deferred) Alerting (Alertmanager / PagerDuty) is out of scope; revisit after Phase 4 ships.

---

## 11. Next Step

Phases 1, 2, 3 are **shipped** (2026-06-20). Phase 4 (Grafana
dashboards) is the only remaining work in the observability rollout.
Per the project `CLAUDE.md` workflow:

1. Create a decision record `history/18_0_0_GRAFANA_DASHBOARDS.md`
   before coding Phase 4 (per the decision-record-first rule).
2. Add a Prometheus datasource to `docker/grafana/datasources/` (one-line
   YAML) so the Infrastructure dashboard's panels resolve. Flagged as
   a Phase 4 prerequisite in `temp/phase-17-metrics-expansion.md`.
3. Build the 4 JSON dashboards per §7.2: System Overview,
   Infrastructure, Logs Explorer, Business Metrics. Provisioning file
   at `docker/grafana/dashboards/default.yaml`.
4. Append a phase log to `temp/phase-18-grafana-dashboards.md` and ship.
5. User performs all git commits (per project `no-autonomous-commits`
   rule); Claude does not commit, push, or tag.

---

## 12. References

| Document | Purpose |
|---|---|
| [06-OBSERVABILITY-DESIGN.md](06-OBSERVABILITY-DESIGN.md) | Architecture — what each component is, how data flows |
| [05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md) | Master plan, project-wide status |
| [04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md) | Agent topology — names the nodes that emit traces |
| [03-PROJECT-SCAFFOLD.md](03-PROJECT-SCAFFOLD.md) | Directory layout, Docker Compose, environment variables |
| [00-SALEOR-APP-WEBHOOK-SETUP.md](00-SALEOR-APP-WEBHOOK-SETUP.md) | Saleor webhook configuration |
| [temp/observability-redesign.md](../../temp/observability-redesign.md) | Working draft that produced this plan |
| LangSmith docs | External — OpenTelemetry endpoint, attribute mapping |
| Grafana Alloy docs | External — `loki.source.docker`, `prometheus.scrape` reference |
