"""LangChain tools for the TrendScout subagent (D11.12: single exposed tool).

Module exposes exactly ONE ``@tool`` to the LLM:

- ``tavily_search`` -- Primary web search via Tavily API (FR-042).
  On Tavily failure (rate-limit, network, quota), transparently
  falls back to the module-level ``duckduckgo_search`` helper
  (FR-043). This is the D11.2/D11.12 placement decision: keep
  fallback logic in the tool itself so the LLM does not waste a
  round-trip retrying a dead tool, and do not pollute the tool
  schema with a separate fallback tool the LLM should not pick.

- ``duckduckgo_search`` -- Plain module-level function (NOT a
  ``@tool``). Used only as an internal fallback when Tavily
  raises. Raises on its own failure so the wrapper can apply
  D11.6 graceful degradation. This asymmetry -- one tool exposed
  to the LLM, two call paths in code -- is the single-tool design
  decided in D11.12.

Deprecation note: ``TavilySearchResults`` is marked deprecated in
``langchain_community==0.4.2`` (the recommended replacement is
``langchain_tavily.TavilySearch``). The project intentionally does
NOT add ``langchain-tavily`` as a dependency (D11.10) and the
deprecation warning is suppressed at module level. The class will
continue to work until ``langchain_community==1.0`` removes it; at
that point the migration target is one line
(``from langchain_tavily import TavilySearch``).
"""

from __future__ import annotations

import warnings

import structlog
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

# D11.10: suppress the TavilySearchResults deprecation warning emitted
# at import time. The class is still functional in 0.4.2 and is the
# chosen integration point (no langchain-tavily dep, D11.10).
warnings.filterwarnings(
    "ignore",
    message=r".*TavilySearchResults.*deprecated.*",
    category=DeprecationWarning,
)

logger = structlog.get_logger(__name__)

# ``TavilySearchAPIWrapper`` (used internally by ``TavilySearchResults``)
# reads ``TAVILY_API_KEY`` from ``os.environ`` at construction time. We
# pass it explicitly via ``api_wrapper_kwargs`` so the module-level
# singleton can be built without mutating ``os.environ`` at import time
# (mutation would leak ``TAVILY_API_KEY`` into the test process).
from app.config import get_settings  # noqa: E402, PLC0415  (lazy to avoid cycle)

_settings = get_settings()
_tavily = TavilySearchResults(
    max_results=5,
    tavily_api_key=_settings.tavily_api_key,
)

# ``DuckDuckGoSearchRun.__init__`` triggers a Pydantic model_validator
# that requires the ``ddgs`` package (a successor of
# ``duckduckgo-search``).  The project currently pins
# ``duckduckgo-search==8.1.1`` while ``langchain_community==0.4.2``
# requires ``ddgs``; this is a pre-existing latent mismatch that
# surfaces only when the class is instantiated.  We attempt the
# construction defensively and degrade to ``None`` so that module
# import succeeds; ``duckduckgo_search`` then raises
# ``RuntimeError`` if the fallback is invoked without a working
# client.  The follow-up is to update ``pyproject.toml`` to depend
# on ``ddgs`` directly.
try:
    _ddg: DuckDuckGoSearchRun | None = DuckDuckGoSearchRun()
except Exception as exc:  # noqa: BLE001  (ImportError + Pydantic ValueError)
    logger.warning(
        "duckduckgo_client_init_failed",
        error=str(exc),
        error_type=type(exc).__name__,
        hint=(
            "ddgs package may be missing — see pyproject.toml follow-up. "
            "Tavily remains the primary search backend."
        ),
    )
    _ddg = None


@tool
def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for current POD design trends, styles, and themes.

    Primary search backend is Tavily (high-quality AI-optimized
    results). If the Tavily call fails (rate-limit, quota
    exceeded, network error), this tool transparently falls back
    to ``duckduckgo_search``. Returns a list of result dicts.

    Args:
        query: Search query string, e.g.
            ``"2026 summer t-shirt trends"``. Formulate freely
            based on the user's context -- the store covers all
            theme categories.
        max_results: Maximum number of results to return. Defaults
            to ``5``. Note: the underlying Tavily client is
            constructed once at module load with ``max_results=5``;
            the parameter is accepted for API compatibility but
            the per-call override is not yet threaded through to
            the singleton (see D11 follow-up).

    Returns:
        List of result dicts, each containing at least
        ``{"title": str, "url": str, "content": str}`` (exact
        shape depends on the underlying backend).

    Raises:
        RuntimeError: If both Tavily and the DuckDuckGo fallback
            fail. The wrapper catches this and returns
            ``{"trend_summary": None, "image_prompt": None}``
            (D11.6 graceful degradation).
    """
    try:
        result = _tavily.invoke({"query": query})
        return _normalise_tavily(result)
    except Exception as exc:
        logger.warning(
            "tavily_search_failed_falling_back",
            query=query,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return duckduckgo_search(query, max_results)


# NOTE: NO ``@tool`` decorator. This function is a private
# module-level helper called only from inside ``tavily_search`` when
# the Tavily backend fails. Exposing it as a separate tool would
# force the LLM to reason about which backend to choose (D11.12).
def duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    """Fallback web search via DuckDuckGo (D11.12: private helper).

    Called only from inside ``tavily_search`` when Tavily raises.
    Returns a list of result dicts parsed from the
    ``DuckDuckGoSearchRun`` string output. Raises ``RuntimeError``
    on its own failure so the wrapper can apply D11.6 graceful
    degradation.

    Args:
        query: Search query string.
        max_results: Approximate maximum number of results to
            return. ``DuckDuckGoSearchRun`` does not honour this
            strictly; the helper truncates the parsed list.

    Returns:
        List of result dicts, each with at least
        ``{"title": str, "url": str, "content": str}``.

    Raises:
        RuntimeError: If the underlying ``DuckDuckGoSearchRun``
            call fails, or if the client failed to initialise at
            module import time (the ``ddgs`` package missing).
    """
    if _ddg is None:
        raise RuntimeError(
            "DuckDuckGo client unavailable (ddgs package missing at "
            "module import time). Tavily remains the primary backend; "
            "this fallback cannot run without ddgs."
        )
    try:
        raw = _ddg.invoke(query)
        return _parse_ddg_output(raw, max_results)
    except Exception as exc:
        logger.error(
            "duckduckgo_search_failed",
            query=query,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise RuntimeError(f"DuckDuckGo search failed: {exc}") from exc


def _normalise_tavily(raw: object) -> list[dict]:
    """Normalise Tavily result shape to ``list[dict]``.

    ``TavilySearchResults`` (deprecated in 0.4.2) returns a tuple
    ``(results, raw_metadata)`` from its ``_run`` / ``_arun`` path.
    When invoked via ``BaseTool.invoke``, the tuple passes through
    unmodified (langchain-community 0.4.2 does not unwrap it), so
    this helper handles both the bare-list shape (older versions,
    fresh from a direct ``api_wrapper.clean_results`` call) and the
    tuple shape.
    """
    if isinstance(raw, tuple) and len(raw) >= 1:
        raw = raw[0]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        # Some failure paths stringify an exception. Surface as empty.
        return []
    return []


def _parse_ddg_output(raw: object, max_results: int) -> list[dict]:
    """Parse ``DuckDuckGoSearchRun`` string output into list[dict].

    ``DuckDuckGoSearchRun`` returns newline-separated blocks of the
    form ``<title>\\n<snippet>\\n<link>`` (link is parenthesised by
    the underlying ``DuckDuckGoSearchAPIWrapper``). We split on the
    last parenthesised URL per block to recover individual results.

    Defensive against:
    - Empty / non-string input (returns empty list).
    - Blocks without parenthesised URLs (title is the full block).
    - More blocks than ``max_results`` (truncates to the cap).
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    results: list[dict] = []
    for chunk in raw.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        title = snippet = url = ""
        if "(" in chunk and ")" in chunk:
            url = chunk[chunk.rfind("(") + 1 : chunk.rfind(")")]
            rest = chunk[: chunk.rfind("(")].strip()
            if "\n" in rest:
                title, snippet = rest.split("\n", 1)
            else:
                title = rest
        else:
            if "\n" in chunk:
                title, snippet = chunk.split("\n", 1)
            else:
                title = chunk
        results.append({"title": title, "url": url, "content": snippet})
        if len(results) >= max_results:
            break
    return results
