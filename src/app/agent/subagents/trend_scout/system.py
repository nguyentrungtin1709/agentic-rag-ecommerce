"""Dynamic SystemMessage builder for the TrendScout subagent.

The wrapper node (``run_trend_scout``) calls this helper to compose
a ``SystemMessage`` content string from the base prompt plus
injected parent-state context. The composed string is prepended as
``messages[0]`` of the subagent invocation, so the LLM sees the
full context (conversation summary, user profile, retrieved
products, image output note) in its message history.

The base prompt lives in
``src/app/agent/prompts/trend_scout_system.md`` and is loaded once
at import time via ``app.agent.prompts.load_prompt``.
"""

from __future__ import annotations

import json

from app.agent.prompts import load_prompt


def _build_trend_scout_system(
    summary: str,
    user_profile: dict | None,
    retrieved_products: list[dict],
    generate_image: bool,
) -> str:
    """Compose the TrendScout ``SystemMessage`` content.

    Each context section is appended only when the corresponding
    field is non-empty so the LLM does not see blank sections.

    Args:
        summary: Accumulated conversation summary from
            ``state["summary"]``; empty string when none.
        user_profile: Serialized ``UserProfile`` dict from
            ``state["user_profile"]``; ``None`` when not yet loaded.
        retrieved_products: List of product payload dicts (may be
            empty).
        generate_image: ``True`` to instruct the agent to include
            an ``image_prompt`` in its response.

    Returns:
        Full ``SystemMessage`` content string.
    """
    base = load_prompt("trend_scout_system")
    parts: list[str] = [base]
    if summary:
        parts.append(f"\n## Conversation history summary\n{summary}\n")
    if user_profile:
        parts.append(f"\n## User preferences\n{json.dumps(user_profile, ensure_ascii=False)}\n")
    if retrieved_products:
        names = [p.get("name", p.get("product_id", "?")) for p in retrieved_products]
        parts.append(f"\n## Products already recommended\n{', '.join(names)}\n")
    if generate_image:
        parts.append(
            "\n## Output note\nInclude exactly one text-to-image prompt in your response.\n"
        )
    return "\n".join(parts)
