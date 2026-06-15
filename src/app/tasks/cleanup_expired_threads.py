"""Celery task — clean up threads that have been inactive for N+ days.

This task is scheduled by Celery Beat to run nightly at 2:00 AM UTC.
It finds all threads whose ``last_activity_at < now() - N days`` and
performs a full cleanup per thread: S3 objects first, then image
records, then the thread row (NFR-015 atomic ordering, D10.1, D10.3,
D10.4, D10.5).  The window ``N`` is read from
``Settings.thread_expiry_days`` (FR-018) so dev environments can
shorten it to exercise the sweep without waiting weeks.

The ``ThreadRepository.find_expired`` SQL already excludes rows whose
``status`` is ``'deleting'``, so a thread whose prior sweep failed
partway through (and is therefore stuck in ``status='deleting'``) is
NOT re-picked by the next nightly run — operator intervention is
required to unstick it (see ``history/10_0_0_CLEANUP_TASKS.md`` for
the rationale).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from app.config import get_settings
from app.db.session import get_asyncpg_pool, open_pools
from app.repositories.image_repo import ImageRepository
from app.repositories.thread_repo import ThreadRepository
from app.services.s3_service import S3Service
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="tasks.cleanup_expired_threads",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    time_limit=3600,
    queue="cleanup",
)
def cleanup_expired_threads(self) -> dict:
    """Delete all threads inactive for more than ``settings.thread_expiry_days``.

    Returns:
        Dict with ``threads_deleted``, ``images_deleted``, and
        ``duration_seconds`` keys.  On unhandled failure: ``status=
        "failed"`` plus ``error_type`` and ``error`` keys.  The task
        is NOT retried on permanent failure (D10.3) — stuck
        ``status='deleting'`` rows are surfaced for manual
        operator review instead.
    """
    settings = get_settings()
    start = time.monotonic()

    try:
        result = asyncio.run(_process(settings))
    except Exception as exc:
        logger.error(
            "cleanup_expired_threads_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {
            "threads_deleted": 0,
            "images_deleted": 0,
            "duration_seconds": round(time.monotonic() - start, 3),
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    result["duration_seconds"] = round(time.monotonic() - start, 3)
    return result


async def _process(settings) -> dict:
    """Async body — find expired threads, delete each sequentially.

    The DB pool is opened on the *current* event loop so asyncpg
    connections are bound here, not a closed loop from a previous
    ``asyncio.run()`` in the same worker process (see
    ``celery_app.py:36-49`` for the long-form rationale).
    """
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    image_repo = ImageRepository(pool)
    thread_repo = ThreadRepository(pool)
    s3 = S3Service(settings)

    # FR-018: inactivity window is configurable per-environment
    # (default 30 days, see ``Settings.thread_expiry_days``).
    cutoff = datetime.now(UTC) - timedelta(days=settings.thread_expiry_days)
    expired = await thread_repo.find_expired(cutoff)
    logger.info(
        "cleanup_expired_threads_sweep_start",
        expired_count=len(expired),
        cutoff=cutoff.isoformat(),
    )

    threads_deleted = 0
    images_deleted = 0
    try:
        for thread_id in expired:
            # Mark deleting first (D10.5) so a subsequent sweep
            # iteration — or a racing explicit DELETE — sees the
            # row in ``status='deleting'`` and skips it.  We do not
            # need the thread's user_id here; S3 keys live on
            # ``generated_images.s3_key`` and the row delete uses
            # ``delete_by_id`` (no owner check, D10.4).
            await thread_repo.set_status(thread_id, "deleting")
            images_deleted += await _delete_one(
                thread_id,
                image_repo,
                thread_repo,
                s3,
            )
            threads_deleted += 1
    finally:
        # Always release boto3 connections, even on failure.
        await s3.close()

    logger.info(
        "cleanup_expired_threads_sweep_done",
        threads_deleted=threads_deleted,
        images_deleted=images_deleted,
    )
    return {
        "threads_deleted": threads_deleted,
        "images_deleted": images_deleted,
        "status": "ok",
    }


async def _delete_one(
    thread_id: uuid.UUID,
    image_repo: ImageRepository,
    thread_repo: ThreadRepository,
    s3: S3Service,
) -> int:
    """Run the NFR-015 chain for one thread.  Returns images_deleted.

    S3 objects are deleted first; if any raises, the chain aborts and
    the surrounding sweep loop propagates the exception (D10.1).  The
    image records and the thread row are only removed after every S3
    key for the thread has been deleted successfully.
    """
    images = await image_repo.list_by_thread(thread_id)
    for image in images:
        await s3.delete(image.s3_key)
    images_deleted = await image_repo.delete_by_thread(thread_id)
    await thread_repo.delete_by_id(thread_id)
    return images_deleted
