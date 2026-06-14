"""Unit tests — ``/api/v1/admin/...`` endpoints (Phase 6 + Phase 9).

The Phase 6 endpoints (``POST /reindex``, ``GET /reindex/{job_id}``)
are covered in ``tests/integration/test_reindex.py`` against the real
Docker stack — this file focuses on the Phase 9 list endpoints
(``GET /admin/threads`` and ``GET /admin/reindex``) and a small
suite of Phase 6 contract assertions that don't need Celery to be
running.

The tests follow the Phase 8 pattern from
``tests/unit/api/test_threads.py``: minimal in-process FastAPI app,
``app.dependency_overrides`` to swap repos with ``AsyncMock``
instances, slowapi storage re-pointed to an in-memory backend so
the ``@_limiter.limit(...)`` decorators don't need Valkey.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from httpx import ASGITransport, AsyncClient

from app.api.admin import router as admin_router
from app.dependencies import (
    IngestionBatchRepoDep,
    IngestionJobRepoDep,
    PoolDep,
    SettingsDep,
    ThreadRepoDep,
    get_current_user,
    get_db_pool,
    get_ingestion_batch_repo,
    get_ingestion_job_repo,
    get_thread_repo,
)
from app.models.ingestion import IngestionJob
from app.models.thread import Thread

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo_override(repo: Any) -> Callable[..., Any]:
    """Build a callable matching the ``get_*_repo(pool)`` signature so
    FastAPI routes the ``PoolDep`` annotation through the dep tree
    rather than treating ``pool`` as a query parameter."""

    def _override(pool: PoolDep) -> Any:  # type: ignore[no-untyped-def]
        return repo

    return _override


def _make_request_override(value: Any) -> Callable[..., Any]:
    """Same as ``_make_repo_override`` for request-scoped deps."""

    def _override(request: Request) -> Any:  # type: ignore[no-untyped-def]
        return value

    return _override


def _make_auth_override(claims: dict[str, Any]) -> Callable[..., Any]:
    """Return a callable that mimics ``get_current_user`` but skips
    JWT verification entirely (Phase 8 pattern — re-declare the
    ``Request`` parameter so FastAPI injects the live request)."""

    async def _override(
        request: Request,  # noqa: ARG001  -- framework-injected
        settings: SettingsDep = None,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        return claims

    return _override


def _make_thread(
    thread_id: uuid.UUID | None = None,
    *,
    user_id: str = "user-1",
    status: str = "idle",
    title: str | None = "Test thread",
) -> Thread:
    """Return a fully-populated ``Thread`` for assertion."""
    now = datetime(2026, 6, 14, tzinfo=UTC)
    return Thread(
        id=thread_id or uuid.uuid4(),
        user_id=user_id,
        title=title,
        status=status,
        title_generated=True,
        title_generation_attempts=0,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )


def _make_job(
    job_id: uuid.UUID | None = None,
    *,
    status: str = "completed",
    celery_task_id: str = "celery-1",
    total_products: int = 100,
    total_batches: int = 1,
    processed_count: int = 1,
    failed_count: int = 0,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error_message: str | None = None,
) -> IngestionJob:
    """Return a fully-populated ``IngestionJob`` for assertion."""
    return IngestionJob(
        id=job_id or uuid.uuid4(),
        celery_task_id=celery_task_id,
        status=status,
        total_products=total_products,
        total_batches=total_batches,
        processed_count=processed_count,
        failed_count=failed_count,
        started_at=started_at,
        completed_at=completed_at,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[FastAPI, None]:
    """Build a minimal FastAPI app that mounts only the admin router.

    The admin router depends on:
        - ``AdminDep`` (JWT + is_staff)
        - ``ThreadRepoDep`` for ``GET /admin/threads``
        - ``IngestionJobRepoDep`` for both reindex endpoints
        - ``IngestionBatchRepoDep`` for ``GET /admin/reindex/{job_id}``

    All four are wired through ``app.dependency_overrides`` per-test;
    this fixture just registers harmless defaults so the real
    ``get_asyncpg_pool()`` is never read.
    """
    from slowapi import Limiter

    from app.rate_limit import _reset_limiter_for_tests

    _reset_limiter_for_tests()
    test_limiter = Limiter(
        key_func=lambda: "test-key",
        storage_uri="memory://",
        headers_enabled=True,
    )
    # Re-point the production limiter's storage at an in-memory backend
    # so the ``@_limiter.limit(...)`` decorator closures captured at
    # import time still work without Valkey.
    from limits.storage import MemoryStorage
    from limits.strategies import STRATEGIES

    import app.api.admin as admin_module

    captured = admin_module._limiter
    captured._storage = MemoryStorage()
    captured._storage_dead = False
    captured._limiter = STRATEGIES["fixed-window"](captured._storage)
    captured._fallback_limiter = None

    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    app: FastAPI | None = None
    try:
        app = FastAPI()
        app.state.limiter = test_limiter
        # Admin router paths are relative ("/reindex", "/threads", etc.);
        # the production-style prefix is "/api/v1/admin".
        app.include_router(admin_router, prefix="/api/v1/admin")
        # Pre-register the deep providers with harmless defaults.
        app.dependency_overrides[get_db_pool] = lambda: MagicMock()
        yield app
    finally:
        await FastAPICache.clear()
        if app is not None:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /admin/threads  (Phase 9, D9.4)
# ---------------------------------------------------------------------------


async def test_list_all_threads_without_jwt_returns_401(app: FastAPI) -> None:
    """Missing ``Authorization`` header → 401 (reuses the bearer scheme)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/admin/threads")
    assert response.status_code == 401


async def test_list_all_threads_for_non_admin_returns_403(app: FastAPI) -> None:
    """``is_staff`` False → 403.  Same admin dep as the per-user endpoints."""
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "user-1", "is_staff": False}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads")
        assert response.status_code == 403
        thread_repo.list_all.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_returns_empty_when_no_threads(
    app: FastAPI,
) -> None:
    """``list_all`` returns ``[]`` → ``items: []`` and ``next_cursor: null``."""
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads")
        assert response.status_code == 200
        body = response.json()
        assert body == {"items": [], "next_cursor": None}
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_returns_items_with_next_cursor(
    app: FastAPI,
) -> None:
    """A full page (rows count == limit) yields ``next_cursor`` set to
    the last item's id; each item is serialised as a ``ThreadResponse``."""
    t1 = _make_thread()
    t2 = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[t1, t2])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads", params={"limit": 2})
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2
        assert body["items"][0]["id"] == str(t1.id)
        assert body["items"][1]["id"] == str(t2.id)
        # Full page → next_cursor points at the last item.
        assert body["next_cursor"] == str(t2.id)
        # The repo was called with the right (limit, before) pair.
        thread_repo.list_all.assert_awaited_once_with(limit=2, before=None)
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_partial_page_has_no_next_cursor(
    app: FastAPI,
) -> None:
    """A partial page (rows count < limit) yields ``next_cursor: null`` —
    there are no more pages to fetch."""
    t1 = _make_thread()
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[t1])  # only 1 row, limit=5
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads", params={"limit": 5})
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["next_cursor"] is None
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_forwards_cursor_param(app: FastAPI) -> None:
    """A non-null ``before`` query param is forwarded to the repo as
    a UUID; the response cursor reflects the last returned item."""
    t1 = _make_thread()
    cursor = uuid.uuid4()
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[t1])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/threads",
                params={"limit": 1, "before": str(cursor)},
            )
        assert response.status_code == 200
        thread_repo.list_all.assert_awaited_once_with(limit=1, before=cursor)
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_rejects_limit_above_100(app: FastAPI) -> None:
    """``Query(ge=1, le=100)`` rejects limit=101 with 422 (Pydantic)."""
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads", params={"limit": 101})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_rejects_limit_below_1(app: FastAPI) -> None:
    """``Query(ge=1, le=100)`` rejects limit=0 with 422."""
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads", params={"limit": 0})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


async def test_list_all_threads_uses_default_limit_20(app: FastAPI) -> None:
    """No ``?limit=`` query param → the default limit of 20 is used
    and forwarded to the repo."""
    t1 = _make_thread()
    # Build 20 rows to simulate a full page (so next_cursor is set).
    rows = [t1] + [_make_thread() for _ in range(19)]
    thread_repo = AsyncMock()
    thread_repo.list_all = AsyncMock(return_value=rows)
    app.dependency_overrides[get_thread_repo] = _make_repo_override(thread_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/threads")
        assert response.status_code == 200
        thread_repo.list_all.assert_awaited_once_with(limit=20, before=None)
        body = response.json()
        assert len(body["items"]) == 20
        # next_cursor is the last item's id.
        assert body["next_cursor"] == str(rows[-1].id)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /admin/reindex  (Phase 9, D9.5' + D9.6')
# ---------------------------------------------------------------------------


async def test_list_reindex_jobs_without_jwt_returns_401(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/admin/reindex")
    assert response.status_code == 401


async def test_list_reindex_jobs_for_non_admin_returns_403(app: FastAPI) -> None:
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "user-1", "is_staff": False}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/reindex")
        assert response.status_code == 403
        job_repo.list_all.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


async def test_list_reindex_jobs_returns_empty_when_no_jobs(
    app: FastAPI,
) -> None:
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/reindex")
        assert response.status_code == 200
        body = response.json()
        assert body == {"items": [], "next_cursor": None}
    finally:
        app.dependency_overrides.clear()


async def test_list_reindex_jobs_serialises_id_as_job_id(app: FastAPI) -> None:
    """``IngestionJobSummary`` renames the underlying ``id`` field to
    ``job_id`` in JSON via Pydantic v2 ``Field(alias="id")`` (D9.6').
    Verify the field rename lands in the response payload."""
    j1 = _make_job(status="processing")
    j2 = _make_job(status="completed", completed_at=datetime(2026, 6, 14, 11, 0, tzinfo=UTC))
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[j1, j2])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/reindex", params={"limit": 2})
        assert response.status_code == 200
        body = response.json()
        # The field is "job_id" in the wire format, NOT "id".
        assert body["items"][0]["job_id"] == str(j1.id)
        assert body["items"][1]["job_id"] == str(j2.id)
        # Underlying fields are present too.
        assert body["items"][0]["status"] == "processing"
        assert body["items"][1]["status"] == "completed"
        assert body["items"][0]["celery_task_id"] == "celery-1"
        # next_cursor is the last item's id (j2).
        assert body["next_cursor"] == str(j2.id)
    finally:
        app.dependency_overrides.clear()


async def test_list_reindex_jobs_excludes_batches_array(app: FastAPI) -> None:
    """D9.6' — the summary response must NOT include ``batches[]``;
    operators drill into ``GET /admin/reindex/{job_id}`` for batch detail."""
    j1 = _make_job()
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[j1])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/reindex")
        assert response.status_code == 200
        body = response.json()
        assert "batches" not in body["items"][0]
    finally:
        app.dependency_overrides.clear()


async def test_list_reindex_jobs_forwards_cursor_param(app: FastAPI) -> None:
    """A non-null ``before`` is forwarded to the repo as a UUID."""
    j1 = _make_job()
    cursor = uuid.uuid4()
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[j1])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/reindex",
                params={"limit": 1, "before": str(cursor)},
            )
        assert response.status_code == 200
        job_repo.list_all.assert_awaited_once_with(limit=1, before=cursor)
    finally:
        app.dependency_overrides.clear()


async def test_list_reindex_jobs_partial_page_no_next_cursor(
    app: FastAPI,
) -> None:
    j1 = _make_job()
    job_repo = AsyncMock()
    job_repo.list_all = AsyncMock(return_value=[j1])  # only 1 row
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/reindex", params={"limit": 5})
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["next_cursor"] is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /admin/reindex  (Phase 6 — minimal contract assertion)
# ---------------------------------------------------------------------------


async def test_trigger_reindex_for_non_admin_returns_403(app: FastAPI) -> None:
    """Admin dep is enforced even on the reindex trigger (FR-085)."""
    job_repo = AsyncMock()
    job_repo.create = AsyncMock()
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "user-1", "is_staff": False}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/admin/reindex")
        assert response.status_code == 403
        job_repo.create.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


async def test_trigger_reindex_creates_job_and_dispatches_celery(
    app: FastAPI,
) -> None:
    """``POST /admin/reindex`` creates a job row with a placeholder ID,
    then patches in the real Celery task ID after ``.apply_async()`` returns
    (Phase 6 implementation detail — verifies the dual-write contract)."""
    new_job_id = uuid.uuid4()
    placeholder_job = _make_job(job_id=new_job_id, celery_task_id="pending")
    job_repo = AsyncMock()
    job_repo.create = AsyncMock(return_value=placeholder_job)
    job_repo.set_celery_task_id = AsyncMock(return_value=None)
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    # ``run_ingestion_job.apply_async`` must return an object with
    # ``.id`` so the endpoint can patch the real task ID back in.
    fake_result = MagicMock()
    fake_result.id = "real-celery-task-id"

    with patch("app.api.admin.run_ingestion_job") as fake_task:
        fake_task.apply_async = MagicMock(return_value=fake_result)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/v1/admin/reindex")
            assert response.status_code == 202
            body = response.json()
            assert body["job_id"] == str(new_job_id)
            assert body["status"] == "processing"
            # The create was called with a placeholder task ID.
            job_repo.create.assert_awaited_once()
            create_arg = job_repo.create.await_args.kwargs["celery_task_id"]
            assert create_arg.startswith("pending-")
            # Celery was dispatched with the job id on the "reindex" queue.
            fake_task.apply_async.assert_called_once()
            apply_args = fake_task.apply_async.call_args
            assert apply_args.kwargs["args"] == [str(new_job_id)]
            assert apply_args.kwargs["queue"] == "reindex"
            # The placeholder was patched with the real ID.
            job_repo.set_celery_task_id.assert_awaited_once_with(new_job_id, "real-celery-task-id")
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /admin/reindex/{job_id}  (Phase 6 — minimal contract assertion)
# ---------------------------------------------------------------------------


async def test_get_reindex_status_returns_404_for_unknown_job(
    app: FastAPI,
) -> None:
    job_repo = AsyncMock()
    job_repo.get = AsyncMock(return_value=None)
    batch_repo = AsyncMock()
    batch_repo.list_by_job = AsyncMock(return_value=[])
    app.dependency_overrides[get_ingestion_job_repo] = _make_repo_override(job_repo)
    app.dependency_overrides[get_ingestion_batch_repo] = _make_repo_override(batch_repo)
    app.dependency_overrides[get_current_user] = _make_auth_override(
        {"sub": "admin-1", "is_staff": True}
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/admin/reindex/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "not found" in response.text.lower()
    finally:
        app.dependency_overrides.clear()


# Reference the unused imports to keep pyright happy.
_ = (IngestionBatchRepoDep, IngestionJobRepoDep, ThreadRepoDep)
