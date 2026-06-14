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


# ---------------------------------------------------------------------------
# list_all (Phase 9, D9.4)
# ---------------------------------------------------------------------------


async def test_list_all_first_page_orders_by_updated_at_desc(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``list_all`` with no cursor selects everything, newest first,
    and the SQL has no ``user_id`` predicate (admin scope)."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    await repo.list_all(limit=20)

    sql, limit_arg = conn.fetch.call_args.args
    # No user_id filter — admin sees every thread.
    assert "WHERE user_id" not in sql
    assert "ORDER BY updated_at DESC, id DESC" in sql
    assert limit_arg == 20


async def test_list_all_first_page_hydrates_threads(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """Rows from the first page are parsed into ``Thread`` models."""
    _pool, conn = mock_asyncpg_pool
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    now = datetime(2026, 6, 14, tzinfo=UTC)
    conn.fetch.return_value = [
        {
            "id": t1,
            "user_id": "u-1",
            "title": "first",
            "status": "idle",
            "title_generated": True,
            "title_generation_attempts": 0,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        },
        {
            "id": t2,
            "user_id": "u-2",
            "title": None,
            "status": "busy",
            "title_generated": False,
            "title_generation_attempts": 1,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        },
    ]

    result = await repo.list_all(limit=20)

    assert len(result) == 2
    assert [t.id for t in result] == [t1, t2]
    assert result[0].user_id == "u-1"
    assert result[1].title is None


async def test_list_all_with_cursor_uses_lt_predicate(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """A non-null cursor resolves to ``updated_at`` and the next page
    uses ``(updated_at, id) < (cursor_updated_at, cursor_id)`` semantics
    (matches the per-user ``list_by_user`` cursor)."""
    _pool, conn = mock_asyncpg_pool
    cursor_id = uuid.uuid4()
    cursor_updated = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)
    # First call resolves the cursor row.
    conn.fetchrow.return_value = {"updated_at": cursor_updated}
    conn.fetch.return_value = []

    await repo.list_all(limit=20, before=cursor_id)

    # fetchrow resolves the cursor.
    cursor_sql, cursor_arg = conn.fetchrow.call_args.args
    assert "SELECT updated_at FROM threads WHERE id = $1" in cursor_sql
    assert cursor_arg == cursor_id
    # fetch uses the resolved updated_at + cursor_id for the WHERE clause.
    fetch_sql, fetch_updated, fetch_cursor_str, fetch_limit = conn.fetch.call_args.args
    assert "updated_at < $1" in fetch_sql
    assert "id::text < $2::text" in fetch_sql
    assert fetch_updated == cursor_updated
    assert fetch_cursor_str == str(cursor_id)
    assert fetch_limit == 20


async def test_list_all_with_unknown_cursor_returns_empty(
    repo: ThreadRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """When the cursor UUID is unknown, the method returns ``[]`` early
    without issuing the second ``fetch`` — matches the per-user method."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchrow.return_value = None

    result = await repo.list_all(limit=20, before=uuid.uuid4())

    assert result == []
    conn.fetch.assert_not_awaited()
