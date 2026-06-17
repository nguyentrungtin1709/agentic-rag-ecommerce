"""Unit tests for app.agent.subagents.trend_scout.tools.

Covers D11.12 (single exposed tool + private fallback helper) and the
graceful-degradation contract from D11.6: the tools raise ``RuntimeError``
on total failure so the wrapper can apply D11.6 (return ``None`` upstream
only when both backends fail).

Module-level note: ``tools.py`` lazily instantiates ``DuckDuckGoSearchRun``
under a defensive ``try/except`` because the project's pinned
``duckduckgo-search`` package does not match ``langchain_community==0.4.2``'s
expectation of an ``ddgs`` package. When the client cannot be built, the
module logs a warning and sets ``_ddg = None``; ``duckduckgo_search`` then
raises ``RuntimeError`` (the follow-up pyproject fix is documented in
``tools.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _tavily_block(title: str, url: str, content: str) -> dict:
    """Build one Tavily-shaped result block."""
    return {"title": title, "url": url, "content": content}


def _ddg_block(title: str, snippet: str, url: str) -> str:
    """Build one ``DuckDuckGoSearchRun``-shaped string block.

    Real ``DuckDuckGoSearchRun`` output uses ``\\n\\n`` as the block
    separator and parenthesises the link at the end of the block.
    """
    return f"{title}\n{snippet}\n({url})"


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_tavily_search_returns_parsed_list_dict_on_success() -> None:
    """``tavily_search`` must return a list of dicts on a successful Tavily call."""
    from app.agent.subagents.trend_scout import tools

    results = [
        _tavily_block("T1", "https://a.example", "snippet A"),
        _tavily_block("T2", "https://b.example", "snippet B"),
    ]

    with patch.object(tools, "_tavily") as mock_tavily:
        mock_tavily.invoke.return_value = results
        out = tools.tavily_search.invoke({"query": "2026 summer t-shirt trends"})

    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["title"] == "T1"
    assert out[1]["url"] == "https://b.example"
    mock_tavily.invoke.assert_called_once()


def test_tavily_search_falls_back_to_duckduckgo_on_tavily_exception() -> None:
    """``tavily_search`` must call ``duckduckgo_search`` when Tavily raises."""
    from app.agent.subagents.trend_scout import tools

    expected = [
        {"title": "DDG title", "url": "https://ddg.example", "content": "DDG snippet"},
    ]

    with (
        patch.object(tools, "_tavily") as mock_tavily,
        patch.object(tools, "duckduckgo_search", return_value=expected) as mock_ddg,
    ):
        mock_tavily.invoke.side_effect = RuntimeError("rate-limited")
        out = tools.tavily_search.invoke({"query": "kettle design 2026"})

    assert out == expected
    mock_tavily.invoke.assert_called_once()
    mock_ddg.assert_called_once_with("kettle design 2026", 5)


def test_duckduckgo_search_parses_ddg_string_output_to_list_dict() -> None:
    """``duckduckgo_search`` must parse ``DuckDuckGoSearchRun`` string output."""
    from app.agent.subagents.trend_scout import tools

    raw = (
        _ddg_block("First title", "First snippet", "https://one.example")
        + "\n\n"
        + _ddg_block("Second title", "Second snippet", "https://two.example")
    )

    with patch.object(tools, "_ddg") as mock_ddg:
        mock_ddg.invoke.return_value = raw
        out = tools.duckduckgo_search("anything", max_results=5)

    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0] == {
        "title": "First title",
        "url": "https://one.example",
        "content": "First snippet",
    }
    assert out[1]["url"] == "https://two.example"


def test_duckduckgo_search_raises_runtime_error_when_ddg_fails() -> None:
    """``duckduckgo_search`` must surface a ``RuntimeError`` when DDG itself fails."""
    from app.agent.subagents.trend_scout import tools

    with patch.object(tools, "_ddg") as mock_ddg:
        mock_ddg.invoke.side_effect = ConnectionError("ddg network down")
        with pytest.raises(RuntimeError):
            tools.duckduckgo_search("anything", max_results=5)


def test_tavily_search_has_tool_decorator_with_name_and_description() -> None:
    """``tavily_search`` must be a ``langchain_core`` tool with name/description."""
    from app.agent.subagents.trend_scout import tools

    assert hasattr(tools.tavily_search, "name"), "tavily_search must be a @tool"
    assert tools.tavily_search.name == "tavily_search"
    assert isinstance(tools.tavily_search.description, str)
    assert "trend" in tools.tavily_search.description.lower()


def test_d11_12_contract_duckduckgo_search_is_plain_function_not_tool() -> None:
    """D11.12: ``duckduckgo_search`` must NOT be a ``@tool``; it is a private helper.

    Exposing it as a tool would force the LLM to choose between Tavily
    and DuckDuckGo, wasting a round-trip when Tavily is the canonical
    primary backend.
    """
    from app.agent.subagents.trend_scout import tools

    assert not hasattr(tools.duckduckgo_search, "name"), (
        "duckduckgo_search must not be a @tool — D11.12 keeps the "
        "fallback as a private module-level function."
    )
    assert not hasattr(tools.duckduckgo_search, "description"), (
        "duckduckgo_search must not be a @tool — D11.12 keeps the "
        "fallback as a private module-level function."
    )
    assert callable(tools.duckduckgo_search)


# ── 16.0.0 — ddgs dependency fix regression guard ────────────────────────────


def test_ddg_client_initialised_after_ddgs_dependency_fix() -> None:
    """``tools._ddg`` is NOT ``None`` once the ``ddgs`` package is installed.

    The historical bug: ``pyproject.toml`` pinned
    ``duckduckgo-search==8.1.1`` while
    ``langchain_community==0.4.2`` requires ``ddgs``.  At module
    load time ``DuckDuckGoSearchRun.__init__`` raised inside its
    Pydantic model_validator and the module caught the exception,
    setting ``_ddg = None`` and logging
    ``duckduckgo_client_init_failed``.

    16.0.0 swaps the pin to ``ddgs>=9.0.0,<10.0.0``.  This test
    pins the resolved contract: after import, ``_ddg`` must be
    a real ``DuckDuckGoSearchRun`` instance, NOT ``None``.
    """
    from langchain_community.tools import DuckDuckGoSearchRun

    from app.agent.subagents.trend_scout import tools

    assert tools._ddg is not None, (
        "tools._ddg is None — ddgs package is missing or "
        "DuckDuckGoSearchRun.__init__ raised; the pyproject.toml "
        "pin must be the ddgs package, not duckduckgo-search."
    )
    assert isinstance(tools._ddg, DuckDuckGoSearchRun)
