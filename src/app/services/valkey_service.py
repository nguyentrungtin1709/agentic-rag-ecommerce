"""Valkey service — thin async wrapper around redis-py for shared state.

Provides two logical databases:
- DB 0: rate limiting keys (managed by slowapi)
- DB 1: response cache keys (managed by fastapi-cache2)

This module only provides helpers for use-cases outside those two
libraries (e.g. idempotency keys, distributed locks, pub/sub).
"""

from __future__ import annotations

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class ValkeyService:
    """Async Redis client wrapper for miscellaneous shared-state operations.

    Args:
        url: Full Redis URL including DB index, e.g.
            ``redis://localhost:6379/0``.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Redis = Redis.from_url(url, decode_responses=True)

    @property
    def client(self) -> Redis:
        """Expose the raw async client for direct use by middlewares."""
        return self._client

    async def ping(self) -> bool:
        """Return ``True`` if the Valkey server is reachable.

        Used in the health-check endpoint.
        """
        try:
            return await self._client.ping()
        except Exception as exc:
            logger.warning("Valkey ping failed", error=str(exc))
            return False

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()
