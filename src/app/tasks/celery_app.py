"""Celery application factory.

Do NOT import this at the top-level of the FastAPI app module — it is
only needed by the Celery worker process and by task callers that use
``.delay()`` / ``.apply_async()``.  Import the ``celery_app`` singleton
directly:

    from app.tasks.celery_app import celery_app
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "agentic_rag_ecommerce",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=[
        "app.tasks.process_webhook",
        "app.tasks.reindex_products",
        "app.tasks.cleanup_expired_threads",
        "app.tasks.delete_thread",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Celery Beat schedule.
    beat_schedule={
        "cleanup-expired-threads-nightly": {
            "task": "tasks.cleanup_expired_threads",
            "schedule": crontab(hour=2, minute=0),  # daily at 02:00 UTC
            "options": {"queue": "cleanup"},
        },
    },
)
