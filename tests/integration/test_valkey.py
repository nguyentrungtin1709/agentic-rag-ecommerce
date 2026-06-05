"""Integration tests — Valkey (Redis-compatible) connectivity.

Verifies that:
- The Valkey service responds to PING.
- SET and GET round-trip correctly.
- TTL-based expiry is honoured.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from redis.asyncio import Redis

from tests.integration.conftest import VALKEY_URL

_TEST_KEY = "integration_test:valkey:sentinel"
_TEST_VALUE = "ok"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def valkey_client() -> AsyncGenerator[Redis, None]:
    """Return an async Redis client connected to Valkey."""
    client: Redis = Redis.from_url(VALKEY_URL, decode_responses=True)
    yield client
    await client.aclose()


async def test_valkey_ping(valkey_client: Redis) -> None:
    """PING must return True."""
    result = await valkey_client.ping()
    assert result is True


async def test_valkey_set_and_get(valkey_client: Redis) -> None:
    """SET followed by GET must return the stored value."""
    await valkey_client.set(_TEST_KEY, _TEST_VALUE)
    value = await valkey_client.get(_TEST_KEY)
    assert value == _TEST_VALUE


async def test_valkey_delete(valkey_client: Redis) -> None:
    """DEL must remove the key so that GET returns None."""
    await valkey_client.set(_TEST_KEY, _TEST_VALUE)
    await valkey_client.delete(_TEST_KEY)
    value = await valkey_client.get(_TEST_KEY)
    assert value is None


async def test_valkey_ttl_expiry(valkey_client: Redis) -> None:
    """SETEX with px=100ms must make the key disappear after expiry."""
    import asyncio

    ttl_key = "integration_test:valkey:ttl"
    await valkey_client.set(ttl_key, "ephemeral", px=200)
    before = await valkey_client.get(ttl_key)
    assert before == "ephemeral"
    await asyncio.sleep(0.3)
    after = await valkey_client.get(ttl_key)
    assert after is None
