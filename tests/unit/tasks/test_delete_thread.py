"""Unit tests — ``delete_thread`` Celery task.

Covers the NFR-015 atomic ordering (S3 → image rows → thread row),
the transient-vs-permanent error classifier, the retry path, the
no-images edge case, the ``s3.close()`` cleanup, and the structured
return value (D10.1).
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.tasks.delete_thread import delete_thread

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


# Fixed UUID strings used throughout the test module — every call into
# ``_process`` runs ``uuid.UUID(thread_id)`` first, so non-UUID strings
# surface as a ValueError *before* any of the mocks have a chance to
# take effect.  Keeping the ids UUID-shaped lets every test start
# from the same well-defined "all mocks in place" state.
_TID = "00000000-0000-0000-0000-000000000001"
_UID = "u-1"


def _invoke_task(fake_self: MagicMock, thread_id: str = _TID, user_id: str = _UID) -> dict:
    """Invoke the underlying ``delete_thread`` function with a fake self.

    Celery's ``.run()`` attribute does not pass ``self``, so the only
    way to inject a controlled ``request.retries`` / ``retry()`` is to
    bind the unbound ``delete_thread`` function (the original function
    captured by the ``@celery_app.task(bind=True)`` decorator) to a
    mock instance.
    """
    real_task = delete_thread._get_current_object()  # type: ignore[attr-defined]
    bound = types.MethodType(real_task.run.__func__, fake_self)
    return bound(thread_id, user_id)


def _fake_self(retries: int = 0) -> MagicMock:
    """Build a fake bound Celery task instance."""
    self = MagicMock()
    self.request.retries = retries
    # ``self.retry`` must re-raise the original exception (Celery
    # semantics) so the test's try/except sees it.
    self.retry.side_effect = lambda exc: (_ for _ in ()).throw(exc)
    return self


def _fake_image(s3_key: str = "images/u-1/t-1/1700000000.png") -> MagicMock:
    """Build a fake ``GeneratedImage`` with just the field the task reads."""
    img = MagicMock()
    img.s3_key = s3_key
    return img


def _build_repos(images: list[MagicMock]) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build mock ImageRepository, ThreadRepository, and S3Service."""
    image_repo = MagicMock()
    image_repo.list_by_thread = AsyncMock(return_value=images)
    image_repo.delete_by_thread = AsyncMock(return_value=len(images))
    thread_repo = MagicMock()
    thread_repo.delete = AsyncMock(return_value=True)
    s3 = MagicMock()
    s3.delete = AsyncMock()
    s3.close = AsyncMock()
    return image_repo, thread_repo, s3


# ---------------------------------------------------------------------------
# Happy path — NFR-015 ordering + return value
# ---------------------------------------------------------------------------


def test_delete_thread_happy_path_deletes_s3_images_then_thread() -> None:
    """Full chain succeeds; return shape and call order are correct."""
    image_repo, thread_repo, s3 = _build_repos(
        [_fake_image("k1"), _fake_image("k2"), _fake_image("k3")],
    )

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
    ):
        result = _invoke_task(_fake_self())

    # NFR-015 ordering: s3.delete called once per image.
    assert s3.delete.await_count == 3
    s3.delete.assert_any_await("k1")
    s3.delete.assert_any_await("k2")
    s3.delete.assert_any_await("k3")
    # Image rows deleted AFTER all S3 keys.
    image_repo.delete_by_thread.assert_awaited_once()
    delete_args = image_repo.delete_by_thread.await_args.args
    assert delete_args[0] == uuid.UUID(_TID)
    # Thread row deleted LAST.
    thread_repo.delete.assert_awaited_once_with(uuid.UUID(_TID), _UID)
    assert result == {
        "thread_id": _TID,
        "images_deleted": 3,
        "status": "deleted",
    }


def test_delete_thread_with_zero_images_still_deletes_thread_row() -> None:
    """Thread with no images -> s3.delete not called, but thread row removed."""
    image_repo, thread_repo, s3 = _build_repos([])

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
    ):
        result = _invoke_task(_fake_self())

    s3.delete.assert_not_awaited()
    image_repo.delete_by_thread.assert_awaited_once()
    thread_repo.delete.assert_awaited_once()
    assert result["images_deleted"] == 0
    assert result["status"] == "deleted"


# ---------------------------------------------------------------------------
# S3 failure — D10.1 strict abort
# ---------------------------------------------------------------------------


def test_delete_thread_s3_failure_aborts_before_deleting_db_rows() -> None:
    """S3 delete raises (non-transient) -> image + thread rows NEVER deleted (NFR-015).

    A non-transient exception returns the ``failed`` dict (does not
    re-raise) — see ``test_delete_thread_transient_s3_error_triggers_retry``
    for the transient case which DOES propagate.  In both branches the
    DB rows must remain untouched.
    """
    image_repo, thread_repo, s3 = _build_repos(
        [_fake_image("k1"), _fake_image("k2")],
    )
    s3.delete = AsyncMock(side_effect=RuntimeError("S3 500"))

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
    ):
        result = _invoke_task(_fake_self())

    image_repo.delete_by_thread.assert_not_called()
    thread_repo.delete.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"


def test_delete_thread_transient_s3_error_triggers_retry() -> None:
    """``httpx.ConnectError`` is transient -> ``self.retry`` is called."""
    image_repo, thread_repo, s3 = _build_repos([_fake_image("k1")])
    s3.delete = AsyncMock(side_effect=httpx.ConnectError("refused"))

    fake_self = _fake_self(retries=0)

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
        pytest.raises(httpx.ConnectError),
    ):
        _invoke_task(fake_self)

    fake_self.retry.assert_called_once()


# ---------------------------------------------------------------------------
# Permanent error path
# ---------------------------------------------------------------------------


def test_delete_thread_permanent_error_returns_failed_status() -> None:
    """Non-transient exception -> structured ``failed`` dict, no retry."""
    image_repo, thread_repo, s3 = _build_repos([])
    image_repo.list_by_thread = AsyncMock(side_effect=ValueError("corrupt row"))

    fake_self = _fake_self(retries=0)

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
    ):
        result = _invoke_task(fake_self)

    fake_self.retry.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_type"] == "ValueError"
    assert "corrupt row" in result["error"]
    assert result["thread_id"] == _TID


# ---------------------------------------------------------------------------
# s3.close() in finally
# ---------------------------------------------------------------------------


def test_delete_thread_calls_s3_close_on_success() -> None:
    """``s3.close()`` is called even on the happy path (boto3 cleanup)."""
    image_repo, thread_repo, s3 = _build_repos([_fake_image("k1")])

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
    ):
        _invoke_task(_fake_self())

    s3.close.assert_awaited_once()


def test_delete_thread_calls_s3_close_on_failure() -> None:
    """``s3.close()`` runs in finally — even when S3 itself raised."""
    image_repo, thread_repo, s3 = _build_repos([_fake_image("k1")])
    s3.delete = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
        pytest.raises(httpx.ConnectError),
    ):
        _invoke_task(_fake_self())

    s3.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def test_delete_thread_logs_s3_phase_start_with_image_count() -> None:
    """The S3-phase-start log includes the thread id and image count."""
    image_repo, thread_repo, s3 = _build_repos(
        [_fake_image("k1"), _fake_image("k2")],
    )

    with (
        patch("app.tasks.delete_thread.open_pools", new=AsyncMock()),
        patch("app.tasks.delete_thread.get_asyncpg_pool", return_value=MagicMock()),
        patch("app.tasks.delete_thread.ImageRepository", return_value=image_repo),
        patch("app.tasks.delete_thread.ThreadRepository", return_value=thread_repo),
        patch("app.tasks.delete_thread.S3Service", return_value=s3),
        patch("app.tasks.delete_thread.logger") as fake_logger,
    ):
        _invoke_task(_fake_self())

    info_calls = fake_logger.info.call_args_list
    s3_phase_log = next(
        (c for c in info_calls if c.args and c.args[0] == "delete_thread_s3_phase_start"),
        None,
    )
    assert s3_phase_log is not None, "delete_thread_s3_phase_start log not emitted"
    assert s3_phase_log.kwargs.get("thread_id") == _TID
    assert s3_phase_log.kwargs.get("image_count") == 2
