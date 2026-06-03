"""Central API router — aggregates all sub-routers.

Import this in ``main.py`` and include it on the ``FastAPI`` app.
Each sub-router is registered with its own prefix and tags.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.health import router as health_router

# Sub-routers are registered here.  Chat and thread routers will be added
# in subsequent implementation phases.
api_router = APIRouter()

api_router.include_router(health_router)
