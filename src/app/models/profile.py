"""Domain model for a long-term user style profile."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """Long-term user style preferences stored in LangGraph AsyncPostgresStore.

    The namespace used for storage is ``("profiles", user_id)``.

    Attributes:
        user_id: Saleor user ID extracted from the JWT ``sub`` claim.
        preferred_styles: Style descriptors the user has shown interest in
            (e.g. ``["minimalist", "streetwear"]``).
        favourite_colours: Colour names or hex codes the user prefers.
        disliked_themes: Themes or styles the user has explicitly rejected.
        last_updated: ISO-8601 timestamp of the last profile write.
    """

    user_id: str
    preferred_styles: list[str] = Field(default_factory=list)
    favourite_colours: list[str] = Field(default_factory=list)
    disliked_themes: list[str] = Field(default_factory=list)
    last_updated: str = ""
