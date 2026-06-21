# Log Pipeline (Alloy replaces Promtail) — Phase 16

**Version**: 16.0.0
**Date**: 2026-06-20
**Status**: In Progress

## What
Replace the Promtail-based host-syslog scraper with a Grafana Alloy
service that reads every container's stdout via the Docker socket and
ships structured logs to Loki with `correlation_id` promoted to a Loki
label. Drop every `logging: driver: local` override so the default
`json-file` driver is in effect end-to-end.

## Why
Phase 15 (LangSmith tracing) shipped 2026-06-18 — one chat turn now
produces a root `LangGraph` run in LangSmith with `correlation_id` on
its metadata. The next debugging pivot is "from a slow span to the
full log trail", which requires `correlation_id` to be a Loki label,
not a JSON field scanned at query time. Audit 2026-06-17 confirmed
that Promtail only scrapes host syslog and all 11 services use
`driver: local`, so application logs never reach Loki today.

## How
- Add `grafana/alloy:v1.17.0` to `docker-compose.yml`; mount
  `/var/run/docker.sock` read-only.
- Author `docker/alloy/config.alloy` with the full pipeline:
  `discovery.docker` -> `discovery.relabel` (extract `service` from
  the compose label, `container` from the container name) ->
  `loki.source.docker` -> `loki.process` (parse structlog JSON,
  promote four fields to labels, keep `endpoint` as structured
  metadata) -> `loki.write`.
- Mount a custom `docker/loki/local-config.yaml` to set
  `limits_config.retention_period = 168h` and add a `loki_data`
  Docker volume so logs survive `docker compose restart`.
- Delete `docker/promtail/` and remove the `promtail` service block
  plus every `logging: driver: local` block from
  `docker-compose.yml`.
- Add `tests/integration/test_log_pipeline.py` with three tests:
  `service` relabel smoke, `correlation_id` round-trip as a Loki
  label, and a static post-condition that `promtail` is gone from
  `docker-compose.yml`.

## Key Decisions
- **D5 revision**: container logging driver is the default
  `json-file`. Action: drop every `logging: driver: local` block
  in `docker-compose.yml` (this is the audit-calls-out change,
  not a fresh decision; the locked decision already mandates
  default driver). Chosen over keeping `driver: local` because the
  whole point of Phase 16 is to make container stdout visible to
  Loki, and `local` driver writes nowhere Alloy can read.
- **D8**: pin `grafana/alloy:v1.17.0` exactly. Chosen over `latest`
  because the observability plan §2 explicitly requires the pin;
  reproducibility of the dev stack matters more than chasing
  upstream.
- **D9**: promote `correlation_id` (and `thread_id`, `user_id`,
  `level`) to Loki labels via `loki.process`. Chosen over keeping
  them as JSON fields because the whole point is O(1) per-request
  log retrieval; the cardinality cost (~200k series at 20 req/min
  over 168 h) is documented as accepted risk in the design doc.
- **D16.1** (new): mount custom `docker/loki/local-config.yaml`
  with `retention_period: 168h` + `allow_structured_metadata: true`.
  Chosen over CLI flag overrides so the config is reviewable in one
  place; `allow_structured_metadata` is required because Alloy
  pushes `endpoint` as structured metadata.
- **D16.2** (new): add `loki_data` + `alloy_data` Docker volumes.
  Chosen over ephemeral storage so logs survive `docker compose
  restart` — a few hundred MB of disk buys stable dev experience.
- **D16.3** (new): extract `service` from
  `__meta_docker_container_label_com_docker_compose_service` and
  `container` from `__meta_docker_container_name`. Chosen over
  parsing the container name because `docker compose` sets the
  service label automatically for every service.
- **D16.4** (new): promote `correlation_id`, `thread_id`, `user_id`,
  `level` to Loki labels; keep `endpoint` as structured metadata.
  Chosen over promoting `endpoint` because it is one-per-route and
  would inflate label cardinality; the design doc §5.2 lists it
  as a JSON field, this decision follows that.
- **D16.5** (new): integration test verifies the label round-trip
  without any new code on the chat endpoint — trigger a request,
  read the first `{service="app"}` log line in Loki, extract its
  `correlation_id` label, re-query with `{correlation_id="<uuid>"}`
  and confirm non-empty result within 5 s. Chosen over adding a
  response header because the existing chat handler contract stays
  unchanged; the test is robust to which endpoint emitted the log.

## Impact
Files affected:
- `docker-compose.yml` (edit: add `alloy`, edit `loki`, drop
  `promtail`, drop 14 `driver: local` blocks, add `loki_data` +
  `alloy_data` volumes)
- `docker/alloy/config.alloy` (new)
- `docker/loki/local-config.yaml` (new)
- `docker/promtail/` (delete)
- `tests/integration/conftest.py` (add `loki_url` + `loki_ready`
  fixtures)
- `tests/integration/test_log_pipeline.py` (new, 3 tests)
- `docs/analysis/05-IMPLEMENTATION-PLAN.md` (header update:
  mark Phase 16 DONE, link to 07 plan)
- `temp/phase-16-log-pipeline.md` (new, after ship)

No breaking changes. No application code touched. Phase 17 will
extend `docker/alloy/config.alloy` with `prometheus.scrape` blocks
— Phase 16 ships only the log half.
