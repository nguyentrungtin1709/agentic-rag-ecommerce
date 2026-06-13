"""Integration test fixtures.

These tests require the full Docker Compose stack to be running::

    docker compose up -d

Service connection strings default to ``localhost`` with host-mapped ports
so the tests can be executed from the developer machine.  Override via
environment variables when running from within another container.

    POSTGRES_TEST_DSN   — default: postgresql://app:changeme@localhost:5433/app
    QDRANT_TEST_URL     — default: http://localhost:6333
    VALKEY_TEST_URL     — default: redis://localhost:6380
    SALEOR_TEST_URL     — default: http://localhost:8000
    APP_TEST_URL        — default: http://localhost:8080
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Connection constants (can be overridden by env vars)
# ---------------------------------------------------------------------------

POSTGRES_DSN: str = os.environ.get(
    "POSTGRES_TEST_DSN",
    "postgresql://app:changeme@localhost:5433/app",
)
QDRANT_URL: str = os.environ.get("QDRANT_TEST_URL", "http://localhost:6333")
VALKEY_URL: str = os.environ.get("VALKEY_TEST_URL", "redis://localhost:6380")
SALEOR_URL: str = os.environ.get("SALEOR_TEST_URL", "http://localhost:8000")
APP_URL: str = os.environ.get("APP_TEST_URL", "http://localhost:8080")

# Override the .env-loaded QDRANT_URL (which points at the Docker-internal
# ``qdrant`` hostname) so application code that calls ``get_settings()``
# (e.g. ``ProductIndexer`` in the round-trip test) sees a host-reachable
# URL when pytest runs outside the Docker network.  Only set when the
# caller has not explicitly provided one.
os.environ.setdefault("QDRANT_URL", QDRANT_URL)


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """Return the PostgreSQL DSN for integration tests."""
    return POSTGRES_DSN


@pytest.fixture(scope="session")
def qdrant_url() -> str:
    """Return the Qdrant URL for integration tests."""
    return QDRANT_URL


@pytest.fixture(scope="session")
def valkey_url() -> str:
    """Return the Valkey/Redis URL for integration tests."""
    return VALKEY_URL


@pytest.fixture(scope="session")
def saleor_url() -> str:
    """Return the Saleor base URL for integration tests."""
    return SALEOR_URL


@pytest.fixture(scope="session")
def app_url() -> str:
    """Return the running FastAPI app base URL for integration tests."""
    return APP_URL
