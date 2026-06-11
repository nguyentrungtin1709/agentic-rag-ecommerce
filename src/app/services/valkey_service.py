"""Valkey service — thin async wrapper around redis-py for shared state.

Provides two logical databases:
- DB 0: rate limiting keys (managed by slowapi)
- DB 1: response cache keys (managed by fastapi-cache2)

This module only provides helpers for use-cases outside those two
libraries (e.g. idempotency keys, distributed locks, pub/sub, daily
image quota counters).
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

    async def get(self, key: str) -> str | None:
        """Return the value at ``key`` or ``None`` if it does not exist.

        The client is constructed with ``decode_responses=True`` so the
        underlying redis-py call always returns ``str | None`` at
        runtime, but the static type signature still widens to
        ``bytes | str | None`` because of redis-py's generic
        ``Redis[bytes]`` typing.  The runtime check below narrows
        the value to ``str`` for the contract promised by this
        service.

        Args:
            key: The Redis key to read.

        Returns:
            The stored string value, or ``None`` if the key is missing.
        """
        raw: bytes | str | None = await self._client.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return raw

    async def set(
        self,
        key: str,
        value: str,
        ttl: int | None = None,
    ) -> None:
        """Set ``key`` to ``value``, optionally with a TTL in seconds.

        Args:
            key: The Redis key to write.
            value: The string value to store.
            ttl: Optional expiry in seconds.  When ``None``, the key
                has no TTL (use with care — it will live forever).
        """
        if ttl is None:
            await self._client.set(key, value)
        else:
            await self._client.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        """Delete ``key``.  No error if the key does not exist.

        Args:
            key: The Redis key to remove.
        """
        await self._client.delete(key)

    async def delete_pattern(self, pattern: str) -> int:
        """Delete every key matching ``pattern`` (SCAN + DEL).

        Uses ``SCAN`` to avoid blocking the server on large keyspaces
        (the ``KEYS`` command is ``O(N)`` and stops the event loop).

        Args:
            pattern: Glob-style pattern, e.g. ``threads:user-123:*``.

        Returns:
            Number of keys deleted.
        """
        deleted = 0
        async for key in self._client.scan_iter(match=pattern, count=500):
            deleted += await self._client.delete(key)
        if deleted:
            logger.info("Valkey pattern delete", pattern=pattern, deleted=deleted)
        return deleted

    async def increment_quota(self, key: str, ttl: int = 86400) -> int:
        """Atomically ``INCR`` a counter, setting TTL on the first hit.

        Used for daily image quota (FR-052).  ``INCR`` is atomic; the
        ``EXPIRE`` is set only on the first hit of each key (when
        ``INCR`` returns ``1``), so the TTL window does not slide
        forward on subsequent writes within the same period.

        Args:
            key: The Redis key to increment.
            ttl: TTL in seconds applied on the first hit (default
                24h — the standard daily-quota window).

        Returns:
            The new counter value after the increment.
        """
        new_value = await self._client.incr(key)
        if new_value == 1:
            await self._client.expire(key, ttl)
        return new_value

    async def get_quota(self, key: str) -> int:
        """Return the current quota counter (``0`` if the key is missing).

        Args:
            key: The Redis key to read.

        Returns:
            The integer counter value, or ``0`` if the key is absent.
        """
        val = await self._client.get(key)
        return int(val) if val is not None else 0

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()
