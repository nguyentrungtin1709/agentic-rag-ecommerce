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
        Settings(  # type: ignore[call-arg]
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url="postgresql+psycopg://test:test@localhost/test",
            saleor_webhook_secret="test-secret-32-chars-minimum-abc",
        )


# ── Phase 1: new env var defaults ───────────────────────────────────────────


def test_message_summarize_defaults(settings_overrides) -> None:
    from app.config import get_settings

    s = get_settings()
    assert s.message_summarize_threshold == 12
    assert s.message_summarize_count == 8


def test_llm_model_name_defaults(settings_overrides) -> None:
    from app.config import get_settings

    s = get_settings()
    assert s.rerank_model == "gpt-5.4-mini"
    assert s.summarize_model == "gpt-5.4-mini"


def test_qdrant_top_k_defaults(settings_overrides) -> None:
    from app.config import get_settings

    s = get_settings()
    assert s.qdrant_sparse_top_k == 12
    assert s.qdrant_similarity_top_k == 12
    assert s.qdrant_hybrid_top_k == 9
    assert s.qdrant_rerank_top_k == 3


def test_ingestion_defaults() -> None:
    """Test Field defaults by bypassing the .env file entirely."""
    from app.config import Settings

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="sk-test",
        saleor_webhook_secret="test-secret-32-chars-minimum-abc",
    )
    assert s.description_max_chars == 500
    assert s.saleor_storefront_url == ""
