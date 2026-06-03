"""Celery task — delete a specific thread and all associated assets.

This task is enqueued by ``DELETE /api/v1/threads/{thread_id}`` after the
thread status has been set to ``deleting`` (202 Accepted is returned to the
client immediately).  Cleanup is atomic: S3 objects are deleted before
database records to avoid orphaned storage (NFR-015).
"""

from __future__ import annotations

import structlog

from app.tasks.celery_app import celery_app

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
    """Delete a thread and all its associated S3 objects and image records.

    This is a stub.  Full implementation will:
    1. Fetch all ``generated_images`` records for ``thread_id``.
    2. Delete each S3 object (``images/{user_id}/{thread_id}/*.png``).
    3. Delete ``generated_images`` rows for the thread.
    4. Delete the ``threads`` row.
       (LangGraph checkpointer rows linked to this ``thread_id`` are cleaned
       up via a separate step or cascade delete on the checkpointer tables.)

    Args:
        self: Celery task instance (for retry support).
        thread_id: UUID string of the thread to delete.
        user_id: Saleor user ID string (used to build the S3 key prefix).

    Returns:
        Dict with ``thread_id``, ``images_deleted``, and ``status`` keys.
    """
    logger.info(
        "delete_thread task started (stub)",
        thread_id=thread_id,
        user_id=user_id,
    )
    return {"thread_id": thread_id, "images_deleted": 0, "status": "stub"}
