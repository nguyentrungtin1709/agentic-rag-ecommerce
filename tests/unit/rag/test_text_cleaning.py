"""Unit tests — text cleaning helpers in ``app.rag.text_cleaning``.

Covers plain text, HTML, and EditorJS JSON inputs to
:func:`clean_product_description`, plus the LLM/no-LLM paths in
:func:`description_for_embedding`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.rag.text_cleaning import (
    clean_product_description,
    description_for_embedding,
)

# ---------------------------------------------------------------------------
# clean_product_description
# ---------------------------------------------------------------------------


def test_clean_product_description_plain_text_returns_as_is() -> None:
    """Plain text without HTML or JSON passes through (whitespace collapsed)."""
    result = clean_product_description("  Cotton t-shirt,   breathable fabric.  ")
    assert result == "Cotton t-shirt, breathable fabric."


def test_clean_product_description_empty_input_returns_empty() -> None:
    """Empty string and ``None``-ish inputs return empty string."""
    assert clean_product_description("") == ""
    # The function only accepts ``str``; non-str is a programming error
    # so we do not exercise it here.


def test_clean_product_description_html_strips_tags() -> None:
    """HTML tags are removed and surrounding whitespace collapsed."""
    raw = "<p>Cotton t-shirt</p><br/><b>breathable</b> fabric"
    result = clean_product_description(raw)
    assert result == "Cotton t-shirt breathable fabric"


def test_clean_product_description_decodes_html_entities() -> None:
    """``html.unescape`` runs after tag stripping (e.g. ``&amp;`` -> ``&``)."""
    raw = "Q&amp;A: cotton &amp; linen blend"
    result = clean_product_description(raw)
    assert result == "Q&A: cotton & linen blend"


def test_clean_product_description_collapses_whitespace() -> None:
    """Internal runs of whitespace (incl. newlines) collapse to a single space."""
    raw = "line one\n\nline two\t\tline   three"
    result = clean_product_description(raw)
    assert result == "line one line two line three"


def test_clean_product_description_editorjs_concatenates_paragraphs() -> None:
    """EditorJS ``paragraph`` blocks are joined with ``\\n\\n``."""
    raw = json.dumps(
        {
            "blocks": [
                {"type": "paragraph", "data": {"text": "First paragraph."}},
                {"type": "paragraph", "data": {"text": "Second paragraph."}},
            ]
        }
    )
    result = clean_product_description(raw)
    assert result == "First paragraph.\n\nSecond paragraph."


def test_clean_product_description_editorjs_includes_headers() -> None:
    """``header`` blocks are included alongside ``paragraph`` blocks."""
    raw = json.dumps(
        {
            "blocks": [
                {"type": "header", "data": {"text": "Material", "level": 2}},
                {"type": "paragraph", "data": {"text": "100% cotton"}},
            ]
        }
    )
    result = clean_product_description(raw)
    assert result == "Material\n\n100% cotton"


def test_clean_product_description_editorjs_skips_unknown_block_types() -> None:
    """``image`` and ``delimiter`` blocks are ignored (no ``text`` extraction)."""
    raw = json.dumps(
        {
            "blocks": [
                {"type": "image", "data": {"url": "x", "caption": "ignored"}},
                {"type": "delimiter", "data": {}},
                {"type": "paragraph", "data": {"text": "kept"}},
            ]
        }
    )
    result = clean_product_description(raw)
    assert result == "kept"


def test_clean_product_description_editorjs_skips_blocks_without_text() -> None:
    """Blocks whose ``data.text`` is missing or non-string are ignored."""
    raw = json.dumps(
        {
            "blocks": [
                {"type": "paragraph", "data": {}},
                {"type": "paragraph", "data": {"text": None}},
                {"type": "paragraph", "data": {"text": "kept"}},
            ]
        }
    )
    result = clean_product_description(raw)
    assert result == "kept"


def test_clean_product_description_editorjs_skips_empty_text() -> None:
    """Blocks with whitespace-only text are dropped (no empty paragraphs)."""
    raw = json.dumps(
        {
            "blocks": [
                {"type": "paragraph", "data": {"text": "   "}},
                {"type": "paragraph", "data": {"text": "kept"}},
            ]
        }
    )
    result = clean_product_description(raw)
    assert result == "kept"


def test_clean_product_description_invalid_json_treated_as_plain_text() -> None:
    """A non-EditorJS JSON-ish string is treated as plain text (not an error)."""
    raw = "{ not valid json"
    result = clean_product_description(raw)
    assert result == "{ not valid json"


def test_clean_product_description_json_without_blocks_treated_as_plain_text() -> None:
    """A JSON object without ``blocks`` is not EditorJS — fall back to plain text."""
    raw = json.dumps({"foo": "bar"})
    result = clean_product_description(raw)
    assert result == '{"foo": "bar"}'


# ---------------------------------------------------------------------------
# description_for_embedding
# ---------------------------------------------------------------------------


async def test_description_for_embedding_short_text_unchanged() -> None:
    """Short text is returned verbatim without making any LLM call."""
    short = "a" * 100

    with patch("app.rag.text_cleaning.ChatOpenAI") as mock_chat:
        result = await description_for_embedding(short, max_chars=500)

    assert result == short
    mock_chat.assert_not_called()


async def test_description_for_embedding_long_text_calls_llm() -> None:
    """Long text triggers an LLM call; the returned summary is used."""
    long = "lorem ipsum " * 200  # well over 500 chars
    fake_response = MagicMock()
    fake_response.content = "Short summary"
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        result = await description_for_embedding(long, max_chars=50)

    assert result == "Short summary"
    fake_model.ainvoke.assert_awaited_once()
    call_args = fake_model.ainvoke.call_args.args[0]
    # First message is the system prompt, second is the cleaned text.
    assert call_args[0].content  # non-empty system message
    assert call_args[1].content == long


async def test_description_for_embedding_summary_is_bounded_to_max_chars() -> None:
    """A summary longer than ``max_chars`` is defensively truncated."""
    long = "lorem ipsum " * 200
    oversized_summary = "x" * 200  # exceeds max_chars=100
    fake_response = MagicMock()
    fake_response.content = oversized_summary
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        result = await description_for_embedding(long, max_chars=100)

    assert len(result) == 100
    assert result == "x" * 100


async def test_description_for_embedding_llm_failure_falls_back_to_truncation() -> None:
    """When the LLM raises, the function falls back to ``cleaned[:max_chars] + '...'``."""
    long = "x" * 1000
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        result = await description_for_embedding(long, max_chars=100)

    assert result == f"{'x' * 100}..."


async def test_description_for_embedding_empty_llm_response_falls_back() -> None:
    """An empty LLM response is treated as a failure and triggers fallback."""
    long = "x" * 1000
    fake_response = MagicMock()
    fake_response.content = "   "
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        result = await description_for_embedding(long, max_chars=100)

    assert result == f"{'x' * 100}..."


async def test_description_for_embedding_prompt_includes_max_chars() -> None:
    """The system prompt is templated with the requested ``max_chars`` value."""
    long = "x" * 1000
    fake_response = MagicMock()
    fake_response.content = "summary"
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("app.rag.text_cleaning.ChatOpenAI", return_value=fake_model):
        await description_for_embedding(long, max_chars=77)

    system_message = fake_model.ainvoke.call_args.args[0][0].content
    assert "77" in system_message
