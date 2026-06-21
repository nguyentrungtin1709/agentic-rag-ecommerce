# 15.0.0 — LangSmith Tracing (Phase 15)

**Status**: IMPLEMENTED
**Date**: 2026-06-18 (revised 2026-06-18, single-path)
**Scope**: Activate end-to-end AI/LLM tracing on LangSmith for the
FastAPI app via the ``langsmith`` SDK auto-trace path. Writes the
``LANGSMITH_*`` env vars at startup so every LangChain / LangGraph
call is traced to project ``agentic-rag-ecommerce``.

Implements Phase 1 of
[docs/analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md](../docs/analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md),
**single-path revision** (D6 was originally dual-path; see "Revision
history" below).

---

## Context

The audit performed 2026-06-17 (see
[07 §1](../docs/analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md))
recorded the main observability gap:

- ``LANGSMITH_TRACING=false`` — the ``langsmith`` SDK had the right
  env vars and dependencies installed, but the master switch was off,
  so no LangChain / LangGraph calls were traced.

Phase 15 closes that gap by wiring ``configure_tracing`` to write the
``LANGSMITH_*`` env vars from validated settings at startup. After
this change, one chat turn produces a trace in LangSmith covering the
orchestrator (root run, with ``correlation_id`` on the metadata), and
the user can click into any node to inspect prompts, tokens, and
latency.

The OpenInference + OTLP HTTP/protobuf path for LlamaIndex spans was
explored in the same phase and then **disabled** after evaluation —
see "Revision history" below. The OTel ``TracerProvider`` is still
built (idle, no library emits spans to it) so custom spans can be
added in the future without re-plumbing.

---

## Decisions

### D15.1 — Single ingestion path (revision of D6)

Only the **LangChain / LangGraph** ingestion path is active:

- Auto-traced by the ``langsmith`` SDK when ``LANGSMITH_TRACING=true``
  is set in the process environment before any ``langchain`` import.
  Activated by ``configure_langsmith(settings)`` at startup.

Rationale (2026-06-18 evaluation): the OpenInference + OTLP path
that produced per-call LlamaIndex spans (e.g.
``OpenAIEmbedding.aget_text_embedding``) was removed after the team
confirmed it is not needed for the current debugging workflow. The
``langsmith`` SDK already traces the LangGraph root run and every
node; that is sufficient for correlating one ``correlation_id`` to a
chat turn's behaviour. Per-call LlamaIndex span visibility can be
re-enabled by uncommenting one block in ``configure_tracing``.

### D15.2 — ``configure_langsmith`` uses ``os.environ[...] = ...``, NOT ``setdefault``

The legacy helper used ``os.environ.setdefault(...)`` so a stale
``LANGSMITH_API_KEY=""`` value in ``.env.example`` would silently
disable tracing in test environments. The new helper writes the env
vars unconditionally (``os.environ["LANGSMITH_API_KEY"] = ...``) so
the validated settings value always wins.

### D15.3 — OTel ``TracerProvider`` built but idle

``build_tracer_provider(settings)`` still constructs a
:class:`TracerProvider` with the three project resource attributes
(``service.name``, ``service.version``, ``deployment.environment``)
and attaches a ``BatchSpanProcessor`` wrapping ``OTLPSpanExporter``
pointing at LangSmith's OTLP endpoint. With no instrumentor wired,
the provider is idle (no spans ever land in the batch queue).

Rationale:

- A future ``FastAPIInstrumentor`` or custom
  ``trace.get_tracer(__name__).start_as_current_span(...)`` call will
  flow to LangSmith automatically without code changes.
- Idle ``BatchSpanProcessor`` has near-zero overhead (no spans queued
  → no exporter calls).
- Settings fields and env vars (``OTEL_EXPORTER_OTLP_ENDPOINT``,
  ``OTEL_EXPORTER_OTLP_HEADERS``) are kept so the wiring can be
  restored by flipping the instrumentor back on.

### D15.4 — Resource attributes locked

The OTel ``Resource`` on the global provider carries exactly three
attributes:

- ``service.name`` — always ``agentic-rag-ecommerce``.
- ``service.version`` — from ``Settings.app_version`` (default
  ``1.0.0``).
- ``deployment.environment`` — from
  ``Settings.deployment_environment`` (default ``development``).

These appear on every OTel span if/when one is emitted. LangSmith reads
them and surfaces them in the trace tree — useful for filtering
traces by environment when the same project receives dev + prod
traces.

### D15.5 — Headers default built from validated settings

When ``settings.otel_exporter_otlp_headers == ""`` the provider-build
code constructs
``f"x-api-key={settings.langsmith_api_key},Langsmith-Project={settings.langsmith_project}"``
at provider-build time. Operators only have to set
``LANGSMITH_API_KEY`` (already required) to get OTel tracing working;
they can still override the full headers string via
``OTEL_EXPORTER_OTLP_HEADERS`` for custom auth setups.

### D15.6 — OTel dependencies pinned to first-class

Three OpenTelemetry packages (``opentelemetry-api``, ``opentelemetry-sdk``,
``opentelemetry-instrumentation``) were transitive via
``openinference-instrumentation-llama-index`` 4.4.2. Phase 15 promotes
them to direct deps in ``pyproject.toml`` so a future dep cleanup
cannot silently drop them. The fourth
(``opentelemetry-exporter-otlp-proto-http``) is new and provides
the LangSmith OTLP ingestion path that D15.3 leaves in place.

Versions: ``>=1.42.0,<2.0`` (API/SDK/exporter) and
``>=0.63b1,<1.0`` (instrumentation) — matches the versions already
installed by the transitive chain.

---

## Revision history

| Date | Change | Reason |
|---|---|---|
| 2026-06-18 (initial) | Implemented D6 dual-path: ``langsmith`` SDK + OpenInference LlamaIndex OTLP. | Original Phase 1 spec. |
| 2026-06-18 (revision) | Disabled OpenInference path (removed ``instrument_llama_index`` call + function + 2 tests). Kept OTel infra (TracerProvider, exporter, env vars, deps) as a future-use hook. | Team evaluation: ``langsmith`` SDK's LangGraph coverage is sufficient for current debugging needs; the extra OTel path added complexity without clear value. |

The ``.instrument(tracer_provider=provider)`` call site in
``configure_tracing`` is documented in the source as a 1-line
restore point.

---

## Files changed

| File | Change |
|---|---|
| [src/app/config.py](../src/app/config.py) | Added 4 fields: ``otel_exporter_otlp_endpoint``, ``otel_exporter_otlp_headers``, ``deployment_environment``, ``app_version`` |
| [src/app/observability/tracing.py](../src/app/observability/tracing.py) | Added ``build_tracer_provider``, ``configure_langsmith``, helper ``_parse_otlp_headers``. ``instrument_llama_index`` was removed in the revision. |
| [pyproject.toml](../pyproject.toml) | Pinned 4 OTel deps under the ``# Observability`` comment |
| [.env](../.env) | Flipped ``LANGSMITH_TRACING`` to ``true``; added ``OTEL_EXPORTER_OTLP_*`` + ``DEPLOYMENT_ENVIRONMENT`` + ``APP_VERSION`` |
| [.env.example](../.env.example) | Same shape; ``LANGSMITH_TRACING`` stays ``false`` (template default) |
| [tests/unit/observability/__init__.py](../tests/unit/observability/__init__.py) | New empty package marker |
| [tests/unit/observability/test_tracing.py](../tests/unit/observability/test_tracing.py) | New: 12 unit tests covering provider construction, OTLP exporter wiring, env-var writes, header parsing |

## Files NOT changed

| File | Why |
|---|---|
| [src/app/main.py](../src/app/main.py) | ``configure_tracing`` call site at line 64 already runs at the right point in lifespan (after ``configure_logging``, before any LLM construction). No change. |
| [src/app/api/chat.py](../src/app/api/chat.py) | ``correlation_id`` binding at lines 131-137 is the wire contract — Phase 15 does not touch the endpoint. |
| [src/app/observability/logging.py](../src/app/observability/logging.py) | structlog JSON setup is owned by Phase 2 (Alloy/Loki label promotion). |

---

## Verification

| Gate | Result |
|---|---|
| ``uv run ruff check .`` | 0 errors |
| ``uv run ruff format --check .`` | 0 reformatting needed |
| ``uv run pyright src/ tests/unit/observability/`` | 0 errors, 0 warnings |
| ``uv run pytest tests/unit -q`` | 447 passed (+12 new observability), coverage 81% |
| ``uv run pytest tests/unit/observability/test_tracing.py -q`` | 12 passed (2.65s) |
| Inline smoke (``configure_tracing`` with both ``langsmith_tracing=True`` and ``=False``) | Both paths log expected messages; ``LANGSMITH_*`` env vars written only when ``langsmith_tracing=True`` |
| End-to-end (live LangSmith dashboard) | One chat turn produces a root ``LangGraph`` run with ``correlation_id`` on metadata and child spans for each node. Confirmed 2026-06-18. |

## Known limitations (this phase)

- The Docker Compose stack could not be started during this phase
  because the external ``saleor-platform_saleor-backend-tier`` network
  does not exist in this dev environment. The lifespan startup path
  is exercised by ``tests/unit/observability/test_tracing.py`` and an
  inline smoke. Full E2E verification (HTTP through the FastAPI
  container → trace in ``smith.langchain.com``) was confirmed by
  manual trace observation outside the test harness.
- No custom OTel span attributes; semantic conventions only.
- No trace sampling / quotas.
- Trace backend is LangSmith SaaS only (per D4).

---

## References

- [docs/analysis/06-OBSERVABILITY-DESIGN.md §6.3](../docs/analysis/06-OBSERVABILITY-DESIGN.md) — OTel resource attributes contract
- [docs/analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md §4](../docs/analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md) — Phase 1 spec (revised)
- [temp/observability-redesign.md §4](../temp/observability-redesign.md) — earlier working draft
- [src/app/observability/tracing.py](../src/app/observability/tracing.py) — implementation
- [src/app/api/chat.py:131-137](../src/app/api/chat.py#L131) — correlation_id wiring (unchanged)
- [tests/unit/observability/test_tracing.py](../tests/unit/observability/test_tracing.py) — unit tests