"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Load .env.local (git-ignored) at import time so the integration test
# fixtures can pick up SALEOR_ADMIN_EMAIL / SALEOR_TEST_USER_* etc.
# See ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 6 for the variables
# this file is expected to provide.
try:
    from dotenv import load_dotenv

    _env_local = Path(__file__).resolve().parent.parent / ".env.local"
    if _env_local.is_file():
        load_dotenv(_env_local, override=False)
except ImportError:
    # python-dotenv is optional; the integration fixtures will skip
    # with a clear message if the env vars are missing.
    pass


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Use asyncio backend for pytest-asyncio."""
    return "asyncio"


@pytest.fixture
def settings_overrides(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Override settings with safe test values.

    Clears the ``get_settings`` LRU cache before and after each test
    so that monkeypatched env vars take effect.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    yield
    get_settings.cache_clear()


@pytest.fixture
def mock_asyncpg_pool() -> tuple[MagicMock, AsyncMock]:
    """Return a mock asyncpg pool with a usable acquire context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn
