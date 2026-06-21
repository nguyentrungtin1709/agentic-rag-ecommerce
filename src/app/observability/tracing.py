"""OpenTelemetry tracing setup for the application.

Call :func:`configure_tracing` once at startup (from the FastAPI
``lifespan``) to enable trace export to LangSmith.

Only the **LangChain / LangGraph** ingestion path is active in this
configuration (Phase 15 — D6, LlamaIndex path temporarily disabled for
evaluation). The :class:`TracerProvider` is still built so that custom
spans added in the future (``trace.get_tracer(__name__).start_as_current_span(...)``)
flow to LangSmith automatically, but no library-side instrumentor is
attached.

- **LangChain / LangGraph** — auto-traced by the ``langsmith`` SDK
  when ``LANGSMITH_TRACING=true`` is set in the process environment.
  :func:`configure_langsmith` writes that flag (and the companion
  ``LANGSMITH_API_KEY`` / ``LANGSMITH_PROJECT`` / ``LANGSMITH_ENDPOINT``
  vars) from validated settings at startup.

When the OpenInference LlamaIndex instrumentor is wired back in, the
:class:`TracerProvider` already in place will receive those spans and
forward them via the OTLP HTTP/protobuf exporter to ``otel/v1/traces``
on the LangSmith host.
"""

from __future__ import annotations

import os

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings

logger = structlog.get_logger(__name__)

_DEFAULT_SERVICE_NAME = "agentic-rag-ecommerce"


def configure_tracing(settings: Settings) -> None:
    """Build the OTel ``TracerProvider`` and activate the ``langsmith``
    SDK auto-trace path.

    Must be called from ``lifespan`` AFTER :func:`app.observability.logging.configure_logging`
    so the structlog logger is initialised, and BEFORE any LLM-construction
    code so the providers are in place by the time the graph runs.

    Args:
        settings: Loaded application settings.
    """
    build_tracer_provider(settings)
    if settings.langsmith_tracing:
        configure_langsmith(settings)


def build_tracer_provider(settings: Settings) -> TracerProvider:
    """Construct a :class:`TracerProvider` with the project resource
    attributes and an OTLP HTTP/protobuf exporter.

    Resource attributes:

    - ``service.name`` — always ``agentic-rag-ecommerce``.
    - ``service.version`` — from ``settings.app_version``.
    - ``deployment.environment`` — from ``settings.deployment_environment``.

    The exporter endpoint is taken from ``settings.otel_exporter_otlp_endpoint``;
    the headers default to ``x-api-key=<key>,Langsmith-Project=<project>``
    when ``settings.otel_exporter_otlp_headers`` is empty.

    The provider is installed as the global tracer provider so the
    ``langsmith`` SDK picks it up for any span created via
    ``trace.get_tracer(__name__)``.

    Args:
        settings: Loaded application settings.

    Returns:
        The newly built :class:`TracerProvider`. Returned for testability;
        callers normally ignore it.
    """
    resource = Resource.create(
        {
            "service.name": _DEFAULT_SERVICE_NAME,
            "service.version": settings.app_version,
            "deployment.environment": settings.deployment_environment,
        },
    )
    provider = TracerProvider(resource=resource)

    headers_str = settings.otel_exporter_otlp_headers or _default_otlp_headers(settings)
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=_parse_otlp_headers(headers_str),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # ``trace.set_tracer_provider`` logs a warning on subsequent calls
    # but never raises; the guard here keeps the log clean for the
    # test-suite re-entry path where ``configure_tracing`` is invoked
    # twice in the same process.
    try:
        trace.set_tracer_provider(provider)
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("set_tracer_provider_failed", error=str(exc))

    logger.info(
        "tracing_configured",
        service_name=_DEFAULT_SERVICE_NAME,
        service_version=settings.app_version,
        deployment_environment=settings.deployment_environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )
    return provider


def configure_langsmith(settings: Settings) -> None:
    """Write the ``LANGSMITH_*`` env vars the ``langsmith`` SDK reads
    for auto-trace activation.

    Uses ``os.environ[...] = ...`` (NOT ``setdefault``) so a stale
    ``""`` value from ``.env.example`` in a test environment cannot
    silently disable tracing — :func:`configure_tracing` always wins.

    Args:
        settings: Loaded application settings.
    """
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_TRACING"] = "true"
    logger.info("langsmith_tracing_enabled", project=settings.langsmith_project)


def _default_otlp_headers(settings: Settings) -> str:
    """Build the default OTLP ``x-api-key`` + ``Langsmith-Project`` header
    string when ``settings.otel_exporter_otlp_headers`` is empty."""
    return f"x-api-key={settings.langsmith_api_key},Langsmith-Project={settings.langsmith_project}"


def _parse_otlp_headers(headers_str: str) -> dict[str, str]:
    """Parse a ``k1=v1,k2=v2`` string into ``{"k1": "v1", "k2": "v2"}``.

    Whitespace around keys and values is stripped. Empty segments
    (from a trailing comma) are skipped silently.

    Args:
        headers_str: Raw header string from ``OTEL_EXPORTER_OTLP_HEADERS``.

    Returns:
        Parsed headers dict suitable for ``OTLPSpanExporter(headers=...)``.

    Raises:
        ValueError: If a non-empty segment lacks ``=``.
    """
    headers: dict[str, str] = {}
    for pair in headers_str.split(","):
        stripped = pair.strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise ValueError(f"Invalid OTLP header pair: {pair!r}")
        key, value = stripped.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers
