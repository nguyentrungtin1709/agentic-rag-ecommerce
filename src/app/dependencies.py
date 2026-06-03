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

from app.auth.jwt_verifier import verify_token
from app.config import Settings, get_settings
from app.db.session import get_asyncpg_pool
from app.repositories.image_repo import ImageRepository
from app.repositories.thread_repo import ThreadRepository
from app.services.qdrant_service import QdrantService
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
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
    settings: SettingsDep,
) -> dict:
    """Validate the Bearer JWT and return the decoded claims.

    Args:
        credentials: Extracted by ``HTTPBearer`` from the Authorization header.
        settings: Application settings for ``saleor_url``.

    Returns:
        Decoded JWT payload dict.

    Raises:
        HTTPException: 401 if the token is invalid or expired.
    """
    try:
        return await verify_token(credentials.credentials, settings.saleor_url)
    except jwt.PyJWTError as exc:
        logger.warning("JWT verification failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc


CurrentUserDep = Annotated[dict, Depends(get_current_user)]


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


ThreadRepoDep = Annotated[ThreadRepository, Depends(get_thread_repo)]
ImageRepoDep = Annotated[ImageRepository, Depends(get_image_repo)]


# ── Services ──────────────────────────────────────────────────────────────────


def get_qdrant_service(request: Request) -> QdrantService:
    """Return the ``QdrantService`` singleton from app state."""
    return request.app.state.qdrant


def get_valkey_service(request: Request) -> ValkeyService:
    """Return the ``ValkeyService`` singleton from app state."""
    return request.app.state.valkey


QdrantDep = Annotated[QdrantService, Depends(get_qdrant_service)]
ValkeyDep = Annotated[ValkeyService, Depends(get_valkey_service)]
