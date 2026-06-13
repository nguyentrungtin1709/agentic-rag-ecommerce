"""Unit tests — ``run_ingestion_job`` orchestrator Celery task.

Tests use ``unittest.mock`` to stub the database, Saleor client, and
worker dispatch.  The ``.apply_async`` call is patched at the module
level so we can assert dispatch counts and args without a running
Celery broker.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.ingestion import IngestionBatch, IngestionJob
from app.tasks.run_ingestion_job import _orchestrate, run_ingestion_job


def _make_job_row(job_id: uuid.UUID) -> IngestionJob:
    return IngestionJob(
        id=job_id,
        celery_task_id="placeholder",
        status="processing",
        total_products=0,
        total_batches=0,
    )


def _make_batch_row(batch_id: uuid.UUID, job_id: uuid.UUID, idx: int) -> IngestionBatch:
    return IngestionBatch(
        id=batch_id,
        job_id=job_id,
        batch_index=idx,
        product_ids=[f"p-{idx}-1", f"p-{idx}-2"],
    )


# ---------------------------------------------------------------------------
# _orchestrate (async body)
# ---------------------------------------------------------------------------


async def test_orchestrate_creates_batch_rows_and_dispatches_workers() -> None:
    """One batch row is created per ``reindex_batch_size`` chunk + worker dispatched."""
    settings = MagicMock()
    settings.reindex_batch_size = 2
    settings.database_url = "postgresql://test/test"

    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.update_status = AsyncMock()
    batch_repo = MagicMock()
    # 5 products -> 3 batches (2, 2, 1)
    batch_ids = [uuid.uuid4() for _ in range(3)]
    batch_repo.create = AsyncMock(
        side_effect=[
            _make_batch_row(batch_ids[0], job_id, 0),
            _make_batch_row(batch_ids[1], job_id, 1),
            _make_batch_row(batch_ids[2], job_id, 2),
        ]
    )

    fake_products = [{"id": f"p-{i}"} for i in range(5)]

    fake_saleor = MagicMock()
    fake_saleor.fetch_all_products = AsyncMock(return_value=fake_products)
    fake_saleor.close = AsyncMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.run_ingestion_job.process_batch") as mock_worker,
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
    ):
        total_batches, dispatched = await _orchestrate(job_id, settings)

    assert total_batches == 3
    assert dispatched == 3
    assert batch_repo.create.await_count == 3
    assert mock_worker.apply_async.call_count == 3
    for call, expected_id in zip(mock_worker.apply_async.call_args_list, batch_ids, strict=True):
        assert call.kwargs["args"] == [str(expected_id)]
        assert call.kwargs["queue"] == "reindex_batches"


async def test_orchestrate_marks_job_processing_with_totals() -> None:
    """``update_status(processing, total_products=N, total_batches=M)`` is called."""
    settings = MagicMock()
    settings.reindex_batch_size = 100
    settings.database_url = "postgresql://test/test"

    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.update_status = AsyncMock()
    batch_repo = MagicMock()
    batch_repo.create = AsyncMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_all_products = AsyncMock(return_value=[{"id": "p-1"}])
    fake_saleor.close = AsyncMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.run_ingestion_job.process_batch"),
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
    ):
        await _orchestrate(job_id, settings)

    # First call: status=processing (initial)
    # Second call: status=processing with totals
    assert job_repo.update_status.await_count == 2
    second_call = job_repo.update_status.await_args_list[1]
    assert second_call.args == (job_id, "processing")
    assert second_call.kwargs == {"total_products": 1, "total_batches": 1}


async def test_orchestrate_handles_zero_products() -> None:
    """Zero products -> 0 batches, no batch rows, no worker dispatches."""
    settings = MagicMock()
    settings.reindex_batch_size = 100
    settings.database_url = "postgresql://test/test"

    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.update_status = AsyncMock()
    batch_repo = MagicMock()
    batch_repo.create = AsyncMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_all_products = AsyncMock(return_value=[])
    fake_saleor.close = AsyncMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.run_ingestion_job.process_batch") as mock_worker,
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
    ):
        total_batches, dispatched = await _orchestrate(job_id, settings)

    assert total_batches == 0
    assert dispatched == 0
    batch_repo.create.assert_not_awaited()
    mock_worker.apply_async.assert_not_called()


async def test_orchestrate_saleor_failure_marks_job_failed() -> None:
    """When ``fetch_all_products`` raises, the job is marked ``failed``.

    In production the sync ``run_ingestion_job`` wrapper calls
    ``_mark_failed`` after the orchestrator raises; that path is
    covered by ``test_run_ingestion_job_marks_failed_and_reraises_on_error``.
    Here we verify the orchestrator does not catch the error and
    leaves the job in the initial ``processing`` state for the
    wrapper to handle.
    """
    settings = MagicMock()
    settings.reindex_batch_size = 100
    settings.database_url = "postgresql://test/test"

    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.update_status = AsyncMock()
    batch_repo = MagicMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_all_products = AsyncMock(side_effect=RuntimeError("saleor down"))
    fake_saleor.close = AsyncMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.run_ingestion_job.process_batch"),
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
        pytest.raises(RuntimeError, match="saleor down"),
    ):
        await _orchestrate(job_id, settings)

    # Only the initial processing transition fired.  No failure update
    # was made from _orchestrate; the sync wrapper handles that.
    job_repo.update_status.assert_awaited_once_with(job_id, "processing")


# ---------------------------------------------------------------------------
# run_ingestion_job (sync wrapper)
# ---------------------------------------------------------------------------


def test_run_ingestion_job_returns_summary_dict() -> None:
    """The sync task returns ``{job_id, total_batches, dispatched}``."""
    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.get = AsyncMock(
        return_value=_make_job_row(job_id).model_copy(update={"total_batches": 3})
    )
    batch_repo = MagicMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job._orchestrate", new_callable=AsyncMock) as mock_orch,
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
    ):
        mock_orch.return_value = (3, 3)
        result = run_ingestion_job.run(str(job_id))  # type: ignore[attr-defined]

    assert result == {
        "job_id": str(job_id),
        "total_batches": 3,
        "dispatched": 3,
    }


def test_run_ingestion_job_marks_failed_and_reraises_on_error() -> None:
    """An unexpected error in ``_orchestrate`` marks the job failed and re-raises."""
    job_id = uuid.uuid4()
    job_repo = MagicMock()
    job_repo.update_status = AsyncMock()
    batch_repo = MagicMock()

    with (
        patch("app.tasks.run_ingestion_job.open_pools"),
        patch("app.tasks.run_ingestion_job.get_asyncpg_pool"),
        patch("app.tasks.run_ingestion_job._orchestrate", new_callable=AsyncMock) as mock_orch,
        patch("app.tasks.run_ingestion_job.IngestionJobRepository", return_value=job_repo),
        patch("app.tasks.run_ingestion_job.IngestionBatchRepository", return_value=batch_repo),
    ):
        mock_orch.side_effect = RuntimeError("orchestration exploded")
        with pytest.raises(RuntimeError, match="orchestration exploded"):
            run_ingestion_job.run(str(job_id))  # type: ignore[attr-defined]

    # _mark_failed opens a fresh pool + constructs its own job_repo, so
    # this assertion is on the *second* repo (built by the wrapper).
    assert job_repo.update_status.await_count == 1
    job_repo.update_status.assert_awaited_with(
        job_id, "failed", error_message="orchestration exploded"
    )
