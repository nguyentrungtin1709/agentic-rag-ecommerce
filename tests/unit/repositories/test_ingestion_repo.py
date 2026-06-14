"""Unit tests — IngestionJobRepository and IngestionBatchRepository.

Tests use the ``mock_asyncpg_pool`` fixture from ``tests/conftest.py``
to verify the SQL and argument marshalling without needing a running
database.  Integration tests (``tests/integration/test_reindex.py``)
cover the real wire format.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.ingestion_repo import (
    IngestionBatchRepository,
    IngestionJobRepository,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def job_repo(mock_asyncpg_pool: tuple[MagicMock, AsyncMock]) -> IngestionJobRepository:
    """Return an ``IngestionJobRepository`` bound to the mock pool."""
    pool, _conn = mock_asyncpg_pool
    return IngestionJobRepository(pool)


@pytest.fixture
def batch_repo(mock_asyncpg_pool: tuple[MagicMock, AsyncMock]) -> IngestionBatchRepository:
    """Return an ``IngestionBatchRepository`` bound to the mock pool."""
    pool, _conn = mock_asyncpg_pool
    return IngestionBatchRepository(pool)


def _make_job_row(job_id: uuid.UUID, **overrides: object) -> dict:
    """Build a dict matching the ``_JOB_COLUMNS`` projection."""
    base: dict = {
        "id": job_id,
        "celery_task_id": "celery-1",
        "status": "pending",
        "total_products": 0,
        "total_batches": 0,
        "processed_count": 0,
        "failed_count": 0,
        "started_at": None,
        "completed_at": None,
        "error_message": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# IngestionJobRepository
# ---------------------------------------------------------------------------


async def test_job_create_inserts_pending_row(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``create`` issues an INSERT returning the new row and parses it."""
    _pool, conn = mock_asyncpg_pool
    new_id = uuid.uuid4()
    conn.fetchrow.return_value = _make_job_row(new_id, celery_task_id="celery-x")

    job = await job_repo.create(celery_task_id="celery-x")

    assert job.id == new_id
    assert job.celery_task_id == "celery-x"
    assert job.status == "pending"
    conn.fetchrow.assert_awaited_once()
    sql, task_id = conn.fetchrow.call_args.args
    assert "INSERT INTO ingestion_jobs (celery_task_id)" in sql
    assert "RETURNING" in sql
    assert task_id == "celery-x"


async def test_job_get_returns_none_for_missing(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``get`` returns ``None`` when the row is not found."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchrow.return_value = None

    result = await job_repo.get(uuid.uuid4())

    assert result is None


async def test_job_get_returns_row_when_found(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``get`` hydrates an ``IngestionJob`` from the row dict."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()
    conn.fetchrow.return_value = _make_job_row(job_id, status="completed")

    result = await job_repo.get(job_id)

    assert result is not None
    assert result.status == "completed"


async def test_job_set_celery_task_id_runs_update(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``set_celery_task_id`` issues an UPDATE with the two params."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()

    await job_repo.set_celery_task_id(job_id, "celery-real-id")

    sql, arg_job, arg_task = conn.execute.call_args.args
    assert "UPDATE ingestion_jobs" in sql
    assert "celery_task_id = $2" in sql
    assert "WHERE id = $1" in sql
    assert arg_job == job_id
    assert arg_task == "celery-real-id"


async def test_job_update_status_processing_sets_started_at(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """Transitioning to ``processing`` stamps ``started_at = now()``."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()

    await job_repo.update_status(job_id, "processing", total_products=10, total_batches=1)

    sql = conn.execute.call_args.args[0]
    assert "status = $2" in sql
    assert "total_products = $3" in sql
    assert "total_batches = $4" in sql
    assert "started_at = now()" in sql
    assert "completed_at" not in sql


async def test_job_update_status_completed_sets_completed_at(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """All terminal statuses stamp ``completed_at = now()``."""
    for terminal in ("completed", "partial_failed", "failed"):
        _pool, conn = mock_asyncpg_pool
        await job_repo.update_status(uuid.uuid4(), terminal)
        sql = conn.execute.call_args.args[0]
        assert "completed_at = now()" in sql, f"missing completed_at for {terminal}"


async def test_job_increment_processed_is_atomic_sql(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``increment_processed`` issues a single UPDATE with the counter."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()

    await job_repo.increment_processed(job_id)

    sql, arg = conn.execute.call_args.args
    assert "processed_count = processed_count + 1" in sql
    assert arg == job_id


async def test_job_increment_failed_is_atomic_sql(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``increment_failed`` issues a single UPDATE with the counter."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()

    await job_repo.increment_failed(job_id)

    sql, arg = conn.execute.call_args.args
    assert "failed_count = failed_count + 1" in sql
    assert arg == job_id


# ---------------------------------------------------------------------------
# IngestionJobRepository.list_all (Phase 9, D9.5')
# ---------------------------------------------------------------------------


async def test_job_list_all_first_page_uses_coalesce_infinity(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """``list_all`` sorts by ``COALESCE(started_at, 'infinity')`` DESC
    so pending jobs (no ``started_at`` yet) float to the top of the
    first page — the documented Phase 9 D9.5' ordering decision."""
    _pool, conn = mock_asyncpg_pool
    conn.fetch.return_value = []

    await job_repo.list_all(limit=20)

    sql, limit_arg = conn.fetch.call_args.args
    assert "ORDER BY COALESCE(started_at, 'infinity'::timestamptz) DESC" in sql
    assert "id::text DESC" in sql
    assert limit_arg == 20


async def test_job_list_all_first_page_hydrates_jobs(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """Rows from the first page are parsed into ``IngestionJob`` models."""
    _pool, conn = mock_asyncpg_pool
    j1 = uuid.uuid4()
    j2 = uuid.uuid4()
    conn.fetch.return_value = [
        _make_job_row(j1, status="processing", total_products=100, total_batches=1),
        _make_job_row(j2, status="completed", total_products=200, total_batches=2),
    ]

    result = await job_repo.list_all(limit=20)

    assert len(result) == 2
    assert [j.id for j in result] == [j1, j2]
    assert result[0].status == "processing"
    assert result[1].total_batches == 2


async def test_job_list_all_with_cursor_uses_coalesce_lt(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """The next-page query uses the same ``COALESCE(...) < $1``
    predicate as the sort key, so pending jobs (which have
    ``COALESCE(...) = 'infinity'``) are correctly skipped from the
    next page when the cursor also points at a pending job."""
    from datetime import UTC, datetime

    _pool, conn = mock_asyncpg_pool
    cursor_id = uuid.uuid4()
    cursor_sort = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)
    conn.fetchrow.return_value = {"sort_key": cursor_sort}
    conn.fetch.return_value = []

    await job_repo.list_all(limit=20, before=cursor_id)

    cursor_sql, cursor_arg = conn.fetchrow.call_args.args
    assert "COALESCE(started_at, 'infinity'::timestamptz) AS sort_key" in cursor_sql
    assert cursor_arg == cursor_id
    fetch_sql, fetch_sort, fetch_cursor_str, fetch_limit = conn.fetch.call_args.args
    assert "COALESCE(started_at, 'infinity'::timestamptz) < $1" in fetch_sql
    assert "id::text < $2::text" in fetch_sql
    assert fetch_sort == cursor_sort
    assert fetch_cursor_str == str(cursor_id)
    assert fetch_limit == 20


async def test_job_list_all_with_unknown_cursor_returns_empty(
    job_repo: IngestionJobRepository, mock_asyncpg_pool: tuple[MagicMock, AsyncMock]
) -> None:
    """An unknown cursor UUID short-circuits to ``[]`` without issuing
    the second ``fetch`` — matches the per-user thread list behaviour."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchrow.return_value = None

    result = await job_repo.list_all(limit=20, before=uuid.uuid4())

    assert result == []
    conn.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# IngestionBatchRepository
# ---------------------------------------------------------------------------


def _make_batch_row(batch_id: uuid.UUID, job_id: uuid.UUID, **overrides: object) -> dict:
    """Build a dict matching the ``_BATCH_COLUMNS`` projection."""
    base: dict = {
        "id": batch_id,
        "job_id": job_id,
        "batch_index": 0,
        "status": "pending",
        "product_ids": ["p-1", "p-2"],
        "skipped_products": [],
        "retry_count": 0,
        "error_type": None,
        "error_message": None,
        "started_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return base


async def test_batch_create_inserts_pending_row_with_product_ids(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``create`` passes ``product_ids`` as a Python list to a jsonb column.

    asyncpg's registered codec serialises the list to JSON on the wire —
    the repository itself must not pre-serialise (which would double-encode).
    """
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    conn.fetchrow.return_value = _make_batch_row(batch_id, job_id, batch_index=0)

    batch = await batch_repo.create(job_id, batch_index=0, product_ids=["p-1", "p-2"])

    assert batch.id == batch_id
    assert batch.product_ids == ["p-1", "p-2"]
    sql, arg_job, arg_idx, arg_ids = conn.fetchrow.call_args.args
    assert "INSERT INTO ingestion_batches" in sql
    assert "::jsonb" in sql
    assert arg_job == job_id
    assert arg_idx == 0
    assert arg_ids == ["p-1", "p-2"]


async def test_batch_get_returns_none_for_missing(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``get`` returns ``None`` when the row is not found."""
    _pool, conn = mock_asyncpg_pool
    conn.fetchrow.return_value = None

    result = await batch_repo.get(uuid.uuid4())

    assert result is None


async def test_batch_get_returns_row_when_found(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``get`` parses JSON columns to lists/dicts and hydrates the model."""
    _pool, conn = mock_asyncpg_pool
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()
    conn.fetchrow.return_value = _make_batch_row(
        batch_id,
        job_id,
        status="done",
        skipped_products=[{"product_id": "p-1", "stage": "cleaning", "error": "x"}],
    )

    result = await batch_repo.get(batch_id)

    assert result is not None
    assert result.status == "done"
    assert result.skipped_products == [{"product_id": "p-1", "stage": "cleaning", "error": "x"}]


async def test_batch_list_by_job_returns_ordered(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``list_by_job`` orders by ``batch_index`` ASC."""
    _pool, conn = mock_asyncpg_pool
    job_id = uuid.uuid4()
    conn.fetch.return_value = [
        _make_batch_row(uuid.uuid4(), job_id, batch_index=0),
        _make_batch_row(uuid.uuid4(), job_id, batch_index=1),
    ]

    result = await batch_repo.list_by_job(job_id)

    assert len(result) == 2
    sql, arg = conn.fetch.call_args.args
    assert "ORDER BY batch_index ASC" in sql
    assert arg == job_id


async def test_batch_mark_processing_stamps_started_at(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``mark_processing`` issues an UPDATE setting status + started_at."""
    _pool, conn = mock_asyncpg_pool
    batch_id = uuid.uuid4()

    await batch_repo.mark_processing(batch_id)

    sql, arg = conn.execute.call_args.args
    assert "status = 'processing'" in sql
    assert "started_at = now()" in sql
    assert arg == batch_id


async def test_batch_mark_done_persists_skipped_products(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``mark_done`` passes ``skipped_products`` as a Python list to a jsonb column.

    asyncpg's registered codec serialises the list to JSON on the wire —
    the repository itself must not pre-serialise (which would double-encode).
    """
    _pool, conn = mock_asyncpg_pool
    batch_id = uuid.uuid4()
    skipped = [
        {"product_id": "p-1", "stage": "cleaning", "error": "bad json"},
        {"product_id": "p-2", "stage": "summarization", "error": "llm down"},
    ]

    await batch_repo.mark_done(batch_id, skipped_products=skipped)

    sql, arg_id, arg_skipped = conn.execute.call_args.args
    assert "status = 'done'" in sql
    assert "completed_at = now()" in sql
    assert "::jsonb" in sql
    assert arg_id == batch_id
    assert arg_skipped == skipped


async def test_batch_mark_failed_records_error_type_and_message(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``mark_failed`` writes the error type, message, and retry count."""
    _pool, conn = mock_asyncpg_pool
    batch_id = uuid.uuid4()

    await batch_repo.mark_failed(
        batch_id,
        error_type="permanent",
        error_message="schema invalid",
        retry_count=2,
    )

    sql, arg_id, arg_type, arg_msg, arg_retries = conn.execute.call_args.args
    assert "status = 'failed'" in sql
    assert "error_type = $2" in sql
    assert "error_message = $3" in sql
    assert "retry_count = $4" in sql
    assert arg_id == batch_id
    assert arg_type == "permanent"
    assert arg_msg == "schema invalid"
    assert arg_retries == 2


async def test_batch_increment_retry_returns_new_value(
    batch_repo: IngestionBatchRepository,
    mock_asyncpg_pool: tuple[MagicMock, AsyncMock],
) -> None:
    """``increment_retry`` returns the new ``retry_count`` from RETURNING."""
    _pool, conn = mock_asyncpg_pool
    batch_id = uuid.uuid4()
    conn.fetchval.return_value = 3

    result = await batch_repo.increment_retry(batch_id)

    assert result == 3
    sql, arg = conn.fetchval.call_args.args
    assert "retry_count = retry_count + 1" in sql
    assert "RETURNING retry_count" in sql
    assert arg == batch_id
