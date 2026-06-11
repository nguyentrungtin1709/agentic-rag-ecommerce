"""Health-check endpoints.

Two probes are provided as per FR-105 and FR-106:

- ``GET /health``  — Liveness probe.  Returns 200 as long as the process
  is running.  No external dependency checks are performed.  Used by
  Docker / Kubernetes to decide whether to restart the container.

- ``GET /ready``   — Readiness probe.  Checks PostgreSQL, Qdrant, and
  Valkey connectivity before returning 200.  Returns 503 when any
  dependency is unreachable.  Used by load balancers and Docker
  Compose ``condition: service_healthy`` to gate downstream services.
"""

from __future__ import annotations

import asyncpg
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.db.session import get_asyncpg_pool
from app.rate_limit import get_limiter
from app.schemas.common import HealthResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

# Resolve the limiter once at import time. The singleton pattern in
# ``app.rate_limit`` ensures tests that clear the limiter can rebuild
# it before the next decorator evaluation.
_limiter = get_limiter()


@router.get(
    "/health",
    summary="Liveness probe",
    response_model=HealthResponse,
)
@_limiter.exempt
async def health() -> JSONResponse:
    """Liveness probe — returns 200 when the service process is running.

    No external dependency checks are performed.  This endpoint must
    always return 200 as long as the FastAPI process is alive (FR-105).
    Exempt from rate limiting per FR-094.
    """
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=HealthResponse(status="ok").model_dump(),
    )


@router.get(
    "/ready",
    summary="Readiness probe",
    response_model=HealthResponse,
)
@_limiter.exempt
async def ready(request: Request) -> JSONResponse:
    """Readiness probe — checks all external infrastructure dependencies.

    Returns 200 when PostgreSQL, Qdrant, and Valkey are all reachable.
    Returns 503 when any dependency is down, along with a ``checks``
    dict identifying which service(s) failed (FR-106). Exempt from
    rate limiting per FR-094.
    """
    checks: dict[str, bool] = {}

    # PostgreSQL
    try:
        pool: asyncpg.Pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception as exc:
        logger.warning("Readiness check: postgres failed", error=str(exc))
        checks["postgres"] = False

    # Qdrant
    try:
        qdrant = request.app.state.qdrant
        await qdrant.client.get_collection(qdrant.collection_name)
        checks["qdrant"] = True
    except Exception as exc:
        logger.warning("Readiness check: qdrant failed", error=str(exc))
        checks["qdrant"] = False

    # Valkey
    checks["valkey"] = await request.app.state.valkey.ping()

    all_ready = all(checks.values())
    http_status = status.HTTP_200_OK if all_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content=HealthResponse(
            status="ok" if all_ready else "degraded",
            checks=checks,
        ).model_dump(),
    )
