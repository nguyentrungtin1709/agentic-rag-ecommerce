"""Celery task — clean up threads that have been inactive for 30+ days.

This task is scheduled by Celery Beat to run nightly at 2:00 AM UTC.
It finds all threads where ``last_activity_at < now() - INTERVAL '30 days'``
and performs a full cleanup: S3 objects deleted before database records to
avoid orphaned storage (NFR-015).
"""

from __future__ import annotations

import structlog

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
    """Delete all threads inactive for more than 30 days and their assets.

    This is a stub.  Full implementation will:
    1. Query the ``threads`` table for rows where
       ``last_activity_at < now() - INTERVAL '30 days'``.
    2. For each expired thread:
       a. Fetch all associated ``generated_images`` records.
       b. Delete S3 objects for each image
          (``images/{user_id}/{thread_id}/*.png``).
       c. Delete ``generated_images`` rows for the thread.
       d. Delete the ``threads`` row (LangGraph checkpointer rows are
          automatically cleaned up via cascade or a separate step).
    3. Return a summary dict with counts.

    Returns:
        Dict with ``threads_deleted``, ``images_deleted``, and
        ``duration_seconds`` keys.
    """
    logger.info("cleanup_expired_threads task started (stub)")
    return {"threads_deleted": 0, "images_deleted": 0, "duration_seconds": 0.0}
