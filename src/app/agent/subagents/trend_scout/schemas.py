"""Pydantic schemas for the TrendScout subagent.

``TrendScoutOutput`` is passed to ``create_agent(response_format=...)``
as the structured output schema. The agent emits one of these as
its final message, and the wrapper extracts it from
``result["structured_response"]``.

The fields are intentionally nullable so a single schema can
represent both a successful research report and a graceful
"no synthesis was possible" outcome (D11.6). The orchestrator
node handles both shapes uniformly by routing to ``synthesize``
in either case.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TrendScoutOutput(BaseModel):
    """Structured output of the TrendScout agent.

    Attributes:
        trend_summary: 2-3 sentence trend report covering top
            themes, color palettes, and relevant styles. ``None``
            when both search tools failed and no synthesis was
            possible (graceful degradation per D11.6).
        image_prompt: Exactly one text-to-image prompt
            (DALL-E compatible) when ``generate_image`` was
            ``True`` and the query is design-related; ``None``
            otherwise. The ``ImageGenerationNode`` (Phase 13)
            reads this value to drive DALL-E.
    """

    trend_summary: str | None = Field(
        description=(
            "2-3 sentence trend report covering top POD design themes, "
            "color palettes, and relevant styles. Null when no "
            "synthesis was possible."
        ),
    )
    image_prompt: str | None = Field(
        default=None,
        description=(
            "One text-to-image prompt when generate_image was True and "
            "the query is design-related. Null otherwise."
        ),
    )
