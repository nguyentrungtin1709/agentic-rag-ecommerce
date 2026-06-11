"""Unit tests — ImageRepository.

Tests use a mock asyncpg pool (the ``mock_asyncpg_pool`` fixture from
``tests/conftest.py``) to verify the SQL and argument marshalling
without needing a running database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.image_repo import ImageRepository


@pytest.fixture
def repo(mock_asyncpg_pool: tuple[MagicMock, AsyncMock]) -> ImageRepository:
    """Return an ImageRepository bound to the mock pool."""
    pool, _conn = mock_asyncpg_pool
    return ImageRepository(pool)


# ---------------------------------------------------------------------------
# delete_by_thread
# ---------------------------------------------------------------------------


async def test_delete_by_thread_returns_row_count(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``delete_by_thread`` returns the count from the DELETE statement."""
    _pool, conn = mock_asyncpg_pool
    thread_id = uuid.uuid4()
    conn.execute.return_value = "DELETE 3"

    count = await repo.delete_by_thread(thread_id)

    assert count == 3
    conn.execute.assert_awaited_once_with(
        "DELETE FROM generated_images WHERE thread_id = $1",
        thread_id,
    )


async def test_delete_by_thread_returns_zero_for_no_rows(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``delete_by_thread`` returns ``0`` when nothing matched."""
    _pool, conn = mock_asyncpg_pool
    conn.execute.return_value = "DELETE 0"

    count = await repo.delete_by_thread(uuid.uuid4())

    assert count == 0


# ---------------------------------------------------------------------------
# list_by_message_id
# ---------------------------------------------------------------------------


async def test_list_by_message_id_filters_by_request_message_id(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``list_by_message_id`` filters by ``request_message_id = $1``."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    await repo.list_by_message_id("msg-123")

    sql, msg_id = conn.fetch.call_args.args
    assert "request_message_id = $1" in sql
    assert "ORDER BY created_at ASC" in sql
    assert msg_id == "msg-123"


async def test_list_by_message_id_returns_models_in_ascending_order(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """The result must be wrapped as ``GeneratedImage`` instances."""
    _pool, conn = mock_asyncpg_pool
    img_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    conn.fetch.return_value = [
        {
            "id": img_id,
            "thread_id": thread_id,
            "user_id": "u1",
            "prompt": "p",
            "s3_key": "k",
            "s3_url": "https://x",
            "model": "dall-e-3",
            "request_message_id": "m1",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    result = await repo.list_by_message_id("m1")

    assert len(result) == 1
    assert result[0].id == img_id
    assert result[0].request_message_id == "m1"


# ---------------------------------------------------------------------------
# count_by_user_date
# ---------------------------------------------------------------------------


async def test_count_by_user_date_returns_count_for_day_window(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``count_by_user_date`` queries the day window with ``$2::timestamptz``."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchval.return_value = 5

    target = date(2026, 6, 11)
    result = await repo.count_by_user_date("user-1", target)

    assert result == 5
    sql, user_id, day = conn.fetchval.call_args.args
    assert "COUNT(*)" in sql
    assert "created_at >= $2::timestamptz" in sql
    assert "INTERVAL '1 day'" in sql
    assert user_id == "user-1"
    assert day == target


async def test_count_by_user_date_returns_zero_for_no_images(
    repo: ImageRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``count_by_user_date`` returns ``0`` when no images match the window."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchval.return_value = 0

    result = await repo.count_by_user_date("user-1", date(2026, 6, 11))

    assert result == 0
