"""Pydantic schemas for the user-profile endpoint (Phase 9).

Provides :class:`UserProfileResponse` (the API-shaped view of
:class:`app.models.profile.UserProfile`) and :class:`ProfileEnvelope`
(the response wrapper carrying the store-level ``updated_at``
freshness signal — see ADR D9.3).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserProfileResponse(BaseModel):
    """The ``UserProfile`` fields exposed via the API (FR-032).

    Re-declares the same fields as
    :class:`app.models.profile.UserProfile` with
    ``from_attributes=False`` so the API layer is decoupled from
    the storage shape and can evolve independently (e.g. add
    field aliases, drop fields, or change types without
    touching the domain model).

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

    model_config = ConfigDict(from_attributes=False)

    age_group: str | None = None
    style_preferences: list[str] = Field(default_factory=list)
    product_interests: list[str] = Field(default_factory=list)
    occasion_context: str | None = None
    recipient_context: str | None = None
    budget_range: str | None = None


class ProfileEnvelope(BaseModel):
    """Envelope around the profile so the response carries a freshness signal (D9.3).

    The profiler node writes a fresh row on every agent turn, so
    ``updated_at`` tells the admin operator how stale the displayed
    profile is.  Sourced from
    :class:`langgraph.store.base.Item.updated_at` which is populated
    by ``AsyncPostgresStore.aput`` on every write.

    Attributes:
        profile: The user profile fields (see
            :class:`UserProfileResponse`).
        updated_at: UTC timestamp of the most recent profile write.
    """

    profile: UserProfileResponse
    updated_at: datetime


__all__ = ["ProfileEnvelope", "UserProfileResponse"]
