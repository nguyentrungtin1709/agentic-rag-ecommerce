"""Alembic environment configuration.

Uses an async SQLAlchemy engine so that ``alembic upgrade head`` works
with the same ``DATABASE_URL`` used at runtime.  No ORM metadata is
required — all migrations are written as raw SQL via ``op.execute()``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings

# The Alembic Config object, which provides access to alembic.ini values.
config = context.config
fileConfig(config.config_file_name)  # type: ignore[arg-type]

# No ORM metadata — migrations are raw SQL.
target_metadata = None


def _get_url() -> str:
    """Return the database URL from application settings."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection required).

    Generates SQL scripts that can be applied manually.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Create an async engine and run migrations within a connection."""
    url = _get_url()
    engine = create_async_engine(url, echo=False)

    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await engine.dispose()


def _do_run_migrations(connection: object) -> None:
    """Execute migrations inside a synchronous context (required by Alembic)."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
