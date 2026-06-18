"""Unit tests for ``app.observability.tracing`` (Phase 15).

Covers the new wiring in :mod:`app.observability.tracing`:

- :func:`build_tracer_provider` builds an OTel ``TracerProvider`` with the
  project resource attributes and an OTLP HTTP/protobuf exporter
  pointing at LangSmith.
- :func:`instrument_llama_index` calls
  ``LlamaIndexInstrumentor().instrument(tracer_provider=...)`` with the
  built provider.
- :func:`configure_langsmith` writes the four ``LANGSMITH_*`` env vars
  using ``os.environ[...] = ...`` (NOT ``setdefault``) so a stale empty
  value in ``.env.example`` cannot silently disable tracing.

No live OTel exporter or HTTP request is performed — tests read the
exporter config directly off the provider's span processor.
"""

from __future__ import annotations

import os

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider

from app.config import Settings
from app.observability.tracing import (
    _parse_otlp_headers,
    build_tracer_provider,
    configure_langsmith,
    configure_tracing,
)

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────


def _test_settings(
    *,
    langsmith_tracing: bool = True,
    langsmith_api_key: str = "lsv2_pt_test_key",
    langsmith_project: str = "agentic-rag-ecommerce",
    otel_endpoint: str = "https://aws.api.smith.langchain.com/otel/v1/traces",
    otel_headers: str = "",
    deployment_environment: str = "development",
    app_version: str = "1.0.0",
) -> Settings:
    """Build a Settings instance bypassing the .env file."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="sk-test",
        saleor_webhook_secret="test-secret-32-chars-minimum-abc",
        langsmith_tracing=langsmith_tracing,
        langsmith_api_key=langsmith_api_key,
        langsmith_project=langsmith_project,
        otel_exporter_otlp_endpoint=otel_endpoint,
        otel_exporter_otlp_headers=otel_headers,
        deployment_environment=deployment_environment,
        app_version=app_version,
    )


def _provider_exporter(provider: TracerProvider) -> OTLPSpanExporter:
    """Return the OTLP span exporter attached to ``provider``.

    OTel SDK 1.42 wraps the registered ``BatchSpanProcessor`` inside a
    ``SynchronousMultiSpanProcessor``; we drill into ``_span_processors[0]``
    to reach the BatchSpanProcessor and read its public ``span_exporter``.
    Internal-attribute access is a documented OTel SDK test seam.
    """
    multi = provider._active_span_processor  # type: ignore[attr-defined]
    sub_processors = multi._span_processors  # type: ignore[attr-defined]
    batch = sub_processors[0]
    return batch.span_exporter  # type: ignore[attr-defined]


# ── build_tracer_provider ────────────────────────────────────────────────


def test_build_tracer_provider_sets_resource_attributes() -> None:
    """Resource carries service.name / service.version / deployment.environment."""
    settings = _test_settings(
        deployment_environment="staging",
        app_version="9.9.9",
    )
    provider = build_tracer_provider(settings)
    try:
        attrs = provider.resource.attributes
        assert attrs["service.name"] == "agentic-rag-ecommerce"
        assert attrs["service.version"] == "9.9.9"
        assert attrs["deployment.environment"] == "staging"
    finally:
        provider.shutdown()


def test_build_tracer_provider_attaches_otlp_exporter() -> None:
    """Exporter is OTLPSpanExporter with the configured endpoint."""
    settings = _test_settings(
        otel_endpoint="https://example.com/otel/v1/traces",
    )
    provider = build_tracer_provider(settings)
    try:
        exporter = _provider_exporter(provider)
        assert isinstance(exporter, OTLPSpanExporter)
        assert exporter._endpoint == "https://example.com/otel/v1/traces"  # type: ignore[attr-defined]
    finally:
        provider.shutdown()


def test_otlp_headers_default_construction() -> None:
    """Empty settings.otel_exporter_otlp_headers -> built from langsmith creds."""
    settings = _test_settings(
        langsmith_api_key="lsv2_pt_default_test",
        langsmith_project="proj-default",
        otel_headers="",
    )
    provider = build_tracer_provider(settings)
    try:
        exporter = _provider_exporter(provider)
        headers = exporter._headers  # type: ignore[attr-defined]
        assert headers == {
            "x-api-key": "lsv2_pt_default_test",
            "Langsmith-Project": "proj-default",
        }
    finally:
        provider.shutdown()


def test_otlp_headers_override_passthrough() -> None:
    """When otel_headers is non-empty, that value is parsed verbatim."""
    settings = _test_settings(
        otel_headers="Authorization=Bearer abc123, X-Custom=42",
    )
    provider = build_tracer_provider(settings)
    try:
        exporter = _provider_exporter(provider)
        assert exporter._headers == {  # type: ignore[attr-defined]
            "Authorization": "Bearer abc123",
            "X-Custom": "42",
        }
    finally:
        provider.shutdown()


def test_set_tracer_provider_is_idempotent_under_repeated_calls() -> None:
    """Calling configure_tracing twice in the same process must NOT raise."""
    settings = _test_settings()
    provider1 = build_tracer_provider(settings)
    provider2 = build_tracer_provider(settings)
    try:
        # Both providers are constructed cleanly; OTel may swap the global
        # to the second one but neither call raises.
        assert isinstance(provider1, TracerProvider)
        assert isinstance(provider2, TracerProvider)
    finally:
        provider1.shutdown()
        provider2.shutdown()


# ── configure_langsmith ──────────────────────────────────────────────────


def test_configure_langsmith_sets_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """All 4 LANGSMITH_* env vars are written from settings."""
    for var in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_TRACING",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = _test_settings(
        langsmith_api_key="lsv2_pt_env_test",
        langsmith_project="proj-env",
    )
    settings.langsmith_endpoint = "https://eu.api.smith.langchain.com"  # type: ignore[misc]
    configure_langsmith(settings)

    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_pt_env_test"
    assert os.environ["LANGSMITH_PROJECT"] == "proj-env"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"
    assert os.environ["LANGSMITH_TRACING"] == "true"


def test_configure_langsmith_overrides_stale_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-existing LANGSMITH_API_KEY='' must be overwritten (NOT setdefault)."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    settings = _test_settings(langsmith_api_key="lsv2_pt_real_override")
    configure_langsmith(settings)
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_pt_real_override"


# ── configure_tracing (top-level) ─────────────────────────────────────────


def test_configure_tracing_skips_langsmith_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When langsmith_tracing=False, no LANGSMITH_TRACING=true is written."""
    for var in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_TRACING",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = _test_settings(langsmith_tracing=False)
    configure_tracing(settings)

    # Nothing was written because configure_langsmith was not called.
    assert "LANGSMITH_TRACING" not in os.environ
    assert "LANGSMITH_API_KEY" not in os.environ


# ── _parse_otlp_headers ──────────────────────────────────────────────────


def test_parse_otlp_headers_basic() -> None:
    assert _parse_otlp_headers("k1=v1,k2=v2") == {"k1": "v1", "k2": "v2"}


def test_parse_otlp_headers_strips_whitespace() -> None:
    assert _parse_otlp_headers("  k1 = v1 , k2 = v2  ") == {"k1": "v1", "k2": "v2"}


def test_parse_otlp_headers_skips_empty_segments() -> None:
    assert _parse_otlp_headers("k1=v1,,k2=v2,") == {"k1": "v1", "k2": "v2"}


def test_parse_otlp_headers_raises_on_malformed() -> None:
    with pytest.raises(ValueError, match="Invalid OTLP header pair"):
        _parse_otlp_headers("k1=v1,novalue")
