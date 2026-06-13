"""Unit tests — ``process_batch`` worker Celery task + helpers.

Covers the worker's classification of transient vs permanent errors
and the per-batch DB updates.
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest

from app.models.ingestion import IngestionBatch, IngestionJob
from app.tasks.process_batch import _is_transient, process_batch

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _invoke_task(fake_self: MagicMock, batch_id: str) -> dict:
    """Invoke the underlying ``process_batch`` function with a fake self.

    Celery's ``.run()`` attribute does not pass ``self``, so the only
    way to inject a controlled ``request.retries`` / ``retry()`` is to
    bind the unbound ``process_batch`` function (the original
    function captured by the ``@celery_app.task(bind=True)``
    decorator) to a mock instance.
    """
    real_task = process_batch._get_current_object()  # type: ignore[attr-defined]
    bound = types.MethodType(real_task.run.__func__, fake_self)
    return bound(batch_id)


# ---------------------------------------------------------------------------
# _is_transient classifier
# ---------------------------------------------------------------------------


def test_is_transient_openai_rate_limit() -> None:
    """``openai.RateLimitError`` is transient."""
    # Use a constructor that doesn't require an HTTP response object.
    exc = openai.RateLimitError(message="rate limit", response=MagicMock(), body=None)
    assert _is_transient(exc) is True


def test_is_transient_openai_api_timeout() -> None:
    """``openai.APITimeoutError`` is transient."""
    exc = openai.APITimeoutError(request=MagicMock())
    assert _is_transient(exc) is True


def test_is_transient_openai_internal_server_error() -> None:
    """``openai.InternalServerError`` is transient."""
    exc = openai.InternalServerError(message="boom", response=MagicMock(), body=None)
    assert _is_transient(exc) is True


def test_is_transient_httpx_connect_error() -> None:
    """``httpx.ConnectError`` is transient."""
    exc = httpx.ConnectError("refused")
    assert _is_transient(exc) is True


def test_is_transient_httpx_read_timeout() -> None:
    """``httpx.ReadTimeout`` is transient."""
    exc = httpx.ReadTimeout("slow")
    assert _is_transient(exc) is True


def test_is_transient_httpx_connect_timeout() -> None:
    """``httpx.ConnectTimeout`` is transient."""
    exc = httpx.ConnectTimeout("slow")
    assert _is_transient(exc) is True


def test_is_transient_qdrant_unexpected_response() -> None:
    """``qdrant_client.http.exceptions.UnexpectedResponse`` is transient."""
    import httpx
    import qdrant_client.http.exceptions as qdrant_exc

    exc = qdrant_exc.UnexpectedResponse(
        status_code=503,
        reason_phrase="Service Unavailable",
        content=b"",
        headers=httpx.Headers(),
    )
    assert _is_transient(exc) is True


def test_is_transient_value_error_is_permanent() -> None:
    """``ValueError`` is not in the transient whitelist -> permanent."""
    assert _is_transient(ValueError("bad data")) is False


def test_is_transient_runtime_error_is_permanent() -> None:
    """``RuntimeError`` is not in the transient whitelist -> permanent."""
    assert _is_transient(RuntimeError("oops")) is False


def test_is_transient_key_error_is_permanent() -> None:
    """``KeyError`` is not in the transient whitelist -> permanent."""
    assert _is_transient(KeyError("missing")) is False


# ---------------------------------------------------------------------------
# process_batch (sync wrapper)
# ---------------------------------------------------------------------------


def _make_batch_row(batch_id: uuid.UUID, job_id: uuid.UUID) -> IngestionBatch:
    return IngestionBatch(
        id=batch_id,
        job_id=job_id,
        batch_index=0,
        status="pending",
        product_ids=["p-1"],
    )


def _make_job_row(job_id: uuid.UUID, **overrides: object) -> IngestionJob:
    base = {
        "id": job_id,
        "celery_task_id": "x",
        "status": "processing",
        "total_products": 1,
        "total_batches": 1,
        "processed_count": 0,
        "failed_count": 0,
    }
    base.update(overrides)
    return IngestionJob(**base)


def test_process_batch_marks_done_on_success() -> None:
    """Successful index_batch -> mark_done + increment_processed + mark job completed."""
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()

    job_row = _make_job_row(job_id, processed_count=0, total_batches=1)

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=_make_batch_row(batch_id, job_id))
    batch_repo.mark_processing = AsyncMock()
    batch_repo.mark_done = AsyncMock()

    async def _increment_processed(_job_id: uuid.UUID) -> None:
        # Mutate the shared job_row so the subsequent job_repo.get()
        # returns a "finished" job and the worker calls update_status.
        job_row.processed_count += 1

    job_repo = MagicMock()
    job_repo.get = AsyncMock(return_value=job_row)
    job_repo.increment_processed = AsyncMock(side_effect=_increment_processed)
    job_repo.update_status = AsyncMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_products_by_ids = AsyncMock(return_value=[])
    fake_saleor.close = AsyncMock()

    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(return_value=(1, []))

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.process_batch.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
    ):
        result = _invoke_task(MagicMock(request=MagicMock(retries=0)), str(batch_id))

    assert result["status"] == "done"
    assert result["succeeded"] == 1
    assert result["skipped"] == 0
    batch_repo.mark_done.assert_awaited_once_with(batch_id, skipped_products=[])
    job_repo.increment_processed.assert_awaited_once_with(job_id)
    job_repo.update_status.assert_awaited_once_with(job_id, "completed")


def test_process_batch_marks_last_batch_partial_failed() -> None:
    """If the job has >=1 failed batch, the final transition is ``partial_failed``."""
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=_make_batch_row(batch_id, job_id))
    batch_repo.mark_processing = AsyncMock()
    batch_repo.mark_done = AsyncMock()

    job_repo = MagicMock()
    job_repo.get = AsyncMock(return_value=_make_job_row(job_id, failed_count=1))
    job_repo.increment_processed = AsyncMock()
    job_repo.update_status = AsyncMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_products_by_ids = AsyncMock(return_value=[])
    fake_saleor.close = AsyncMock()

    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(return_value=(1, []))

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.process_batch.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
    ):
        _invoke_task(MagicMock(request=MagicMock(retries=0)), str(batch_id))

    job_repo.update_status.assert_awaited_once_with(job_id, "partial_failed")


def test_process_batch_transient_error_triggers_retry() -> None:
    """``openai.RateLimitError`` -> ``self.retry`` is called with the exception."""
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=_make_batch_row(batch_id, job_id))
    batch_repo.mark_processing = AsyncMock()
    batch_repo.increment_retry = AsyncMock()

    job_repo = MagicMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_products_by_ids = AsyncMock(return_value=[{"id": "p-1"}])
    fake_saleor.close = AsyncMock()

    rate_limit = openai.RateLimitError(message="rate limit", response=MagicMock(), body=None)
    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(side_effect=rate_limit)

    # The sync ``.retry`` call on a Celery task raises ``Retry`` —
    # patch it to a no-op so the test can assert it was called.
    fake_self = MagicMock()
    fake_self.request.retries = 0
    fake_self.retry = MagicMock(side_effect=Exception("RETRY"))

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.process_batch.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
        pytest.raises(Exception, match="RETRY"),
    ):
        _invoke_task(fake_self, str(batch_id))

    fake_self.retry.assert_called_once()
    batch_repo.increment_retry.assert_awaited_once_with(batch_id)


def test_process_batch_permanent_error_marks_batch_failed() -> None:
    """``ValueError`` is permanent -> mark_failed + return failure dict (no re-raise)."""
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=_make_batch_row(batch_id, job_id))
    batch_repo.mark_processing = AsyncMock()
    batch_repo.mark_failed = AsyncMock()

    job_repo = MagicMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_products_by_ids = AsyncMock(return_value=[{"id": "p-1"}])
    fake_saleor.close = AsyncMock()

    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(side_effect=ValueError("malformed data"))

    fake_self = MagicMock()
    fake_self.request.retries = 0
    fake_self.retry = MagicMock()

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.process_batch.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
    ):
        result = _invoke_task(fake_self, str(batch_id))

    assert result["status"] == "failed"
    assert result["error_type"] == "permanent"
    assert "malformed data" in result["error"]
    batch_repo.mark_failed.assert_awaited_once()
    fake_self.retry.assert_not_called()


def test_process_batch_whole_batch_permanent_failure_marks_failed() -> None:
    """``PermanentProductError`` from the indexer -> batch failed + job failed_count++."""
    from app.rag.indexer import PermanentProductError

    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=_make_batch_row(batch_id, job_id))
    batch_repo.mark_processing = AsyncMock()
    batch_repo.mark_failed = AsyncMock()

    job_repo = MagicMock()
    job_repo.increment_failed = AsyncMock()

    fake_saleor = MagicMock()
    fake_saleor.fetch_products_by_ids = AsyncMock(return_value=[{"id": "p-1"}])
    fake_saleor.close = AsyncMock()

    fake_indexer = MagicMock()
    fake_indexer.index_batch = AsyncMock(
        side_effect=PermanentProductError("all 1 products in batch failed cleaning")
    )

    fake_self = MagicMock()
    fake_self.request.retries = 0
    fake_self.retry = MagicMock()

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.SaleorClient", return_value=fake_saleor),
        patch("app.tasks.process_batch.ProductIndexer", return_value=fake_indexer),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
    ):
        result = _invoke_task(fake_self, str(batch_id))

    assert result["status"] == "failed"
    batch_repo.mark_failed.assert_awaited_once()
    job_repo.increment_failed.assert_awaited_once_with(job_id)


def test_process_batch_batch_not_found_is_permanent() -> None:
    """A missing batch row raises ``ValueError`` -> permanent -> batch marked failed."""
    batch_id = uuid.uuid4()

    batch_repo = MagicMock()
    batch_repo.get = AsyncMock(return_value=None)
    batch_repo.mark_failed = AsyncMock()

    job_repo = MagicMock()

    fake_self = MagicMock()
    fake_self.request.retries = 0
    fake_self.retry = MagicMock()

    with (
        patch("app.tasks.process_batch.open_pools"),
        patch("app.tasks.process_batch.get_asyncpg_pool"),
        patch("app.tasks.process_batch.IngestionBatchRepository", return_value=batch_repo),
        patch("app.tasks.process_batch.IngestionJobRepository", return_value=job_repo),
    ):
        result = _invoke_task(fake_self, str(batch_id))

    assert result["status"] == "failed"
    assert result["error_type"] == "permanent"
    assert "not found" in result["error"]
    batch_repo.mark_failed.assert_awaited_once()
