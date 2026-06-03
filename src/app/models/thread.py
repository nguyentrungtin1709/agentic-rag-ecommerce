"""Domain model for a chat thread."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class Thread(BaseModel):
    """A chat session between a user and the POD Stylist agent.

    Attributes:
        id: UUID primary key (maps to ``threads.id``).
        user_id: Saleor user ID from the JWT ``sub`` claim.
        title: Auto-generated thread title (null until first exchange).
        status: Lifecycle state — ``idle``, ``busy``, or ``deleting``.
        title_generated: ``True`` once the title is finalised; prevents
            further title updates (FR-024).
        title_generation_attempts: Number of LLM attempts made so far;
            capped at ``TITLE_GENERATION_MAX_ATTEMPTS`` (FR-023).
        created_at: Thread creation timestamp.
        updated_at: Last-modified timestamp.
        last_activity_at: Updated on every chat run; used by the nightly
            expiry job (FR-018).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: str
    title: str | None = None
    status: str = "idle"
    title_generated: bool = False
    title_generation_attempts: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
