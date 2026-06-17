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

Test-user credentials live in ``.env.local`` (git-ignored) — see
``docs/SALEOR-APP-WEBHOOK-SETUP.md`` Step 6 for the full
``accountRegister`` / ``staffCreate`` / ``confirmAccount`` /
``setPassword`` flow that creates the two test users.

    SALEOR_ADMIN_EMAIL          — admin login used by fixtures to mint JWTs
    SALEOR_ADMIN_PASSWORD
    SALEOR_TEST_USER_EMAIL      — regular customer (is_staff=false)
    SALEOR_TEST_USER_PASSWORD
    SALEOR_TEST_STAFF_EMAIL     — staff account (is_staff=true)
    SALEOR_TEST_STAFF_PASSWORD
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

# Test-user credentials — loaded from .env.local (git-ignored).  See the
# module docstring for the full list of supported variables.  Fall back
# to empty strings so the tests fail fast with a clear ``KeyError`` if
# the developer forgot to populate ``.env.local``.
SALEOR_ADMIN_EMAIL: str = os.environ.get("SALEOR_ADMIN_EMAIL", "")
SALEOR_ADMIN_PASSWORD: str = os.environ.get("SALEOR_ADMIN_PASSWORD", "")
SALEOR_TEST_USER_EMAIL: str = os.environ.get("SALEOR_TEST_USER_EMAIL", "")
SALEOR_TEST_USER_PASSWORD: str = os.environ.get("SALEOR_TEST_USER_PASSWORD", "")
SALEOR_TEST_STAFF_EMAIL: str = os.environ.get("SALEOR_TEST_STAFF_EMAIL", "")
SALEOR_TEST_STAFF_PASSWORD: str = os.environ.get("SALEOR_TEST_STAFF_PASSWORD", "")

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


# ---------------------------------------------------------------------------
# Saleor test-user credentials (see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def saleor_admin_credentials() -> tuple[str, str]:
    """Return ``(email, password)`` for the Saleor admin account.

    These are loaded from the ``SALEOR_ADMIN_EMAIL`` /
    ``SALEOR_ADMIN_PASSWORD`` env vars (typically populated from
    ``.env.local``).  Used by the JWT-minting fixture to obtain
    short-lived tokens for the regular and staff test users.
    """
    if not SALEOR_ADMIN_EMAIL or not SALEOR_ADMIN_PASSWORD:
        pytest.skip(
            "SALEOR_ADMIN_EMAIL / SALEOR_ADMIN_PASSWORD not set — "
            "see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6."
        )
    return SALEOR_ADMIN_EMAIL, SALEOR_ADMIN_PASSWORD


@pytest.fixture(scope="session")
def saleor_test_user_credentials() -> tuple[str, str]:
    """Return ``(email, password)`` for the regular test user.

    Loaded from ``SALEOR_TEST_USER_EMAIL`` /
    ``SALEOR_TEST_USER_PASSWORD``.
    """
    if not SALEOR_TEST_USER_EMAIL or not SALEOR_TEST_USER_PASSWORD:
        pytest.skip(
            "SALEOR_TEST_USER_EMAIL / SALEOR_TEST_USER_PASSWORD not set — "
            "see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6."
        )
    return SALEOR_TEST_USER_EMAIL, SALEOR_TEST_USER_PASSWORD


@pytest.fixture(scope="session")
def saleor_test_staff_credentials() -> tuple[str, str]:
    """Return ``(email, password)`` for the staff test user.

    Loaded from ``SALEOR_TEST_STAFF_EMAIL`` /
    ``SALEOR_TEST_STAFF_PASSWORD``.
    """
    if not SALEOR_TEST_STAFF_EMAIL or not SALEOR_TEST_STAFF_PASSWORD:
        pytest.skip(
            "SALEOR_TEST_STAFF_EMAIL / SALEOR_TEST_STAFF_PASSWORD not set — "
            "see docs/SALEOR-APP-WEBHOOK-SETUP.md Step 6."
        )
    return SALEOR_TEST_STAFF_EMAIL, SALEOR_TEST_STAFF_PASSWORD
