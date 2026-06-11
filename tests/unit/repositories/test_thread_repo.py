"""Unit tests — ThreadRepository.

Tests use a mock asyncpg pool (the ``mock_asyncpg_pool`` fixture from
``tests/conftest.py``) to verify the SQL and argument marshalling
without needing a running database.  Integration tests
(``tests/integration/test_postgres.py``) cover the real wire format.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.thread_repo import ThreadRepository


@pytest.fixture
def repo(mock_asyncpg_pool: tuple[MagicMock, AsyncMock]) -> ThreadRepository:
    """Return a ThreadRepository bound to the mock pool."""
    pool, _conn = mock_asyncpg_pool
    return ThreadRepository(pool)


# ---------------------------------------------------------------------------
# find_expired
# ---------------------------------------------------------------------------


async def test_find_expired_returns_ids_older_than_cutoff(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """Only threads with ``last_activity_at < cutoff`` are returned."""
    _pool, conn = mock_asyncpg_pool
    old_id = uuid.uuid4()
    recent_id = uuid.uuid4()
    conn.fetch.return_value = [
        {"id": old_id},
        {"id": recent_id},
    ]

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    result = await repo.find_expired(cutoff)

    assert result == [old_id, recent_id]
    conn.fetch.assert_awaited_once()
    sql, cutoff_arg = conn.fetch.call_args.args
    assert "last_activity_at < $1" in sql
    assert "status != 'deleting'" in sql
    assert cutoff_arg == cutoff


async def test_find_expired_excludes_deleting_status(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """The SQL filter must exclude rows already marked 'deleting'."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    await repo.find_expired(datetime.now(UTC))

    sql = conn.fetch.call_args.args[0]
    assert "status != 'deleting'" in sql
    assert "deleting" in sql


async def test_find_expired_returns_empty_list_when_no_expirations(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``find_expired`` returns ``[]`` when the query yields no rows."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    result = await repo.find_expired(datetime.now(UTC) + timedelta(days=365))

    assert result == []


async def test_find_expired_does_not_use_updated_at(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """The query must use ``last_activity_at`` (FR-018) not ``updated_at``."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    await repo.find_expired(datetime.now(UTC))

    sql = conn.fetch.call_args.args[0]
    assert "last_activity_at" in sql
    assert "updated_at" not in sql
