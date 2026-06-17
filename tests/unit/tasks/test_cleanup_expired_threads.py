"""Unit tests — ``cleanup_expired_threads`` Celery task.

Covers the configurable expiry cutoff (FR-018,
``Settings.thread_expiry_days``), the sequential per-thread
processing (D10.3), the ``set_status('deleting')`` mark-first step
(D10.5), the count aggregation, the ``s3.close()`` cleanup, and the
``failed`` permanent-error path.
"""

from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings, get_settings
from app.tasks.cleanup_expired_threads import cleanup_expired_threads


def _settings() -> Settings:
    """Return the cached ``Settings`` instance (the env already supplies the
    required fields via the test session's ``.env`` load)."""
    return get_settings()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _invoke_task() -> dict:
    """Invoke the underlying ``cleanup_expired_threads`` function with a fake self."""
    fake_self = MagicMock()
    real_task = cleanup_expired_threads._get_current_object()  # type: ignore[attr-defined]
    bound = types.MethodType(real_task.run.__func__, fake_self)
    return bound()


def _fake_image(s3_key: str = "images/u-1/t-1/1700000000.png") -> MagicMock:
    img = MagicMock()
    img.s3_key = s3_key
    return img


def _build_repos(
    expired_ids: list[uuid.UUID],
    images_per_thread: int = 0,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build mock ImageRepository, ThreadRepository, and S3Service.

    The ``image_repo.list_by_thread`` always returns ``images_per_thread``
    mock images (newest first, simple list), and ``delete_by_thread``
    returns the count.
    """
    images = [_fake_image(f"k-{i}") for i in range(images_per_thread)]
    image_repo = MagicMock()
    image_repo.list_by_thread = AsyncMock(return_value=images)
    image_repo.delete_by_thread = AsyncMock(return_value=images_per_thread)
    thread_repo = MagicMock()
    thread_repo.find_expired = AsyncMock(return_value=expired_ids)
    thread_repo.set_status = AsyncMock()
    thread_repo.delete_by_id = AsyncMock(return_value=True)
    s3 = MagicMock()
    s3.delete = AsyncMock()
    s3.close = AsyncMock()
    return image_repo, thread_repo, s3


# ---------------------------------------------------------------------------
# Empty sweep
# ---------------------------------------------------------------------------


def test_cleanup_returns_ok_with_zero_counts_when_no_expired_threads() -> None:
    """``find_expired`` returns ``[]`` -> result is ok with zero counts."""
    image_repo, thread_repo, s3 = _build_repos(expired_ids=[])

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        result = _invoke_task()

    assert result["status"] == "ok"
    assert result["threads_deleted"] == 0
    assert result["images_deleted"] == 0
    assert isinstance(result["duration_seconds"], float)
    assert result["duration_seconds"] >= 0
    s3.delete.assert_not_awaited()
    thread_repo.set_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sequential processing + count aggregation
# ---------------------------------------------------------------------------


def test_cleanup_processes_each_expired_thread_sequentially() -> None:
    """3 expired threads -> set_status + delete chain called 3 times."""
    ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    image_repo, thread_repo, s3 = _build_repos(
        expired_ids=ids,
        images_per_thread=2,
    )

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        result = _invoke_task()

    # set_status called once per thread.
    assert thread_repo.set_status.await_count == 3
    for tid in ids:
        thread_repo.set_status.assert_any_await(tid, "deleting")
    # delete_by_id called once per thread.
    assert thread_repo.delete_by_id.await_count == 3
    for tid in ids:
        thread_repo.delete_by_id.assert_any_await(tid)
    # S3 deletes: 2 images * 3 threads = 6 calls.
    assert s3.delete.await_count == 6
    # Aggregate counts.
    assert result["threads_deleted"] == 3
    assert result["images_deleted"] == 6
    assert result["status"] == "ok"


def test_cleanup_marks_each_thread_deleting_before_s3_chain() -> None:
    """``set_status('deleting')`` is called BEFORE S3 deletes (D10.5)."""
    ids = [uuid.uuid4(), uuid.uuid4()]
    image_repo, thread_repo, s3 = _build_repos(
        expired_ids=ids,
        images_per_thread=1,
    )

    # Build a recording mock that captures the call order.
    call_log: list[tuple[str, str]] = []
    real_set_status = thread_repo.set_status

    async def record_set_status(thread_id: uuid.UUID, status: str) -> None:
        call_log.append(("set_status", str(thread_id)))
        await real_set_status(thread_id, status)

    real_s3_delete = s3.delete

    async def record_s3_delete(key: str) -> None:
        call_log.append(("s3_delete", key))
        await real_s3_delete(key)

    thread_repo.set_status = AsyncMock(side_effect=record_set_status)
    s3.delete = AsyncMock(side_effect=record_s3_delete)

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        _invoke_task()

    # For the first thread: set_status(thread_1) must come before any
    # s3.delete call for that thread.  We check the first set_status
    # happens before the first s3.delete.
    set_status_indices = [i for i, (op, _) in enumerate(call_log) if op == "set_status"]
    s3_delete_indices = [i for i, (op, _) in enumerate(call_log) if op == "s3_delete"]
    assert set_status_indices[0] < s3_delete_indices[0]


# ---------------------------------------------------------------------------
# Configurable expiry cutoff (FR-018)
# ---------------------------------------------------------------------------


def test_cleanup_uses_default_30_day_cutoff() -> None:
    """The cutoff passed to ``find_expired`` is ``now() - N days``
    where ``N`` comes from ``Settings.thread_expiry_days`` (FR-018)."""
    image_repo, thread_repo, s3 = _build_repos(expired_ids=[])

    before = datetime.now(UTC)
    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        _invoke_task()
    after = datetime.now(UTC)

    expected_days = _settings().thread_expiry_days
    cutoff = thread_repo.find_expired.await_args.args[0]
    # Cutoff is within ``[before - Nd, after - Nd]`` (with 1s slack for clock drift).
    lower = before - timedelta(days=expected_days, seconds=1)
    upper = after - timedelta(days=expected_days) + timedelta(seconds=1)
    assert lower <= cutoff <= upper


def test_cleanup_honors_custom_thread_expiry_days() -> None:
    """``Settings.thread_expiry_days`` flows through to the cutoff (no hardcode)."""
    image_repo, thread_repo, s3 = _build_repos(expired_ids=[])

    custom = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="sk-test",
        saleor_webhook_secret="a" * 40,
        thread_expiry_days=7,
    )
    before = datetime.now(UTC)
    with (
        patch("app.tasks.cleanup_expired_threads.get_settings", return_value=custom),
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        _invoke_task()
    after = datetime.now(UTC)

    cutoff = thread_repo.find_expired.await_args.args[0]
    lower = before - timedelta(days=7, seconds=1)
    upper = after - timedelta(days=7) + timedelta(seconds=1)
    assert lower <= cutoff <= upper


# ---------------------------------------------------------------------------
# Permanent-error path
# ---------------------------------------------------------------------------


def test_cleanup_returns_failed_status_on_unexpected_exception() -> None:
    """``find_expired`` raises -> structured ``failed`` dict, no S3 work."""
    image_repo, thread_repo, s3 = _build_repos(expired_ids=[])
    thread_repo.find_expired = AsyncMock(side_effect=RuntimeError("DB down"))

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        result = _invoke_task()

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert "DB down" in result["error"]
    assert result["threads_deleted"] == 0
    assert result["images_deleted"] == 0
    s3.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# s3.close() in finally
# ---------------------------------------------------------------------------


def test_cleanup_calls_s3_close_on_success() -> None:
    """``s3.close()`` runs after the sweep on the happy path."""
    image_repo, thread_repo, s3 = _build_repos(expired_ids=[])

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        _invoke_task()

    s3.close.assert_awaited_once()


def test_cleanup_calls_s3_close_on_error() -> None:
    """``s3.close()`` runs in finally — even when an inner call raises."""
    image_repo, thread_repo, s3 = _build_repos(
        expired_ids=[uuid.uuid4()],
        images_per_thread=1,
    )
    s3.delete = AsyncMock(side_effect=RuntimeError("S3 500"))

    with (
        patch("app.tasks.cleanup_expired_threads.open_pools", new=AsyncMock()),
        patch("app.tasks.cleanup_expired_threads.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.cleanup_expired_threads.ImageRepository", return_value=image_repo),
        patch("app.tasks.cleanup_expired_threads.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.cleanup_expired_threads.S3Service", return_value=s3),
    ):
        result = _invoke_task()

    s3.close.assert_awaited_once()
    assert result["status"] == "failed"
    assert result["threads_deleted"] == 0
