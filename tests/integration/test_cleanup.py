"""Integration tests — cleanup Celery tasks against a real Postgres DB.

End-to-end: insert thread + image rows directly via asyncpg, run the
task body synchronously, assert the row state is clean.

S3 is mocked (the real S3 bucket is a Terraform-managed resource we
do not want to mutate in tests).  Postgres is real — the test
verifies the NFR-015 row state and the 30-day sweep semantics.

Skipped when Postgres is unreachable so a missing environment fails
loudly with a single, descriptive message.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Protocol
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.config import get_settings
from app.tasks.cleanup_expired_threads import _process as _cleanup_process
from app.tasks.delete_thread import _process as _delete_thread_process
from tests.integration.conftest import APP_URL, POSTGRES_DSN

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stack-availability helpers
# ---------------------------------------------------------------------------


def _app_available() -> bool:
    """Return ``True`` when the FastAPI app responds to ``GET /health``.

    Cleanup tasks touch DB + S3 only, so the app process itself is not
    strictly required — but if the app is up, the DB is almost
    certainly up, and skipping both together keeps the message simple.
    """
    try:
        response = httpx.get(f"{APP_URL}/health", timeout=2.0)
        return response.status_code == 200
    except (httpx.RequestError, httpx.HTTPError):
        return False


_SKIP_NO_STACK = pytest.mark.skipif(
    not _app_available(),
    reason="Stack offline — start with 'docker compose up -d'",
)


# ---------------------------------------------------------------------------
# S3 mock — context-manager helper that patches both task modules
# ---------------------------------------------------------------------------


class _S3Mock(Protocol):
    """Shape of the patched S3 service used by the cleanup tests."""

    delete: AsyncMock
    close: AsyncMock


def _patch_s3() -> tuple[_S3Mock, _S3Patcher]:
    """Patch ``S3Service`` in both task modules and return the mock + CM.

    Usage::

        s3, patcher = _patch_s3()
        with patcher:
            ...

    The mock records every ``delete`` call and never raises — the
    S3-failure path is covered by the unit tests, not here.
    """
    mock_s3: _S3Mock = MagicMock()  # type: ignore[assignment]
    mock_s3.delete = AsyncMock()
    mock_s3.close = AsyncMock()
    return mock_s3, _S3Patcher(mock_s3)


class _S3Patcher:
    """Context manager that patches ``S3Service`` in both task modules."""

    def __init__(self, mock_s3: _S3Mock) -> None:
        self._mock_s3 = mock_s3
        self._stack = ExitStack()

    def __enter__(self) -> _S3Mock:
        self._stack.enter_context(
            patch("app.tasks.delete_thread.S3Service", return_value=self._mock_s3),
        )
        self._stack.enter_context(
            patch("app.tasks.cleanup_expired_threads.S3Service", return_value=self._mock_s3),
        )
        return self._mock_s3

    def __exit__(self, *exc: object) -> None:
        del exc  # unused — required by context-manager protocol
        self._stack.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Open a short-lived asyncpg pool for the test module."""
    pool: asyncpg.Pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=2)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def cleanup_threads(
    pg_pool: asyncpg.Pool,
) -> AsyncGenerator[list[uuid.UUID], None]:
    """Yield a list for the caller to append thread ids to, then wipe them.

    The test mutates ``tracked`` by appending; the fixture's teardown
    deletes both ``threads`` and ``generated_images`` rows for every
    tracked id, regardless of what the test did or did not assert.
    """
    tracked: list[uuid.UUID] = []
    yield tracked
    if not tracked:
        return
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM generated_images WHERE thread_id = ANY($1::uuid[])",
            tracked,
        )
        await conn.execute(
            "DELETE FROM threads WHERE id = ANY($1::uuid[])",
            tracked,
        )


async def _insert_thread(
    pg_pool: asyncpg.Pool,
    *,
    user_id: str = "u-cleanup-test",
    last_activity_offset_days: int = 0,
    status: str = "idle",
) -> uuid.UUID:
    """Insert a thread row with an explicit ``last_activity_at`` offset.

    Args:
        pg_pool: Active asyncpg pool.
        user_id: Saleor user id.
        last_activity_offset_days: Negative = past, positive = future
            (relative to now).  Default 0 = "right now".
        status: Thread lifecycle status (e.g. ``"idle"``, ``"deleting"``).

    Returns:
        The new thread UUID.
    """
    thread_id = uuid.uuid4()
    last_activity = datetime.now(UTC) + timedelta(days=last_activity_offset_days)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO threads (id, user_id, last_activity_at, status)
            VALUES ($1, $2, $3, $4)
            """,
            thread_id,
            user_id,
            last_activity,
            status,
        )
    return thread_id


async def _insert_image(
    pg_pool: asyncpg.Pool,
    thread_id: uuid.UUID,
    *,
    user_id: str = "u-cleanup-test",
    s3_key: str = "images/u-cleanup-test/t-1/1700000000.png",
) -> uuid.UUID:
    """Insert a generated_images row.  Returns the new image id."""
    image_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO generated_images
                (id, thread_id, user_id, prompt, s3_key, s3_url, model)
            VALUES ($1, $2, $3, 'test', $4, 'https://example.com/x.png', 'dall-e-3')
            """,
            image_id,
            thread_id,
            user_id,
            s3_key,
        )
    return image_id


async def _invoke_delete_thread(
    thread_id: str,
    user_id: str,
) -> dict:
    """Await ``_process`` directly — the Celery wrapper uses ``asyncio.run``
    which is forbidden inside a running event loop (pytest-asyncio).

    The wrapper itself (asyncio.run + try/except + retry) is exercised
    by the unit tests; this integration test focuses on the async
    body + real Postgres.
    """
    return await _delete_thread_process(thread_id, user_id, get_settings())


async def _invoke_cleanup_expired_threads() -> dict:
    """Await ``_process`` directly — see ``_invoke_delete_thread`` for why."""
    return await _cleanup_process(get_settings())


# ---------------------------------------------------------------------------
# delete_thread — E2E
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
async def test_delete_thread_e2e_removes_db_rows(
    pg_pool: asyncpg.Pool,
    cleanup_threads: list[uuid.UUID],
) -> None:
    """Insert 1 thread + 2 image rows; run ``delete_thread``; assert all gone."""
    thread_id = await _insert_thread(pg_pool)
    cleanup_threads.append(thread_id)
    image_a = await _insert_image(pg_pool, thread_id, s3_key="k-a")
    image_b = await _insert_image(pg_pool, thread_id, s3_key="k-b")

    mock_s3, s3_patcher = _patch_s3()
    with (
        s3_patcher,
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=pg_pool),
    ):
        result = await _invoke_delete_thread(str(thread_id), "u-cleanup-test")

    # S3 was called once per image.
    assert mock_s3.delete.await_count == 2
    # Return shape.
    assert result["status"] == "deleted"
    assert result["images_deleted"] == 2
    # DB state: image rows and thread row are gone.
    async with pg_pool.acquire() as conn:
        image_count = await conn.fetchval(
            "SELECT COUNT(*) FROM generated_images WHERE id = ANY($1::uuid[])",
            [image_a, image_b],
        )
        thread_count = await conn.fetchval(
            "SELECT COUNT(*) FROM threads WHERE id = $1",
            thread_id,
        )
    assert image_count == 0
    assert thread_count == 0


@_SKIP_NO_STACK
async def test_delete_thread_idempotent_when_run_twice(
    pg_pool: asyncpg.Pool,
    cleanup_threads: list[uuid.UUID],
) -> None:
    """Run twice on the same id — second run is a clean no-op."""
    thread_id = await _insert_thread(pg_pool)
    cleanup_threads.append(thread_id)
    await _insert_image(pg_pool, thread_id, s3_key="k-a")

    s3_patcher = _patch_s3()[1]
    with (
        s3_patcher,
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=pg_pool),
    ):
        first = await _invoke_delete_thread(str(thread_id), "u-cleanup-test")
        second = await _invoke_delete_thread(str(thread_id), "u-cleanup-test")

    assert first["status"] == "deleted"
    assert first["images_deleted"] == 1
    # Second run: thread is gone, so no images, so 0 deletes.
    assert second["status"] == "deleted"
    assert second["images_deleted"] == 0


# ---------------------------------------------------------------------------
# cleanup_expired_threads — E2E
# ---------------------------------------------------------------------------


@_SKIP_NO_STACK
async def test_cleanup_sweeps_only_expired_threads(
    pg_pool: asyncpg.Pool,
    cleanup_threads: list[uuid.UUID],
) -> None:
    """One thread older than 30 days and one fresh — only the old one is removed."""
    old_id = await _insert_thread(
        pg_pool,
        last_activity_offset_days=-31,
        user_id="u-old",
    )
    fresh_id = await _insert_thread(
        pg_pool,
        last_activity_offset_days=-5,
        user_id="u-fresh",
    )
    cleanup_threads.append(old_id)
    cleanup_threads.append(fresh_id)
    await _insert_image(pg_pool, old_id, user_id="u-old", s3_key="old-k")

    s3_patcher = _patch_s3()[1]
    with (
        s3_patcher,
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=pg_pool),
    ):
        result = await _invoke_cleanup_expired_threads()

    assert result["status"] == "ok"
    assert result["threads_deleted"] == 1
    assert result["images_deleted"] == 1
    # Old thread + its images are gone; fresh thread is intact.
    async with pg_pool.acquire() as conn:
        old_count = await conn.fetchval(
            "SELECT COUNT(*) FROM threads WHERE id = $1",
            old_id,
        )
        fresh_count = await conn.fetchval(
            "SELECT COUNT(*) FROM threads WHERE id = $1",
            fresh_id,
        )
        fresh_image_count = await conn.fetchval(
            "SELECT COUNT(*) FROM generated_images WHERE thread_id = $1",
            fresh_id,
        )
    assert old_count == 0
    assert fresh_count == 1
    assert fresh_image_count == 0


@_SKIP_NO_STACK
async def test_cleanup_skips_threads_already_deleting(
    pg_pool: asyncpg.Pool,
    cleanup_threads: list[uuid.UUID],
) -> None:
    """A thread with ``status='deleting'`` is left alone (idempotency guard)."""
    deleting_id = await _insert_thread(
        pg_pool,
        last_activity_offset_days=-31,
        status="deleting",
        user_id="u-stuck",
    )
    cleanup_threads.append(deleting_id)

    s3_patcher = _patch_s3()[1]
    with (
        s3_patcher,
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=pg_pool),
    ):
        result = await _invoke_cleanup_expired_threads()

    assert result["status"] == "ok"
    assert result["threads_deleted"] == 0
    # The row is still present.
    async with pg_pool.acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM threads WHERE id = $1",
            deleting_id,
        )
    assert row_count == 1
