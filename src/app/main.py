"""FastAPI application entry point.

Creates the ``FastAPI`` app instance, registers the lifespan context
manager, mounts all routers, and configures middleware.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from openai import AsyncOpenAI
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import from_url as redis_from_url
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.agent.graph import build_graph
from app.api.router import api_router
from app.config import get_settings
from app.db.session import close_pools, open_pools
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing
from app.rate_limit import get_limiter
from app.services.qdrant_service import QdrantService
from app.services.s3_service import S3Service
from app.services.valkey_service import ValkeyService

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown lifecycle.

    Startup order (5.7 + 5.8):
        1. Logging
        2. Tracing
        3. DB connection pools
        4. LangGraph checkpoint + store setup
        5. Agent graph compilation
        6. Qdrant collection bootstrap
        7. Valkey connection
        8. S3 bucket ensure (fail fast if missing)
        9. OpenAI client (for Phase 13 DALL-E)

    Shutdown order (reverse of construction):
        1. OpenAI client close
        2. S3 client close
        3. Valkey / cache Redis close
        4. Qdrant client close
        5. DB connection pools close
    """
    settings = get_settings()

    configure_logging(settings.log_level)
    configure_tracing(settings)

    logger.info("Starting up", app="agentic-rag-ecommerce")

    # ── DB pools ────────────────────────────────────────────────────────────
    await open_pools(settings.database_url)
    from app.db.session import get_psycopg_pool

    psycopg_pool = get_psycopg_pool()

    # ── LangGraph persistence ─────────────────────────────────────────────
    checkpointer = AsyncPostgresSaver(psycopg_pool)
    store = AsyncPostgresStore(psycopg_pool)
    await checkpointer.setup()
    await store.setup()

    # ── Agent graph ─────────────────────────────────────────────────────────
    app.state.graph = build_graph(checkpointer=checkpointer, store=store)
    # Phase 9: expose the store on app state so handlers can read long-term
    # user state (profiler namespace ``("profiles", user_id)``) without
    # rebuilding a second ``AsyncPostgresStore`` instance. See
    # ``history/9_0_0_PROFILE_AND_ADMIN_API.md`` decision D9.1.
    app.state.store = store

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant = QdrantService(settings)
    await qdrant.ensure_collection()
    app.state.qdrant = qdrant

    # ── Valkey (rate-limit storage + slowapi key lookup) ───────────────────
    valkey = ValkeyService(settings.valkey_url)
    app.state.valkey = valkey

    # ── fastapi-cache2 (Phase 5.2 — thread-list cache) ─────────────────────
    # Separate Redis logical DB (valkey_cache_url = valkey_url/1) so the
    # rate-limit and cache namespaces do not collide. The backend expects
    # raw bytes, so we explicitly set decode_responses=False.
    cache_redis = redis_from_url(settings.valkey_cache_url, decode_responses=False)
    FastAPICache.init(RedisBackend(cache_redis), prefix="fastapi-cache")
    app.state.cache_redis = cache_redis

    # ── S3 (5.8 NEW — fail fast on missing bucket) ────────────────────────
    s3 = S3Service(settings)
    await s3.ensure_bucket()
    app.state.s3 = s3

    # ── OpenAI client (5.8 NEW — for Phase 13 DALL-E) ────────────────────
    # Stored as a bare AsyncOpenAI; consumed by generate_image node
    # via OpenAIDep. Not used by LangChain ChatOpenAI nodes (see ADR).
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    app.state.openai = openai_client

    logger.info("Startup complete")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutting down")
    await openai_client.close()
    await s3.close()
    await cache_redis.aclose()
    await valkey.close()
    await qdrant.close()
    await close_pools()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Returns:
        A fully configured ``FastAPI`` instance.
    """
    app = FastAPI(
        title="POD Stylist — Agentic RAG E-Commerce",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Rate limiting (slowapi, Phase 5.1) ─────────────────────────────────
    # Limiter is keyed on JWT sub claim (via get_jwt_user_id_or_ip in
    # app.rate_limit). Decorators on individual routes are applied in
    # the route modules; the global state is wired here.
    limiter = get_limiter()
    app.state.limiter = limiter

    # Wrap slowapi's handler so its signature matches FastAPI's generic
    # ``ExceptionHandler`` type.  We accept the wider ``Exception`` and
    # rely on slowapi to raise ``RateLimitExceeded`` only.
    async def _on_rate_limit_exceeded(request: Request, exc: Exception) -> Response:
        return _rate_limit_exceeded_handler(request, exc)  # type: ignore[arg-type]

    app.add_exception_handler(RateLimitExceeded, _on_rate_limit_exceeded)
    app.add_middleware(SlowAPIMiddleware)

    # ── Prometheus metrics ──────────────────────────────────────────────────
    # /metrics is mounted before the rate limiter sees it, so the
    # Instrumentator's middleware is exempt from rate limiting by
    # design (it runs inside Starlette's routing and is not a
    # registered slowapi route).
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # ── Routers ─────────────────────────────────────────────────────────────
    app.include_router(api_router)

    return app


app = create_app()
