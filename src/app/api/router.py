"""Central API router — aggregates all sub-routers.

Import this in ``main.py`` and include it on the ``FastAPI`` app.

Mount structure:
  /health                           — liveness probe
  /ready                            — readiness probe
  /api/v1/threads/*                 — thread CRUD + history
  /api/v1/threads/{id}/runs/stream  — SSE chat endpoint
  /api/v1/users/{id}/profile        — user profile (admin only)
  /api/v1/admin/*                   — admin operations
  /webhooks/saleor                  — Saleor webhook receiver
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.profile import router as profile_router
from app.api.threads import router as threads_router
from app.api.webhooks import router as webhooks_router

api_router = APIRouter()

# ── Infrastructure probes (no prefix) ─────────────────────────────────────
api_router.include_router(health_router)

# ── API v1 ─────────────────────────────────────────────────────────────────
_v1 = APIRouter(prefix="/api/v1")

_v1.include_router(threads_router, prefix="/threads", tags=["threads"])
_v1.include_router(chat_router, prefix="/threads", tags=["chat"])
_v1.include_router(profile_router, prefix="/users", tags=["profile"])
_v1.include_router(admin_router, prefix="/admin", tags=["admin"])

api_router.include_router(_v1)

# ── Webhooks (no /api/v1 prefix per spec) ──────────────────────────────────
api_router.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
