"""Text cleaning for Saleor product descriptions.

The Saleor ``Product.description`` field can be one of:

- Plain text (some stores)
- Raw HTML (legacy imports)
- EditorJS JSON (most common; e.g.
  ``{"blocks": [{"type": "paragraph", "data": {"text": "..."}}]}``)

This module converts all three into a clean plain-text string used
for BOTH the embedding input and the ``metadata['description']``
field stored in Qdrant (FR-035, FR-035a).

Implementation uses only the Python standard library — no
BeautifulSoup or other HTML-parsing dependency, to keep the ingestion
hot path lean (the description is read once per product per reindex).

When the cleaned text exceeds ``settings.description_max_chars``, the
caller is expected to invoke :func:`description_for_embedding`, which
uses an LLM to produce a bounded summary suitable for embedding.
"""

from __future__ import annotations

import html
import json
import re

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings

logger = structlog.get_logger(__name__)


# Match any HTML tag — used by the stdlib-only tag stripper.
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Match runs of whitespace (spaces, tabs, newlines) for collapsing.
_WHITESPACE_RE = re.compile(r"\s+")


def clean_product_description(raw: str) -> str:
    """Return a plain-text version of a Saleor ``description`` field.

    Args:
        raw: The raw description value as stored by Saleor.  May be
            plain text, HTML, or an EditorJS JSON document.

    Returns:
        Cleaned plain-text string.  Empty string when the input has
        no extractable text.

    Notes:
        EditorJS block boundaries (already ``"\\n\\n"``-joined) are
        preserved so the embedding model sees sentence-grouped text.
        HTML/whitespace runs are collapsed into single spaces.
    """
    if not raw:
        return ""

    text = _try_extract_editorjs(raw)
    if text is None:
        text = _strip_html(raw)
        # For non-EditorJS inputs, collapse every whitespace run
        # (including newlines) into a single space.
        text = _WHITESPACE_RE.sub(" ", text).strip()
    else:
        # EditorJS already gives us block-level ``"\\n\\n"`` separators;
        # only collapse the inner whitespace inside each block.
        text = _collapse_internal_whitespace(text)

    text = html.unescape(text)
    return text


def _collapse_internal_whitespace(text: str) -> str:
    """Collapse whitespace runs within a string while preserving ``\\n\\n`` block boundaries.

    Args:
        text: A string containing ``"\\n\\n"``-separated blocks.

    Returns:
        The same string with each block's internal whitespace
        collapsed to single spaces, and outer whitespace stripped.
    """
    blocks = text.split("\n\n")
    cleaned_blocks = [_WHITESPACE_RE.sub(" ", block).strip() for block in blocks]
    return "\n\n".join(b for b in cleaned_blocks if b)


def _try_extract_editorjs(raw: str) -> str | None:
    """Try to extract text from an EditorJS JSON document.

    Args:
        raw: The raw description string.

    Returns:
        Concatenated text from ``paragraph`` and ``header`` blocks
        joined by ``"\\n\\n"``, or ``None`` if the input is not a
        valid EditorJS document.
    """
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return None
    try:
        doc = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None
    blocks = doc.get("blocks")
    if not isinstance(blocks, list):
        return None

    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type not in ("paragraph", "header"):
            continue
        data = block.get("data")
        if not isinstance(data, dict):
            continue
        text = data.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)

    return "\n\n".join(parts)


def _strip_html(raw: str) -> str:
    """Remove HTML tags from a string.

    The implementation is intentionally simple — Saleor descriptions
    are short, well-bounded inputs (no malicious or pathological
    payloads expected).  We do NOT parse with an HTML library; the
    regex stripper plus :func:`html.unescape` is sufficient.

    Each tag is replaced with a single space so that adjacent tokens
    across a tag boundary (e.g. ``"<b>breathable</b> fabric"``) do
    not run together.  Whitespace is then collapsed by the caller.

    Args:
        raw: The raw string, possibly containing HTML.

    Returns:
        The string with all HTML tags removed.
    """
    return _HTML_TAG_RE.sub(" ", raw)


async def description_for_embedding(
    cleaned: str,
    *,
    max_chars: int,
) -> str:
    """Apply the two-track description rule to the cleaned text.

    - If ``len(cleaned) <= max_chars``, returns the cleaned text
      unchanged — no LLM call is made.
    - Otherwise, asks the configured ``settings.summarize_model`` to
      produce a summary of at most ``max_chars`` characters.

    On any LLM error, log a warning and fall back to
    ``cleaned[:max_chars] + "..."`` so the embedding pipeline still
    produces a useful (if truncated) vector.

    Args:
        cleaned: The cleaned plain-text description.
        max_chars: The embedding model's optimal input length cap.

    Returns:
        The text to embed — either the cleaned text or a summary.
    """
    if len(cleaned) <= max_chars:
        return cleaned

    settings = Settings()  # type: ignore[call-arg]
    try:
        from pathlib import Path

        prompt_path = Path("src/app/agent/prompts/description_summarize_system.md")
        system_prompt = prompt_path.read_text().format(max_chars=max_chars)
    except (FileNotFoundError, OSError) as exc:
        logger.warning("summarize_prompt_load_failed", error=str(exc))
        system_prompt = (
            "You are a product description summarizer. "
            f"Produce a summary of at most {max_chars} characters."
        )

    try:
        model = ChatOpenAI(model=settings.summarize_model, temperature=0)
        response = await model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=cleaned),
            ]
        )
        summary = str(response.content).strip()
        if not summary:
            raise ValueError("LLM returned an empty summary")
        # Bound the summary to max_chars defensively.
        return summary[:max_chars]
    except Exception as exc:  # broad catch: any LLM/SDK error -> fallback
        logger.warning(
            "description_summarization_failed_using_truncation",
            error=str(exc),
            max_chars=max_chars,
        )
        return f"{cleaned[:max_chars]}..."
