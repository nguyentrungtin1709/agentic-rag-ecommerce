"""Celery task — delete a specific thread and all associated assets.

This task is enqueued by ``DELETE /api/v1/threads/{thread_id}`` after the
thread status has been set to ``deleting`` (202 Accepted is returned to the
client immediately).  Cleanup is atomic: S3 objects are deleted before
database records to avoid orphaned storage (NFR-015, D10.1).

The sync task body delegates to an async coroutine via ``asyncio.run``
so DB pool / S3 lifecycle / retry classification follow the same
pattern as ``process_batch`` and ``process_webhook``.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog

from app.config import get_settings
from app.db.session import get_asyncpg_pool, open_pools
from app.repositories.image_repo import ImageRepository
from app.repositories.thread_repo import ThreadRepository
from app.services.s3_service import S3Service
from app.tasks.celery_app import celery_app
from app.utils.transient import is_transient

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="tasks.delete_thread",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="cleanup",
)
def delete_thread(
    self,
    thread_id: str,
    user_id: str,
) -> dict:
    """Delete a thread and all its S3 objects and image records.

    On any failure during the S3 phase the chain aborts and Celery
    auto-retries the whole task (D10.1) — ``S3Service.delete`` and
    ``ThreadRepository.delete`` are both idempotent, so a retry is
    safe.  Permanent errors (non-transient) return a
    ``{"status": "failed", ...}`` dict without re-raising.

    Args:
        self: Celery task instance (for retry support).
        thread_id: UUID string of the thread to delete.
        user_id: Saleor user ID string (owner check on the thread
            row; not used to build S3 keys — those live on
            ``generated_images.s3_key``).

    Returns:
        Dict with ``thread_id``, ``images_deleted``, and ``status``
        keys on success.  On permanent failure, ``status="failed"``
        plus ``error_type`` and ``error`` keys.
    """
    settings = get_settings()

    try:
        return asyncio.run(_process(thread_id, user_id, settings))
    except Exception as exc:
        if is_transient(exc):
            logger.warning(
                "delete_thread_transient_error_will_retry",
                thread_id=thread_id,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=str(exc),
                retries=self.request.retries,
            )
            raise self.retry(exc=exc) from exc
        logger.error(
            "delete_thread_permanent_error",
            thread_id=thread_id,
            user_id=user_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


async def _process(thread_id: str, user_id: str, settings) -> dict:
    """Async body — S3 → image rows → thread row (NFR-015).

    The DB pool is opened on the *current* event loop so asyncpg
    connections are bound here, not a closed loop from a previous
    ``asyncio.run()`` in the same worker process (see
    ``celery_app.py:36-49`` for the long-form rationale).

    Args:
        thread_id: UUID string of the thread to delete.
        user_id: Saleor user ID string (owner of the thread row).
        settings: Application settings.

    Returns:
        Success dict ``{"thread_id", "images_deleted", "status":
        "deleted"}``.  Any exception raised here triggers a Celery
        retry when transient, or a permanent-failure return when not.
    """
    await open_pools(settings.database_url)
    pool = get_asyncpg_pool()
    image_repo = ImageRepository(pool)
    thread_repo = ThreadRepository(pool)
    s3 = S3Service(settings)

    thread_uuid = uuid.UUID(thread_id)

    try:
        # Step 1: list images so we know which S3 keys to delete.
        images = await image_repo.list_by_thread(thread_uuid)
        logger.info(
            "delete_thread_s3_phase_start",
            thread_id=thread_id,
            image_count=len(images),
        )

        # Step 2: delete S3 objects FIRST.  Any failure here aborts
        # the chain (NFR-015) — we never delete image rows that still
        # reference a live S3 key.  ``S3Service.delete`` is idempotent
        # (204 on missing key) so the eventual retry is safe.
        for image in images:
            await s3.delete(image.s3_key)

        # Step 3: delete image records.  ``delete_by_thread`` returns
        # the row count for the operator-visible summary.
        images_deleted = await image_repo.delete_by_thread(thread_uuid)

        # Step 4: delete the thread row.  Owner-scoped; returns False
        # if the row was already gone (idempotent re-run is fine).
        await thread_repo.delete(thread_uuid, user_id)
    finally:
        # Always release boto3 connections, even on failure.
        await s3.close()

    logger.info(
        "delete_thread_done",
        thread_id=thread_id,
        images_deleted=images_deleted,
    )
    return {
        "thread_id": thread_id,
        "images_deleted": images_deleted,
        "status": "deleted",
    }
