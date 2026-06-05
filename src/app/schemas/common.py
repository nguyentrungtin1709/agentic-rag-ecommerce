"""Common/shared response schemas used across multiple API endpoints."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    """Response schema for health-check and readiness-check endpoints."""

    status: str
    checks: dict[str, bool] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response returned on 4xx/5xx failures."""

    code: str
    message: str


class PaginatedResponse[T](BaseModel):
    """Generic cursor-based paginated response (FR-015, FR-019)."""

    items: list[T]
    next_cursor: str | None = None


class CursorPage[T](BaseModel):
    """Alias of ``PaginatedResponse`` for endpoints that use UUID cursors."""

    items: list[T]
    next_cursor: str | None = None
