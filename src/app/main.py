"""FastAPI application entry point.

Creates the ``FastAPI`` app instance, registers the lifespan context
manager, mounts all routers, and configures middleware.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from prometheus_fastapi_instrumentator import Instrumentator

from app.agent.graph import build_graph
from app.api.router import api_router
from app.config import get_settings
from app.db.session import close_pools, open_pools
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing
from app.services.qdrant_service import QdrantService
from app.services.valkey_service import ValkeyService

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown lifecycle.

    Startup order:
        1. Logging
        2. Tracing
        3. DB connection pools
        4. LangGraph checkpoint + store setup
        5. Agent graph compilation
        6. Qdrant collection bootstrap
        7. Valkey connection

    Shutdown:
        1. Valkey connection close
        2. Qdrant client close
        3. DB connection pools close
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

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant = QdrantService(settings)
    await qdrant.ensure_collection()
    app.state.qdrant = qdrant

    # ── Valkey ──────────────────────────────────────────────────────────────
    valkey = ValkeyService(settings.valkey_url)
    app.state.valkey = valkey

    logger.info("Startup complete")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutting down")
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

    # ── Prometheus metrics ──────────────────────────────────────────────────
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # ── Routers ─────────────────────────────────────────────────────────────
    app.include_router(api_router)

    return app


app = create_app()
