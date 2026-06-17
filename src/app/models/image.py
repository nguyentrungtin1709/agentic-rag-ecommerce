"""Domain model for a generated image record."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GeneratedImage(BaseModel):
    """Record of an AI-generated image stored in S3.

    Attributes:
        id: UUID primary key (maps to ``generated_images.id``).
        thread_id: Foreign key to the parent thread.
        user_id: Saleor user ID for quota tracking.
        prompt: The prompt used to generate the image.
        s3_key: S3 object key (path within the bucket).
        s3_url: Public S3 URL of the image.
        model: Model identifier used for generation (e.g. ``"gpt-image-2"``).
        request_message_id: ``HumanMessage.id`` of the turn that triggered
            generation; links the image to the correct turn in thread
            history (FR-020, FR-051).  ``None`` for legacy records.
        created_at: Generation timestamp.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    thread_id: uuid.UUID
    user_id: str
    prompt: str
    s3_key: str
    s3_url: str
    model: str
    request_message_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
