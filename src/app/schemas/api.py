"""Backward-compatible re-exports from the split schema modules.

The canonical home for each schema is now:
  - ``schemas.common``   — HealthResponse, PaginatedResponse, ErrorResponse, CursorPage
  - ``schemas.thread``   — CreateThreadRequest, ThreadResponse, ThreadListResponse,
                           HistoryImageItem, HistoryMessage, ThreadHistoryResponse
  - ``schemas.chat``     — ChatRequest, ChatChunk, UsagePayload, DonePayload
  - ``schemas.webhook``  — SaleorWebhookPayload
  - ``schemas.profile``  — UserProfileResponse, ProfileEnvelope (Phase 9)
  - ``schemas.ingestion``— IngestionJobSummary, IngestionJobListResponse (Phase 9)

New code should import directly from the canonical modules above.
This file exists only for backward compatibility during the Phase 4 → 9 transition.
"""

from __future__ import annotations

from app.schemas.chat import ChatChunk, ChatRequest, DonePayload, ProductItem, UsagePayload
from app.schemas.common import CursorPage, ErrorResponse, HealthResponse, PaginatedResponse
from app.schemas.ingestion import IngestionJobListResponse, IngestionJobSummary
from app.schemas.profile import ProfileEnvelope, UserProfileResponse
from app.schemas.thread import (
    CreateThreadRequest,
    HistoryImageItem,
    HistoryMessage,
    ThreadHistoryResponse,
    ThreadListResponse,
    ThreadResponse,
)
from app.schemas.webhook import SaleorWebhookPayload

# Legacy alias kept for any existing references.
WebhookEvent = SaleorWebhookPayload

__all__ = [
    "ChatChunk",
    "ChatRequest",
    "CreateThreadRequest",
    "CursorPage",
    "DonePayload",
    "ErrorResponse",
    "HealthResponse",
    "HistoryImageItem",
    "HistoryMessage",
    "IngestionJobListResponse",
    "IngestionJobSummary",
    "PaginatedResponse",
    "ProductItem",
    "ProfileEnvelope",
    "SaleorWebhookPayload",
    "ThreadHistoryResponse",
    "ThreadListResponse",
    "ThreadResponse",
    "UsagePayload",
    "UserProfileResponse",
    "WebhookEvent",
]
