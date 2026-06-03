"""Unit tests for app.config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_get_settings_returns_singleton(settings_overrides) -> None:
    from app.config import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_valkey_computed_properties(settings_overrides) -> None:
    from app.config import get_settings

    settings = get_settings()
    assert settings.valkey_rate_limit_url.endswith("/0")
    assert settings.valkey_cache_url.endswith("/1")


def test_settings_missing_required_field() -> None:
    """Construct Settings without .env file and without OPENAI_API_KEY."""
    from app.config import Settings

    with pytest.raises(ValidationError):
        # _env_file=None bypasses the .env file so the missing field is caught.
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://test:test@localhost/test",
            saleor_webhook_secret="test-secret-32-chars-minimum-abc",
        )  # type: ignore[call-arg]
