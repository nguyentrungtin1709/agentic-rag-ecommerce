"""Unit tests for app.agent.prompts.load_prompt utility."""

from __future__ import annotations

import pytest

EXPECTED_TEMPLATE_NAMES = [
    "orchestrator_system",
    "profiler_system",
    "summarize_system",
    "prepare_query_system",
    "rerank_system",
    "title_system",
    "synthesize_sufficient_system",
    "synthesize_clarification_system",
    "synthesize_out_of_scope_system",
    "synthesize_fallback_system",
    "trend_scout_system",
]


@pytest.mark.parametrize("name", EXPECTED_TEMPLATE_NAMES)
def test_load_prompt_returns_non_empty_string(name: str) -> None:
    """Each registered prompt template must return a non-empty string."""
    from app.agent.prompts import load_prompt

    content = load_prompt(name)
    assert isinstance(content, str)
    assert len(content.strip()) > 0, f"Prompt '{name}' is empty"


def test_load_prompt_unknown_name_raises_file_not_found() -> None:
    """Requesting a non-existent template must raise FileNotFoundError."""
    from app.agent.prompts import load_prompt

    with pytest.raises(FileNotFoundError):
        load_prompt("this_template_does_not_exist")


def test_load_prompt_no_extension_needed() -> None:
    """The caller must NOT include .md in the name argument."""
    from app.agent.prompts import load_prompt

    # Calling with .md suffix should raise FileNotFoundError (double extension)
    with pytest.raises(FileNotFoundError):
        load_prompt("orchestrator_system.md")
