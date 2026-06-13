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
from kombu import Queue

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
        "app.tasks.run_ingestion_job",
        "app.tasks.process_batch",
    ],
)


# ── DB pool management in Celery workers ─────────────────────────────────────
#
# FastAPI's ``lifespan`` opens the pools in the API process.  The Celery
# worker is a separate process with no lifespan hook, so tasks open and
# close the pool themselves (see ``app.db.session``).
#
# Why NOT use ``worker_process_init`` to pre-open the pool: asyncpg binds
# connections to the running event loop at creation time.  Each
# ``asyncio.run()`` in a task creates a fresh loop, so a pool created in
# one loop is dead by the time the next ``asyncio.run()`` runs in the same
# process — surfaces as ``got Future ... attached to a different loop``.
# The fix: each task's ``asyncio.run(coro())`` opens the pool on entry
# (idempotent — reuses the existing module-level pool when it was opened
# on the same loop) and closes it on exit.

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Fix for RabbitMQ 4.3.0 — control/event queues must be exclusive (non-durable,
    # non-exclusive transient queues are rejected by RabbitMQ 4.3+).
    # This mirrors the fix in Celery PR #10290 (will be default in Celery 5.7.0).
    control_queue_exclusive=True,
    event_queue_exclusive=True,
    # Explicit durable queues — required for RabbitMQ 4.x which disallows
    # transient non-exclusive queues (deprecated_features.transient_nonexcl_queues).
    # FR-100: dedicated queues for webhook, reindex, and cleanup tasks.
    task_queues=[
        Queue("celery", durable=True),
        Queue("webhook", durable=True),
        Queue("reindex", durable=True),
        Queue("reindex_batches", durable=True),
        Queue("cleanup", durable=True),
    ],
    task_default_queue="celery",
    # Celery Beat schedule.
    beat_schedule={
        "cleanup-expired-threads-nightly": {
            "task": "tasks.cleanup_expired_threads",
            "schedule": crontab(hour=2, minute=0),  # daily at 02:00 UTC
            "options": {"queue": "cleanup"},
        },
    },
)
