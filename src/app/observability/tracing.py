"""OpenTelemetry tracing setup for the application.

Call ``configure_tracing()`` once at startup to enable trace export to
LangSmith (when ``LANGSMITH_TRACING=true``) and to instrument
LlamaIndex via the OpenInference integration.

The function is intentionally a no-op when tracing is disabled so that
the rest of the codebase never needs to guard against ``None`` tracers.
"""

import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)


def configure_tracing(settings: Settings) -> None:
    """Enable observability instrumentation based on settings.

    Activates OpenInference LlamaIndex instrumentation unconditionally
    (it is cheap and emits structured spans to any configured OTLP
    exporter).  LangSmith tracing is only activated when
    ``LANGSMITH_TRACING=true``.

    Args:
        settings: Loaded application settings.
    """
    _instrument_llama_index()

    if settings.langsmith_tracing:
        _configure_langsmith(settings)


def _instrument_llama_index() -> None:
    """Register OpenInference instrumentation for LlamaIndex."""
    try:
        from openinference.instrumentation.llama_index import (
            LlamaIndexInstrumentor,
        )

        LlamaIndexInstrumentor().instrument()
        logger.info("LlamaIndex OpenInference instrumentation active")
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to instrument LlamaIndex", error=str(exc))


def _configure_langsmith(settings: Settings) -> None:
    """Set environment variables required by the LangSmith SDK.

    The LangSmith SDK reads ``LANGSMITH_API_KEY``, ``LANGSMITH_PROJECT``,
    ``LANGSMITH_TRACING``, and ``LANGSMITH_ENDPOINT`` directly from the
    environment, so we write them here from validated settings.

    Args:
        settings: Loaded application settings.
    """
    import os

    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    logger.info("LangSmith tracing enabled", project=settings.langsmith_project)
