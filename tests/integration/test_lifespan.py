"""Integration tests — application lifespan (Phase 5.7 + 5.8).

Verifies that the ``lifespan`` context manager in ``app.main``:

- Populates ``app.state`` with every shared resource declared in the
  Phase 5 ADR (``graph``, ``qdrant``, ``valkey``, ``cache_redis``,
  ``s3``, ``openai``).
- Calls ``S3Service.ensure_bucket`` so the pod fails readiness when
  Terraform has not provisioned the bucket.
- Closes every client in reverse construction order during shutdown
  (openai → s3 → cache_redis → valkey → qdrant → DB pools).
- Configures the ``AsyncOpenAI`` client with the API key from settings.

External services are mocked at the import boundary so the test does
not need the Docker Compose stack to be running.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _run_lifespan(app: FastAPI):
    """Run the app's lifespan context manager and yield the app."""
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
def app() -> FastAPI:
    """Build a fresh FastAPI app for each test (bypasses ``create_app``)."""
    return FastAPI()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_lifespan_populates_all_app_state(app: FastAPI) -> None:
    """All shared resources must be present on ``app.state`` after startup."""
    # Patch every external service and DB-touching call BEFORE we
    # import app.main so the patches take effect.
    with (
        patch("app.main.open_pools", new=AsyncMock()),
        patch("app.main.close_pools", new=AsyncMock()),
        patch("app.db.session.get_psycopg_pool") as mock_get_psycopg,
        patch("app.main.AsyncPostgresSaver") as mock_checkpointer,
        patch("app.main.AsyncPostgresStore") as mock_store,
        patch("app.main.build_graph") as mock_build_graph,
        patch("app.main.QdrantService") as mock_qdrant_cls,
        patch("app.main.ValkeyService") as mock_valkey_cls,
        patch("app.main.S3Service") as mock_s3_cls,
        patch("app.main.redis_from_url") as mock_redis_from_url,
        patch("app.main.FastAPICache"),
        patch("app.main.AsyncOpenAI") as mock_async_openai,
    ):
        # Return a mock psycopg pool so the lifespan can construct checkpointer/store.
        mock_get_psycopg.return_value = MagicMock(name="psycopg-pool")
        # Configure async mocks for awaitable construction methods.
        mock_checkpointer.return_value.setup = AsyncMock()
        mock_store.return_value.setup = AsyncMock()
        mock_build_graph.return_value = MagicMock(name="compiled-graph")

        mock_qdrant_instance = MagicMock(name="qdrant-service")
        mock_qdrant_instance.ensure_collection = AsyncMock()
        mock_qdrant_instance.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant_instance

        mock_valkey_instance = MagicMock(name="valkey-service")
        mock_valkey_instance.close = AsyncMock()
        mock_valkey_cls.return_value = mock_valkey_instance

        mock_s3_instance = MagicMock(name="s3-service")
        mock_s3_instance.ensure_bucket = AsyncMock()
        mock_s3_instance.close = AsyncMock()
        mock_s3_cls.return_value = mock_s3_instance

        mock_cache_redis = MagicMock(name="cache-redis")
        mock_cache_redis.aclose = AsyncMock()
        mock_redis_from_url.return_value = mock_cache_redis

        mock_openai_instance = MagicMock(name="openai-client")
        mock_openai_instance.close = AsyncMock()
        mock_async_openai.return_value = mock_openai_instance

        # We need a real ``get_settings`` (no patch).  Make sure the env
        # has minimum required values; the project-level fixture takes
        # care of that.

        # Import the lifespan AFTER patches are in place.
        from app.main import lifespan  # noqa: PLC0415

        # Replace the FastAPI app's lifespan with the patched one.
        app.router.lifespan_context = lambda _a: lifespan(_a)

        async with _run_lifespan(app) as running:
            # ── Verify every shared resource is on app.state ──────
            assert hasattr(running.state, "graph")
            assert hasattr(running.state, "qdrant")
            assert hasattr(running.state, "valkey")
            assert hasattr(running.state, "cache_redis")
            assert hasattr(running.state, "s3")
            assert hasattr(running.state, "openai")

            # ── Verify S3Service.ensure_bucket was called (fail-fast) ──
            mock_s3_instance.ensure_bucket.assert_awaited_once()

        # ── Verify all clients were closed in reverse order ──────
        mock_openai_instance.close.assert_awaited_once()
        mock_s3_instance.close.assert_awaited_once()
        mock_cache_redis.aclose.assert_awaited_once()
        mock_valkey_instance.close.assert_awaited_once()
        mock_qdrant_instance.close.assert_awaited_once()


async def test_s3_ensure_bucket_is_called_on_startup(app: FastAPI) -> None:
    """``S3Service.ensure_bucket`` must be awaited during startup (FR)."""
    with (
        patch("app.main.open_pools", new=AsyncMock()),
        patch("app.main.close_pools", new=AsyncMock()),
        patch("app.db.session.get_psycopg_pool") as mock_get_psycopg,
        patch("app.main.AsyncPostgresSaver") as mock_checkpointer,
        patch("app.main.AsyncPostgresStore") as mock_store,
        patch("app.main.build_graph"),
        patch("app.main.QdrantService") as mock_qdrant_cls,
        patch("app.main.ValkeyService") as mock_valkey_cls,
        patch("app.main.S3Service") as mock_s3_cls,
        patch("app.main.redis_from_url") as mock_redis_from_url,
        patch("app.main.FastAPICache"),
        patch("app.main.AsyncOpenAI") as mock_async_openai,
    ):
        mock_get_psycopg.return_value = MagicMock(name="psycopg-pool")
        mock_checkpointer.return_value.setup = AsyncMock()
        mock_store.return_value.setup = AsyncMock()
        mock_qdrant_instance = MagicMock()
        mock_qdrant_instance.ensure_collection = AsyncMock()
        mock_qdrant_instance.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant_instance
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.close = AsyncMock()
        mock_valkey_cls.return_value = mock_valkey_instance
        mock_s3_instance = MagicMock()
        mock_s3_instance.ensure_bucket = AsyncMock()
        mock_s3_instance.close = AsyncMock()
        mock_s3_cls.return_value = mock_s3_instance
        mock_redis_from_url.return_value = MagicMock(aclose=AsyncMock())
        mock_async_openai.return_value = MagicMock(close=AsyncMock())

        from app.main import lifespan  # noqa: PLC0415

        app.router.lifespan_context = lambda _a: lifespan(_a)

        async with _run_lifespan(app):
            pass

        mock_s3_instance.ensure_bucket.assert_awaited_once()


async def test_openai_client_uses_settings_api_key(app: FastAPI) -> None:
    """The OpenAI client is constructed with the settings API key."""
    with (
        patch("app.main.open_pools", new=AsyncMock()),
        patch("app.main.close_pools", new=AsyncMock()),
        patch("app.db.session.get_psycopg_pool") as mock_get_psycopg,
        patch("app.main.AsyncPostgresSaver") as mock_checkpointer,
        patch("app.main.AsyncPostgresStore") as mock_store,
        patch("app.main.build_graph"),
        patch("app.main.QdrantService") as mock_qdrant_cls,
        patch("app.main.ValkeyService") as mock_valkey_cls,
        patch("app.main.S3Service") as mock_s3_cls,
        patch("app.main.redis_from_url") as mock_redis_from_url,
        patch("app.main.FastAPICache"),
        patch("app.main.AsyncOpenAI") as mock_async_openai,
    ):
        mock_get_psycopg.return_value = MagicMock(name="psycopg-pool")
        mock_checkpointer.return_value.setup = AsyncMock()
        mock_store.return_value.setup = AsyncMock()
        mock_qdrant_instance = MagicMock()
        mock_qdrant_instance.ensure_collection = AsyncMock()
        mock_qdrant_instance.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant_instance
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.close = AsyncMock()
        mock_valkey_cls.return_value = mock_valkey_instance
        mock_s3_instance = MagicMock()
        mock_s3_instance.ensure_bucket = AsyncMock()
        mock_s3_instance.close = AsyncMock()
        mock_s3_cls.return_value = mock_s3_instance
        mock_redis_from_url.return_value = MagicMock(aclose=AsyncMock())
        mock_async_openai.return_value = MagicMock(close=AsyncMock())

        from app.config import get_settings  # noqa: PLC0415
        from app.main import lifespan  # noqa: PLC0415

        app.router.lifespan_context = lambda _a: lifespan(_a)

        async with _run_lifespan(app):
            pass

        # AsyncOpenAI must have been called with the configured API key.
        mock_async_openai.assert_called_once()
        kwargs = mock_async_openai.call_args.kwargs
        assert kwargs.get("api_key") == get_settings().openai_api_key


async def test_shutdown_closes_clients_in_reverse_construction_order(
    app: FastAPI,
) -> None:
    """Shutdown closes openai → s3 → cache_redis → valkey → qdrant."""
    call_order: list[str] = []

    async def _track(name: str):
        call_order.append(name)

    with (
        patch("app.main.open_pools", new=AsyncMock()),
        patch("app.main.close_pools", new=AsyncMock()),
        patch("app.db.session.get_psycopg_pool") as mock_get_psycopg,
        patch("app.main.AsyncPostgresSaver") as mock_checkpointer,
        patch("app.main.AsyncPostgresStore") as mock_store,
        patch("app.main.build_graph"),
        patch("app.main.QdrantService") as mock_qdrant_cls,
        patch("app.main.ValkeyService") as mock_valkey_cls,
        patch("app.main.S3Service") as mock_s3_cls,
        patch("app.main.redis_from_url") as mock_redis_from_url,
        patch("app.main.FastAPICache"),
        patch("app.main.AsyncOpenAI") as mock_async_openai,
    ):
        mock_get_psycopg.return_value = MagicMock(name="psycopg-pool")
        mock_checkpointer.return_value.setup = AsyncMock()
        mock_store.return_value.setup = AsyncMock()
        mock_qdrant_instance = MagicMock()

        async def _qdrant_close() -> None:
            await _track("qdrant")

        mock_qdrant_instance.ensure_collection = AsyncMock()
        mock_qdrant_instance.close = AsyncMock(side_effect=_qdrant_close)
        mock_qdrant_cls.return_value = mock_qdrant_instance

        mock_valkey_instance = MagicMock()

        async def _valkey_close() -> None:
            await _track("valkey")

        mock_valkey_instance.close = AsyncMock(side_effect=_valkey_close)
        mock_valkey_cls.return_value = mock_valkey_instance

        mock_s3_instance = MagicMock()
        mock_s3_instance.ensure_bucket = AsyncMock()

        async def _s3_close() -> None:
            await _track("s3")

        mock_s3_instance.close = AsyncMock(side_effect=_s3_close)
        mock_s3_cls.return_value = mock_s3_instance

        mock_cache_redis = MagicMock()

        async def _cache_close() -> None:
            await _track("cache_redis")

        mock_cache_redis.aclose = AsyncMock(side_effect=_cache_close)
        mock_redis_from_url.return_value = mock_cache_redis

        mock_openai_instance = MagicMock()

        async def _openai_close() -> None:
            await _track("openai")

        mock_openai_instance.close = AsyncMock(side_effect=_openai_close)
        mock_async_openai.return_value = mock_openai_instance

        from app.main import lifespan  # noqa: PLC0415

        app.router.lifespan_context = lambda _a: lifespan(_a)

        async with _run_lifespan(app):
            pass

    assert call_order == ["openai", "s3", "cache_redis", "valkey", "qdrant"]


async def test_s3_ensure_bucket_failure_propagates(app: FastAPI) -> None:
    """A failed ``ensure_bucket`` must propagate (no silent catch)."""
    with (
        patch("app.main.open_pools", new=AsyncMock()),
        patch("app.main.close_pools", new=AsyncMock()),
        patch("app.db.session.get_psycopg_pool") as mock_get_psycopg,
        patch("app.main.AsyncPostgresSaver") as mock_checkpointer,
        patch("app.main.AsyncPostgresStore") as mock_store,
        patch("app.main.build_graph"),
        patch("app.main.QdrantService") as mock_qdrant_cls,
        patch("app.main.ValkeyService") as mock_valkey_cls,
        patch("app.main.S3Service") as mock_s3_cls,
        patch("app.main.redis_from_url") as mock_redis_from_url,
        patch("app.main.FastAPICache"),
        patch("app.main.AsyncOpenAI") as mock_async_openai,
    ):
        mock_get_psycopg.return_value = MagicMock(name="psycopg-pool")
        mock_checkpointer.return_value.setup = AsyncMock()
        mock_store.return_value.setup = AsyncMock()
        mock_qdrant_instance = MagicMock()
        mock_qdrant_instance.ensure_collection = AsyncMock()
        mock_qdrant_instance.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_qdrant_instance
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.close = AsyncMock()
        mock_valkey_cls.return_value = mock_valkey_instance

        from app.services.errors import BucketNotFoundError  # noqa: PLC0415

        mock_s3_instance = MagicMock()
        mock_s3_instance.ensure_bucket = AsyncMock(
            side_effect=BucketNotFoundError("my-bucket", "missing")
        )
        mock_s3_cls.return_value = mock_s3_instance
        mock_redis_from_url.return_value = MagicMock(aclose=AsyncMock())
        mock_async_openai.return_value = MagicMock(close=AsyncMock())

        from app.main import lifespan  # noqa: PLC0415

        app.router.lifespan_context = lambda _a: lifespan(_a)

        with pytest.raises(BucketNotFoundError):
            async with _run_lifespan(app):
                pass  # pragma: no cover
