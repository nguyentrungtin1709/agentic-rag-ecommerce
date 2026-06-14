"""FastAPI dependency providers.

Each function here is a FastAPI dependency that resolves a request-scoped
or singleton resource.  Singletons are stored on ``app.state`` and set
during the ``lifespan`` context manager in ``main.py``.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.pregel import Pregel
from openai import AsyncOpenAI

from app.auth.jwt_verifier import verify_token
from app.config import Settings, get_settings
from app.db.session import get_asyncpg_pool
from app.repositories.image_repo import ImageRepository
from app.repositories.ingestion_repo import (
    IngestionBatchRepository,
    IngestionJobRepository,
)
from app.repositories.thread_repo import ThreadRepository
from app.services.qdrant_service import QdrantService
from app.services.s3_service import S3Service
from app.services.valkey_service import ValkeyService

logger = structlog.get_logger(__name__)

_bearer_scheme = HTTPBearer(auto_error=True)


# ── Config ──────────────────────────────────────────────────────────────────


def get_app_settings() -> Settings:
    """Return the cached application settings."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


# ── Auth ─────────────────────────────────────────────────────────────────────


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
    settings: SettingsDep,
) -> dict:
    """Validate the Bearer JWT and return the decoded claims.

    The ``iss`` claim is verified against ``settings.saleor_jwt_issuer``
    when set, falling back to ``settings.saleor_url`` for backwards
    compatibility.  This split lets the application fetch the JWKS from
    a Docker-internal alias (e.g. ``host.docker.internal``) while still
    validating the user-visible ``iss`` URL set by Saleor.

    The verified claims are also stashed on ``request.state.current_user``
    so downstream cache key builders (e.g. ``thread_list_key_builder``)
    can read the authenticated user without re-parsing the token
    (D8.7 / Q1).

    Args:
        request: Inbound FastAPI request — used to expose the claims
            on ``request.state`` for downstream consumers.
        credentials: Extracted by ``HTTPBearer`` from the Authorization header.
        settings: Application settings for ``saleor_url`` and JWT issuer.

    Returns:
        Decoded JWT payload dict.

    Raises:
        HTTPException: 401 if the token is invalid or expired.
    """
    try:
        claims = await verify_token(
            credentials.credentials,
            settings.saleor_url,
            issuer=settings.saleor_jwt_issuer or None,
        )
    except jwt.PyJWTError as exc:
        logger.warning("JWT verification failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc
    request.state.current_user = claims
    return claims


CurrentUserDep = Annotated[dict, Depends(get_current_user)]


async def get_current_admin(current_user: CurrentUserDep) -> dict:
    """Require ``is_staff: true`` in the verified JWT claims.

    Used by admin-only endpoints: ``GET /users/{id}/profile``,
    ``POST /admin/reindex``, ``GET /admin/threads`` (FR-085).

    Args:
        current_user: Decoded JWT payload from ``get_current_user``.

    Returns:
        The same JWT payload dict if the user is a staff member.

    Raises:
        HTTPException: 403 Forbidden if ``is_staff`` is missing or False.
    """
    if not current_user.get("is_staff"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


AdminDep = Annotated[dict, Depends(get_current_admin)]


# ── Database pool ────────────────────────────────────────────────────────────


def get_db_pool() -> asyncpg.Pool:
    """Return the active asyncpg connection pool."""
    return get_asyncpg_pool()


PoolDep = Annotated[asyncpg.Pool, Depends(get_db_pool)]


# ── Repositories ─────────────────────────────────────────────────────────────


def get_thread_repo(pool: PoolDep) -> ThreadRepository:
    """Construct a ``ThreadRepository`` scoped to the current request."""
    return ThreadRepository(pool)


def get_image_repo(pool: PoolDep) -> ImageRepository:
    """Construct an ``ImageRepository`` scoped to the current request."""
    return ImageRepository(pool)


def get_ingestion_job_repo(pool: PoolDep) -> IngestionJobRepository:
    """Construct an ``IngestionJobRepository`` scoped to the current request.

    Used by the admin ``POST /admin/reindex`` and
    ``GET /admin/reindex/{job_id}`` endpoints (Phase 6).  Celery
    worker tasks construct this class directly via
    :func:`app.db.session.get_asyncpg_pool` because they run outside
    the FastAPI app context where ``Depends`` is unavailable.
    """
    return IngestionJobRepository(pool)


def get_ingestion_batch_repo(pool: PoolDep) -> IngestionBatchRepository:
    """Construct an ``IngestionBatchRepository`` scoped to the current request.

    Used by the admin ``GET /admin/reindex/{job_id}`` endpoint to list
    per-batch status.  Celery worker tasks construct this class
    directly via :func:`app.db.session.get_asyncpg_pool`.
    """
    return IngestionBatchRepository(pool)


ThreadRepoDep = Annotated[ThreadRepository, Depends(get_thread_repo)]
ImageRepoDep = Annotated[ImageRepository, Depends(get_image_repo)]
IngestionJobRepoDep = Annotated[IngestionJobRepository, Depends(get_ingestion_job_repo)]
IngestionBatchRepoDep = Annotated[IngestionBatchRepository, Depends(get_ingestion_batch_repo)]


# ── Services ──────────────────────────────────────────────────────────────────


def get_qdrant_service(request: Request) -> QdrantService:
    """Return the ``QdrantService`` singleton from app state."""
    return request.app.state.qdrant


def get_valkey_service(request: Request) -> ValkeyService:
    """Return the ``ValkeyService`` singleton from app state."""
    return request.app.state.valkey


def get_s3_service(request: Request) -> S3Service:
    """Return the ``S3Service`` singleton from app state (Phase 5.8)."""
    return request.app.state.s3


def get_openai_client(request: Request) -> AsyncOpenAI:
    """Return the ``AsyncOpenAI`` singleton from app state (Phase 5.8).

    Consumed by the image-generation node (Phase 13) for DALL-E.
    Stored as a bare ``AsyncOpenAI``; no wrapper class (see
    ``history/5_0_0_SHARED_RESOURCE_INJECTION.md`` ADR D5.7).
    """
    return request.app.state.openai


def get_graph(request: Request) -> Pregel:
    """Return the compiled LangGraph state graph from app state (Phase 8).

    Set in ``app.main.lifespan`` after the LangGraph checkpointer
    and store are ready.  Consumed by ``GET /api/v1/threads/{id}/history``
    to read the latest ``StateSnapshot`` for a thread.
    """
    return request.app.state.graph


QdrantDep = Annotated[QdrantService, Depends(get_qdrant_service)]
ValkeyDep = Annotated[ValkeyService, Depends(get_valkey_service)]
S3Dep = Annotated[S3Service, Depends(get_s3_service)]
OpenAIDep = Annotated[AsyncOpenAI, Depends(get_openai_client)]
GraphDep = Annotated[Pregel, Depends(get_graph)]
