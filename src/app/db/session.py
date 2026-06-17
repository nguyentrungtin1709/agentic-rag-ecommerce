"""Database session management.

Provides two connection pools:
- ``asyncpg_pool``: Used directly by repositories for raw SQL queries.
- ``psycopg_pool``: Used by LangGraph checkpoint and store (psycopg v3).

Both pools are created lazily at application startup via ``open_pools()``
and closed gracefully via ``close_pools()``.  Use the module-level
``get_asyncpg_pool()`` and ``get_psycopg_pool()`` helpers inside FastAPI
dependency functions.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

_asyncpg_pool: asyncpg.Pool | None = None
_psycopg_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None


async def open_pools(database_url: str) -> None:
    """Create and open both connection pools.

    Must be called once before the application begins serving requests.
    Converts the ``postgresql+psycopg://`` DSN (used by SQLAlchemy /
    Alembic) to the bare ``postgresql://`` form expected by asyncpg and
    psycopg_pool.

    Idempotent on the *current* event loop: when both pools are already
    open AND their connections are bound to the loop running this
    coroutine, this is a no-op.  When called from a different loop
    (Celery workers, where each ``asyncio.run()`` creates a fresh loop),
    the existing pool is dropped and recreated — see ``close_pools()``
    for why we cannot ``await pool.close()`` on a pool bound to a
    dead loop.

    Args:
        database_url: The full DSN including scheme prefix.
    """
    global _asyncpg_pool, _psycopg_pool

    current_loop = asyncio.get_running_loop()
    if (
        _asyncpg_pool is not None
        and _psycopg_pool is not None
        and _pool_loop(_asyncpg_pool) is current_loop
    ):
        return

    # Pool exists but is bound to a different (closed) loop — drop the
    # reference.  We cannot await close() on a pool bound to a dead loop
    # because it tries to schedule a ``call_later`` shutdown timer.
    _asyncpg_pool = None
    _psycopg_pool = None

    bare_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    async def _init_asyncpg_conn(conn: asyncpg.Connection) -> None:
        """Decode JSON / JSONB columns to native Python objects.

        Without this codec asyncpg returns ``json`` columns as raw
        strings, which then fail Pydantic validation when the model
        expects ``list[str]`` / ``dict``.
        """
        await conn.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    _asyncpg_pool = await asyncpg.create_pool(
        bare_url,
        min_size=2,
        max_size=10,
        init=_init_asyncpg_conn,
    )

    _psycopg_pool = AsyncConnectionPool(
        bare_url,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await _psycopg_pool.open()


async def close_pools() -> None:
    """Close both connection pools gracefully.

    Safe to call from any event loop.  When the existing pool's
    connections are bound to a *different* (closed) loop — the typical
    Celery-worker case where each ``asyncio.run()`` creates a fresh
    loop — we drop the reference without awaiting ``close()``: that
    would schedule a shutdown timer on the dead loop and raise
    ``RuntimeError: Event loop is closed``.
    """
    global _asyncpg_pool, _psycopg_pool

    current_loop = asyncio.get_running_loop()

    if _asyncpg_pool is not None:
        if _pool_loop(_asyncpg_pool) is current_loop:
            await _asyncpg_pool.close()
        _asyncpg_pool = None

    if _psycopg_pool is not None:
        if _pool_loop(_psycopg_pool) is current_loop:
            await _psycopg_pool.close()
        _psycopg_pool = None


def _pool_loop(pool: object) -> asyncio.AbstractEventLoop | None:
    """Return the event loop a pool is bound to, or None.

    Both asyncpg ``Pool`` and psycopg ``AsyncConnectionPool`` store the
    loop they were created on in a private attribute.  ``asyncpg.Pool``
    uses ``_loop``; ``psycopg_pool.AsyncConnectionPool`` uses ``_loop``
    as well but we read defensively in case the attribute name changes
    in a future release.
    """
    return getattr(pool, "_loop", None)


def get_asyncpg_pool() -> asyncpg.Pool:
    """Return the active asyncpg pool.

    Raises:
        RuntimeError: If ``open_pools()`` has not been called.
    """
    if _asyncpg_pool is None:
        raise RuntimeError("asyncpg pool is not initialised. Call open_pools() first.")
    return _asyncpg_pool


def get_psycopg_pool() -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """Return the active psycopg v3 async pool.

    Raises:
        RuntimeError: If ``open_pools()`` has not been called.
    """
    if _psycopg_pool is None:
        raise RuntimeError("psycopg pool is not initialised. Call open_pools() first.")
    return _psycopg_pool
