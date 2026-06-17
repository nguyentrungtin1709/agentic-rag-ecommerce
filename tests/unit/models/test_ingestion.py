"""Unit tests — IngestionJob and IngestionBatch Pydantic models.

These tests verify field defaults and field-level validation in
isolation.  Round-trip DB marshalling is covered by the repository
tests in ``test_ingestion_repo.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.ingestion import IngestionBatch, IngestionJob


def test_ingestion_job_default_status_is_pending() -> None:
    """``IngestionJob`` defaults to status=pending and zero counters."""
    job = IngestionJob(id=uuid.uuid4(), celery_task_id="celery-123")

    assert job.status == "pending"
    assert job.total_products == 0
    assert job.total_batches == 0
    assert job.processed_count == 0
    assert job.failed_count == 0
    assert job.started_at is None
    assert job.completed_at is None
    assert job.error_message is None


def test_ingestion_job_accepts_explicit_terminal_status() -> None:
    """Terminal status strings are accepted verbatim."""
    now = datetime.now(UTC)
    job = IngestionJob(
        id=uuid.uuid4(),
        celery_task_id="celery-456",
        status="completed",
        total_products=100,
        total_batches=1,
        processed_count=1,
        failed_count=0,
        started_at=now,
        completed_at=now,
    )

    assert job.status == "completed"
    assert job.total_products == 100
    assert job.total_batches == 1
    assert job.processed_count == 1
    assert job.started_at == now
    assert job.completed_at == now


def test_ingestion_batch_default_status_is_pending() -> None:
    """``IngestionBatch`` defaults to status=pending with empty products."""
    batch = IngestionBatch(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        batch_index=0,
    )

    assert batch.status == "pending"
    assert batch.product_ids == []
    assert batch.skipped_products == []
    assert batch.retry_count == 0
    assert batch.error_type is None
    assert batch.error_message is None
    assert batch.started_at is None
    assert batch.completed_at is None


def test_ingestion_batch_skipped_products_defaults_to_empty_list() -> None:
    """The ``skipped_products`` field default-factory must return a new list.

    Pydantic ``Field(default_factory=list)`` shares no state across
    instances — verify by mutating one and confirming the other is
    unaffected.
    """
    batch_a = IngestionBatch(id=uuid.uuid4(), job_id=uuid.uuid4(), batch_index=0)
    batch_b = IngestionBatch(id=uuid.uuid4(), job_id=uuid.uuid4(), batch_index=1)

    batch_a.skipped_products.append({"product_id": "p-1", "stage": "cleaning", "error": "x"})

    assert batch_b.skipped_products == []


def test_ingestion_batch_accepts_full_failure_state() -> None:
    """A permanently-failed batch carries the error context."""
    now = datetime.now(UTC)
    batch = IngestionBatch(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        batch_index=2,
        status="failed",
        product_ids=["p-1", "p-2"],
        retry_count=2,
        error_type="permanent",
        error_message="schema mismatch",
        started_at=now,
        completed_at=now,
    )

    assert batch.status == "failed"
    assert batch.retry_count == 2
    assert batch.error_type == "permanent"
    assert batch.error_message == "schema mismatch"
    assert batch.product_ids == ["p-1", "p-2"]
