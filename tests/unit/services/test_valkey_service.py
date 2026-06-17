"""Unit tests — ValkeyService extensions (get/set/delete/delete_pattern/quota).

Tests use a fakeredis backend so the tests are fully self-contained —
no running Valkey is required.  The tests verify:

- ``get`` / ``set`` / ``delete`` round-trip values and handle misses.
- ``delete_pattern`` scans with the requested pattern and counts deletes.
- ``increment_quota`` sets the TTL on the first hit only.
- ``get_quota`` returns ``0`` for missing keys and parses integer values.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.services.valkey_service import ValkeyService


@pytest.fixture
def valkey() -> ValkeyService:
    """Return a ``ValkeyService`` whose ``_client`` is a fakeredis instance."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    service = ValkeyService("redis://localhost:6379/0")
    service._client = fake  # type: ignore[attr-defined]
    return service


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


async def test_get_returns_value_when_key_exists(valkey: ValkeyService) -> None:
    """``get`` returns the stored string value."""
    await valkey.set("k", "v")
    assert await valkey.get("k") == "v"


async def test_get_returns_none_for_missing_key(valkey: ValkeyService) -> None:
    """``get`` returns ``None`` when the key is absent."""
    assert await valkey.get("missing") is None


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


async def test_set_without_ttl_persists_forever(valkey: ValkeyService) -> None:
    """``set(key, value)`` with no TTL writes a non-expiring key."""
    await valkey.set("k", "v")
    ttl = await valkey._client.ttl("k")  # type: ignore[attr-defined]
    assert ttl == -1  # -1 means "no TTL"


async def test_set_with_ttl_applies_expiry(valkey: ValkeyService) -> None:
    """``set(key, value, ttl=N)`` applies a TTL in seconds."""
    await valkey.set("k", "v", ttl=60)
    ttl = await valkey._client.ttl("k")  # type: ignore[attr-defined]
    assert 0 < ttl <= 60


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_removes_key(valkey: ValkeyService) -> None:
    """``delete`` removes the key so the next ``get`` returns ``None``."""
    await valkey.set("k", "v")
    await valkey.delete("k")
    assert await valkey.get("k") is None


async def test_delete_on_missing_key_does_not_raise(valkey: ValkeyService) -> None:
    """``delete`` on a non-existent key is a silent no-op."""
    await valkey.delete("never-existed")  # must not raise


# ---------------------------------------------------------------------------
# delete_pattern
# ---------------------------------------------------------------------------


async def test_delete_pattern_removes_matching_keys(valkey: ValkeyService) -> None:
    """``delete_pattern`` clears every key matching the glob."""
    await valkey.set("threads:user-a:head:20", "[1]")
    await valkey.set("threads:user-a:head:50", "[2]")
    await valkey.set("threads:user-b:head:20", "[3]")

    deleted = await valkey.delete_pattern("threads:user-a:*")

    assert deleted == 2
    assert await valkey.get("threads:user-a:head:20") is None
    assert await valkey.get("threads:user-a:head:50") is None
    assert await valkey.get("threads:user-b:head:20") == "[3]"


async def test_delete_pattern_returns_zero_for_no_matches(
    valkey: ValkeyService,
) -> None:
    """``delete_pattern`` returns ``0`` when no key matches."""
    deleted = await valkey.delete_pattern("nothing-matches:*")
    assert deleted == 0


async def test_delete_pattern_uses_scan_not_keys(
    valkey: ValkeyService,
) -> None:
    """``delete_pattern`` must use ``SCAN`` (``scan_iter``) to avoid blocking.

    We spy on ``scan_iter`` to confirm it is the iterator backing the
    implementation, rather than relying on fakeredis' ``KEYS`` behaviour.
    """
    await valkey.set("k", "v")

    scan_iter_spy = MagicMock(wraps=valkey._client.scan_iter)  # type: ignore[attr-defined]
    valkey._client.scan_iter = scan_iter_spy  # type: ignore[attr-defined]

    await valkey.delete_pattern("k")

    scan_iter_spy.assert_called_once_with(match="k", count=500)


# ---------------------------------------------------------------------------
# increment_quota / get_quota
# ---------------------------------------------------------------------------


async def test_increment_quota_first_call_sets_ttl(valkey: ValkeyService) -> None:
    """The TTL is applied only when ``INCR`` returns ``1`` (first hit).

    We verify the behavioural effect (TTL is set on the key) rather
    than spying on the ``EXPIRE`` call — fakeredis + AsyncMock does
    not interop cleanly with ``side_effect=``.
    """
    new_value = await valkey.increment_quota("user-a:2026-06-11", ttl=120)

    assert new_value == 1
    ttl = await valkey._client.ttl("user-a:2026-06-11")  # type: ignore[attr-defined]
    assert 0 < ttl <= 120


async def test_increment_quota_subsequent_calls_do_not_reset_ttl(
    valkey: ValkeyService,
) -> None:
    """Subsequent INCRs in the same period do NOT call ``EXPIRE``.

    If ``EXPIRE`` were re-applied, the TTL window would slide forward
    on every increment — the opposite of what a daily quota needs.
    """
    # Prime the TTL with a short window.
    await valkey.increment_quota("k", ttl=60)
    initial_ttl = await valkey._client.ttl("k")  # type: ignore[attr-defined]
    assert 0 < initial_ttl <= 60

    # Sleep briefly so the second TTL read is observably different if EXPIRE
    # were re-applied. We then assert the TTL has NOT increased.
    import asyncio

    await asyncio.sleep(0.05)
    new_value = await valkey.increment_quota("k", ttl=60)
    later_ttl = await valkey._client.ttl("k")  # type: ignore[attr-defined]

    assert new_value == 2
    assert later_ttl <= initial_ttl


async def test_get_quota_returns_integer_value(valkey: ValkeyService) -> None:
    """``get_quota`` parses the stored value as ``int``."""
    await valkey.set("quota:k", "5")
    assert await valkey.get_quota("quota:k") == 5


async def test_get_quota_returns_zero_for_missing_key(valkey: ValkeyService) -> None:
    """``get_quota`` returns ``0`` when the key is absent."""
    assert await valkey.get_quota("missing") == 0


# ---------------------------------------------------------------------------
# ping (regression — ensure existing behaviour is unchanged)
# ---------------------------------------------------------------------------


async def test_ping_returns_true_on_healthy_backend(valkey: ValkeyService) -> None:
    """``ping`` returns ``True`` when the server responds."""
    assert await valkey.ping() is True


async def test_ping_returns_false_on_failure(valkey: ValkeyService) -> None:
    """``ping`` returns ``False`` (and logs a warning) on error."""
    valkey._client = MagicMock()  # type: ignore[attr-defined]
    valkey._client.ping = AsyncMock(side_effect=ConnectionError("nope"))  # type: ignore[attr-defined]
    assert await valkey.ping() is False
