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

    Args:
        database_url: The full DSN including scheme prefix.
    """
    global _asyncpg_pool, _psycopg_pool

    bare_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    _asyncpg_pool = await asyncpg.create_pool(bare_url, min_size=2, max_size=10)

    _psycopg_pool = AsyncConnectionPool(
        bare_url,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await _psycopg_pool.open()


async def close_pools() -> None:
    """Close both connection pools gracefully."""
    global _asyncpg_pool, _psycopg_pool

    if _asyncpg_pool is not None:
        await _asyncpg_pool.close()
        _asyncpg_pool = None

    if _psycopg_pool is not None:
        await _psycopg_pool.close()
        _psycopg_pool = None


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
