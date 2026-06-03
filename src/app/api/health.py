"""Health-check endpoint.

Returns the status of all critical infrastructure dependencies.  Used by
Docker health-checks, load-balancer probes, and Grafana dashboards.
"""

from __future__ import annotations

import asyncpg
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.db.session import get_asyncpg_pool
from app.schemas.api import HealthResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Infrastructure health check",
)
async def health(request: Request) -> JSONResponse:
    """Check connectivity to PostgreSQL, Qdrant, and Valkey.

    Returns 200 when all checks pass, 503 when any check fails.
    The response body always contains the ``checks`` dict so callers
    can identify which dependency is down.
    """
    checks: dict[str, bool] = {}

    # PostgreSQL
    try:
        pool: asyncpg.Pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception as exc:
        logger.warning("Health check: postgres failed", error=str(exc))
        checks["postgres"] = False

    # Qdrant
    try:
        qdrant = request.app.state.qdrant
        await qdrant.client.get_collection(qdrant.collection_name)
        checks["qdrant"] = True
    except Exception as exc:
        logger.warning("Health check: qdrant failed", error=str(exc))
        checks["qdrant"] = False

    # Valkey
    checks["valkey"] = await request.app.state.valkey.ping()

    all_healthy = all(checks.values())
    http_status = status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content=HealthResponse(
            status="ok" if all_healthy else "degraded",
            checks=checks,
        ).model_dump(),
    )
