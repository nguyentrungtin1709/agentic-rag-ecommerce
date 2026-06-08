"""Domain model for a long-term user style profile."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """Long-term user style preferences stored in LangGraph AsyncPostgresStore.

    The namespace used for storage is ``("profiles", user_id)``.
    The model does not carry ``user_id`` — the key is the namespace itself.

    Attributes:
        age_group: Broad age bracket inferred from conversation context
            (e.g. ``"teen"``, ``"adult"``, ``"senior"``).
        style_preferences: Style descriptors accumulated over sessions
            (e.g. ``["minimalist", "streetwear", "vintage"]``).
        product_interests: Product types the user has shown interest in
            (e.g. ``["t-shirt", "mug", "canvas"]``).
        occasion_context: Most recently mentioned occasion or use case
            (e.g. ``"Christmas gift"``, ``"birthday"``).
        recipient_context: Who the product is intended for when mentioned
            (e.g. ``"mom"``, ``"friend"``, ``"self"``).
        budget_range: Free-text budget signal extracted from the latest
            message (e.g. ``"under 200k"``, ``"cheap"``).
    """

    age_group: str | None = None
    style_preferences: list[str] = Field(default_factory=list)
    product_interests: list[str] = Field(default_factory=list)
    occasion_context: str | None = None
    recipient_context: str | None = None
    budget_range: str | None = None
