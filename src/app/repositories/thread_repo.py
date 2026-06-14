"""Thread repository — raw SQL CRUD over the ``threads`` table.

Uses ``asyncpg`` directly (no ORM).  All methods accept a pool acquired
from ``app.db.session.get_asyncpg_pool()``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
import structlog

from app.models.thread import Thread

logger = structlog.get_logger(__name__)


class ThreadRepository:
    """Data-access layer for the ``threads`` table.

    Args:
        pool: An active ``asyncpg.Pool`` instance.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, user_id: str) -> Thread:
        """Insert a new thread row and return the domain model.

        Args:
            user_id: Saleor user ID from the JWT.

        Returns:
            The newly created ``Thread``.
        """
        thread_id = uuid.uuid4()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO threads (id, user_id)
                VALUES ($1, $2)
                RETURNING id, user_id, title, status, title_generated,
                          title_generation_attempts, created_at, updated_at,
                          last_activity_at
                """,
                thread_id,
                user_id,
            )
        thread = Thread(**dict(row))
        logger.info("Thread created", thread_id=str(thread.id), user_id=user_id)
        return thread

    async def get(self, thread_id: uuid.UUID, user_id: str) -> Thread | None:
        """Fetch a single thread by ID, scoped to the given user.

        Args:
            thread_id: UUID of the thread.
            user_id: Must match the thread's ``user_id`` column.

        Returns:
            ``Thread`` if found and owned by ``user_id``, else ``None``.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, title, status, title_generated,
                       title_generation_attempts, created_at, updated_at,
                       last_activity_at
                FROM threads
                WHERE id = $1 AND user_id = $2
                """,
                thread_id,
                user_id,
            )
        return Thread(**dict(row)) if row else None

    async def list_by_user(
        self,
        user_id: str,
        limit: int = 20,
        before: uuid.UUID | None = None,
    ) -> list[Thread]:
        """Return threads owned by a user, newest first (cursor-based).

        Args:
            user_id: Filter by this user.
            limit: Maximum rows to return (default 20).
            before: Cursor — return threads older than the thread with
                this ID.  Pass ``None`` to fetch the first page (FR-015).

        Returns:
            List of ``Thread`` domain models.
        """
        async with self._pool.acquire() as conn:
            if before is None:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, title, status, title_generated,
                           title_generation_attempts, created_at, updated_at,
                           last_activity_at
                    FROM threads
                    WHERE user_id = $1
                    ORDER BY updated_at DESC, id DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
            else:
                cursor_row = await conn.fetchrow(
                    "SELECT updated_at FROM threads WHERE id = $1 AND user_id = $2",
                    before,
                    user_id,
                )
                if cursor_row is None:
                    return []
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, title, status, title_generated,
                           title_generation_attempts, created_at, updated_at,
                           last_activity_at
                    FROM threads
                    WHERE user_id = $1
                      AND (updated_at < $2
                           OR (updated_at = $2 AND id::text < $3::text))
                    ORDER BY updated_at DESC, id DESC
                    LIMIT $4
                    """,
                    user_id,
                    cursor_row["updated_at"],
                    str(before),
                    limit,
                )
        return [Thread(**dict(row)) for row in rows]

    async def list_all(
        self,
        limit: int = 20,
        before: uuid.UUID | None = None,
    ) -> list[Thread]:
        """Return threads across all users, newest first (cursor-based).

        Admin-only counterpart of :meth:`list_by_user` for the
        ``GET /api/v1/admin/threads`` endpoint (FR-104, Phase 9).
        No ``user_id`` predicate — admin operators see every thread.

        Note:
            The cursor is **not** ownership-checked (admin context).
            An operator may pass a thread ID belonging to any user; the
            cursor resolves against the global rowset and either returns
            the next page or an empty list if the ID is unknown.

        Args:
            limit: Maximum rows to return (default 20).
            before: Cursor — return threads older than the thread with
                this ID.  Pass ``None`` for the first page.

        Returns:
            List of ``Thread`` domain models in newest-first order.
        """
        async with self._pool.acquire() as conn:
            if before is None:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, title, status, title_generated,
                           title_generation_attempts, created_at, updated_at,
                           last_activity_at
                    FROM threads
                    ORDER BY updated_at DESC, id DESC
                    LIMIT $1
                    """,
                    limit,
                )
            else:
                cursor_row = await conn.fetchrow(
                    "SELECT updated_at FROM threads WHERE id = $1",
                    before,
                )
                if cursor_row is None:
                    return []
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, title, status, title_generated,
                           title_generation_attempts, created_at, updated_at,
                           last_activity_at
                    FROM threads
                    WHERE (updated_at < $1
                           OR (updated_at = $1 AND id::text < $2::text))
                    ORDER BY updated_at DESC, id DESC
                    LIMIT $3
                    """,
                    cursor_row["updated_at"],
                    str(before),
                    limit,
                )
        return [Thread(**dict(row)) for row in rows]

    async def set_status(self, thread_id: uuid.UUID, status: str) -> None:
        """Update the lifecycle status of a thread.

        Valid values: ``'idle'``, ``'busy'``, ``'deleting'``.
        Also bumps ``updated_at`` (FR-013).

        Args:
            thread_id: UUID of the thread.
            status: New status string.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE threads
                SET status = $1, updated_at = now()
                WHERE id = $2
                """,
                status,
                thread_id,
            )
        logger.debug("Thread status updated", thread_id=str(thread_id), status=status)

    async def touch(self, thread_id: uuid.UUID) -> None:
        """Refresh ``last_activity_at`` and ``updated_at`` for a thread.

        Called on every successful chat run to keep the 30-day expiry
        window accurate (FR-018).

        Args:
            thread_id: UUID of the thread to touch.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE threads
                SET last_activity_at = now(), updated_at = now()
                WHERE id = $1
                """,
                thread_id,
            )

    async def update_title(self, thread_id: uuid.UUID, title: str) -> None:
        """Finalise the auto-generated title for an existing thread.

        Sets ``title_generated = TRUE`` to prevent further updates (FR-024)
        and resets ``title_generation_attempts`` now that the title is done.

        Args:
            thread_id: UUID of the thread to update.
            title: Finalised title string.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE threads
                SET title = $1,
                    title_generated = TRUE,
                    updated_at = now()
                WHERE id = $2
                """,
                title,
                thread_id,
            )
        logger.info("Thread title updated", thread_id=str(thread_id))

    async def increment_title_attempts(self, thread_id: uuid.UUID) -> int:
        """Increment the title-generation attempt counter and return the new value.

        Called each time a title-generation LLM call fails so the caller
        can decide whether to keep retrying or fall back to truncation (FR-023).

        Args:
            thread_id: UUID of the thread.

        Returns:
            The new value of ``title_generation_attempts`` after incrementing.
        """
        async with self._pool.acquire() as conn:
            new_count: int = await conn.fetchval(
                """
                UPDATE threads
                SET title_generation_attempts = title_generation_attempts + 1,
                    updated_at = now()
                WHERE id = $1
                RETURNING title_generation_attempts
                """,
                thread_id,
            )
        logger.debug(
            "Title generation attempt incremented",
            thread_id=str(thread_id),
            attempts=new_count,
        )
        return new_count

    async def delete(self, thread_id: uuid.UUID, user_id: str) -> bool:
        """Hard-delete a thread row (used by the Celery cleanup task).

        For user-facing soft-delete, use ``set_status(thread_id, 'deleting')``
        and enqueue the cleanup Celery task instead (FR-017).

        Args:
            thread_id: UUID of the thread.
            user_id: Owner check — will not delete if mismatch.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM threads WHERE id = $1 AND user_id = $2",
                thread_id,
                user_id,
            )
        deleted = result == "DELETE 1"
        if deleted:
            logger.info("Thread deleted", thread_id=str(thread_id), user_id=user_id)
        return deleted

    async def find_expired(self, cutoff: datetime) -> list[uuid.UUID]:
        """Return IDs of threads whose last activity is older than ``cutoff``.

        Used by the periodic cleanup job (Phase 10) to select threads
        for hard-deleting after the 30-day inactivity window (FR-018).
        Excludes threads already in ``status='deleting'`` so a second
        job invocation cannot race the first to produce duplicate work.

        Args:
            cutoff: Threshold timestamp; threads with
                ``last_activity_at < cutoff`` are returned.

        Returns:
            List of thread UUIDs eligible for cleanup.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id
                FROM threads
                WHERE last_activity_at < $1
                  AND status != 'deleting'
                """,
                cutoff,
            )
        return [row["id"] for row in rows]
