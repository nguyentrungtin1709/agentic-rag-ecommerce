"""Integration tests — PostgreSQL connectivity via asyncpg.

Verifies that:
- The asyncpg pool can connect to the live database.
- A basic query executes successfully.
- The Alembic version table is present (migration applied).
- Application tables created by the initial migration exist.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import asyncpg
import pytest_asyncio

from tests.integration.conftest import POSTGRES_DSN


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Open a short-lived asyncpg pool for the test module."""
    pool: asyncpg.Pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=2)
    yield pool
    await pool.close()


async def test_postgres_select_one(pg_pool: asyncpg.Pool) -> None:
    """SELECT 1 returns integer 1."""
    result = await pg_pool.fetchval("SELECT 1")
    assert result == 1


async def test_postgres_alembic_version_table_exists(pg_pool: asyncpg.Pool) -> None:
    """alembic_version table must exist after ``alembic upgrade head``."""
    exists = await pg_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'alembic_version'
        )
        """
    )
    assert exists is True, "alembic_version table not found — run 'alembic upgrade head'"


async def test_postgres_threads_table_exists(pg_pool: asyncpg.Pool) -> None:
    """threads table must exist after the initial migration."""
    exists = await pg_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'threads'
        )
        """
    )
    assert exists is True, "threads table not found — run 'alembic upgrade head'"


async def test_postgres_generated_images_table_exists(pg_pool: asyncpg.Pool) -> None:
    """generated_images table must exist after the initial migration."""
    exists = await pg_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'generated_images'
        )
        """
    )
    assert exists is True, "generated_images table not found — run 'alembic upgrade head'"
