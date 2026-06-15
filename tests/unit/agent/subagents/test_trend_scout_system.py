"""Unit tests for app.agent.subagents.trend_scout.system.

``_build_trend_scout_system`` composes a dynamic ``SystemMessage`` content
string from the base prompt loaded via ``app.agent.prompts.load_prompt``
plus optional context sections (conversation summary, user profile,
retrieved products, image-output note). Each section must be appended
only when the corresponding field is non-empty so the LLM never sees
blank sections.
"""

from __future__ import annotations

import json

# ── Tests ─────────────────────────────────────────────────────────────────────


def test_build_trend_scout_system_returns_base_prompt_alone_when_all_empty() -> None:
    """All-empty inputs must return just the base prompt, no extra sections."""
    from app.agent.prompts import load_prompt
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    base = load_prompt("trend_scout_system")
    out = _build_trend_scout_system(
        summary="",
        user_profile=None,
        retrieved_products=[],
        generate_image=False,
    )

    assert out == base


def test_build_trend_scout_system_appends_summary_section_when_summary_present() -> None:
    """Non-empty ``summary`` must trigger a 'Conversation history summary' section."""
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    out = _build_trend_scout_system(
        summary="User asked earlier about minimalist tees.",
        user_profile=None,
        retrieved_products=[],
        generate_image=False,
    )

    assert "## Conversation history summary" in out
    assert "User asked earlier about minimalist tees." in out


def test_build_trend_scout_system_appends_user_profile_section_as_json() -> None:
    """Non-None ``user_profile`` must be serialised as JSON under 'User preferences'."""
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    profile = {"style_preferences": ["minimalist", "monochrome"], "age": 28}
    out = _build_trend_scout_system(
        summary="",
        user_profile=profile,
        retrieved_products=[],
        generate_image=False,
    )

    assert "## User preferences" in out
    assert json.dumps(profile, ensure_ascii=False) in out


def test_build_trend_scout_system_appends_retrieved_products_section() -> None:
    """Non-empty ``retrieved_products`` must produce a 'Products already recommended' section."""
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    products = [
        {"name": "Black minimalist tee", "product_id": "p-001"},
        {"name": "White line-art hoodie", "product_id": "p-002"},
    ]
    out = _build_trend_scout_system(
        summary="",
        user_profile=None,
        retrieved_products=products,
        generate_image=False,
    )

    assert "## Products already recommended" in out
    assert "Black minimalist tee" in out
    assert "White line-art hoodie" in out


def test_build_trend_scout_system_falls_back_to_product_id_when_name_missing() -> None:
    """Products without a ``name`` key must fall back to ``product_id`` in the summary."""
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    products = [{"product_id": "p-fallback"}]
    out = _build_trend_scout_system(
        summary="",
        user_profile=None,
        retrieved_products=products,
        generate_image=False,
    )

    assert "p-fallback" in out


def test_build_trend_scout_system_appends_image_output_note_when_generate_image_true() -> None:
    """``generate_image=True`` must add an 'Output note' prompting image emission."""
    from app.agent.subagents.trend_scout.system import _build_trend_scout_system

    out_with = _build_trend_scout_system(
        summary="",
        user_profile=None,
        retrieved_products=[],
        generate_image=True,
    )
    out_without = _build_trend_scout_system(
        summary="",
        user_profile=None,
        retrieved_products=[],
        generate_image=False,
    )

    assert "## Output note" in out_with
    assert "text-to-image prompt" in out_with
    assert "## Output note" not in out_without
