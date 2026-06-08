"""Prompt template registry for the POD Stylist agent nodes.

All LLM prompt templates are stored as ``.md`` files in this package
directory (NFR-025).  Every template is read from disk exactly once at
module import time and cached in ``_cache``.  Subsequent calls to
``load_prompt`` perform only a dictionary lookup — no I/O.

If a ``.md`` file is missing the ``FileNotFoundError`` surfaces at startup
(fail-fast) rather than at runtime when the first request hits the node.

Usage::

    from app.agent.prompts import load_prompt

    system_text = load_prompt("orchestrator_system")
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

# Build the cache at import time.  Each key is the file stem (no extension).
_cache: dict[str, str] = {
    path.stem: path.read_text(encoding="utf-8") for path in sorted(_PROMPTS_DIR.glob("*.md"))
}


def load_prompt(name: str) -> str:
    """Return a cached prompt template by name (without the ``.md`` extension).

    Args:
        name: Template name, e.g. ``"orchestrator_system"``.

    Returns:
        Full text content of the template.

    Raises:
        FileNotFoundError: If ``name`` does not match any registered template.
    """
    try:
        return _cache[name]
    except KeyError:
        raise FileNotFoundError(
            f"Prompt template '{name}.md' not found in {_PROMPTS_DIR}"
        ) from None
