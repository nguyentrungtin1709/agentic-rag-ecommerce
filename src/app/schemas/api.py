"""Backward-compatible re-exports from the split schema modules.

The canonical home for each schema is now:
  - ``schemas.common``  — HealthResponse, PaginatedResponse, ErrorResponse, CursorPage
  - ``schemas.thread``  — CreateThreadRequest, ThreadResponse, ThreadListResponse
  - ``schemas.chat``    — ChatRequest, ChatChunk, UsagePayload, DonePayload
  - ``schemas.webhook`` — SaleorWebhookPayload

New code should import directly from the canonical modules above.
This file exists only for backward compatibility during the Phase 4 → 6 transition.
"""

from __future__ import annotations

from app.schemas.chat import ChatChunk, ChatRequest, DonePayload, ProductItem, UsagePayload
from app.schemas.common import CursorPage, ErrorResponse, HealthResponse, PaginatedResponse
from app.schemas.thread import CreateThreadRequest, ThreadListResponse, ThreadResponse
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
    "PaginatedResponse",
    "ProductItem",
    "SaleorWebhookPayload",
    "ThreadListResponse",
    "ThreadResponse",
    "UsagePayload",
    "WebhookEvent",
]
