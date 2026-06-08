"""Application configuration loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the agentic-rag-ecommerce service.

    All values are loaded from environment variables or a .env file.
    Field names map 1:1 to environment variable names (case-insensitive).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── PostgreSQL ──────────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="Async PostgreSQL DSN used by asyncpg / psycopg v3.",
    )

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str = Field(default="")
    qdrant_collection_name: str = Field(default="products")

    # ── Valkey / Redis ──────────────────────────────────────────────────────
    # Base URL without DB index; DB index is appended by computed properties.
    valkey_url: str = Field(default="redis://localhost:6379")

    # ── Celery / RabbitMQ ───────────────────────────────────────────────────
    celery_broker_url: str = Field(default="amqp://guest:guest@localhost:5672//")
    celery_result_backend: str = Field(default="redis://localhost:6379/2")

    # ── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str = Field(..., description="OpenAI API key.")

    # ── LLM Model Names ─────────────────────────────────────────────────────
    response_model: str = Field(default="gpt-4o")
    orchestrator_model: str = Field(default="gpt-4o-mini")
    title_model: str = Field(default="gpt-4o-mini")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dims: int = Field(default=1536)

    # ── Tavily ──────────────────────────────────────────────────────────────
    tavily_api_key: str = Field(default="")

    # ── Saleor ──────────────────────────────────────────────────────────────
    saleor_url: str = Field(default="http://localhost:8080")
    saleor_app_token: str = Field(default="")
    saleor_webhook_secret: str = Field(
        ..., description="HMAC-SHA256 webhook secret (min 32 chars)."
    )

    # ── AWS S3 ──────────────────────────────────────────────────────────────
    aws_s3_bucket: str = Field(default="")
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_region: str = Field(default="ap-southeast-1")

    # ── Message Summarization ───────────────────────────────────────────────
    message_summarize_threshold: int = Field(default=12)
    message_summarize_count: int = Field(default=8)

    # ── LLM Model Names (extended) ──────────────────────────────────────────
    rerank_model: str = Field(default="gpt-5.4-mini")
    summarize_model: str = Field(default="gpt-5.4-mini")

    # ── Qdrant Search Top-K ─────────────────────────────────────────────────
    qdrant_sparse_top_k: int = Field(default=12)
    qdrant_similarity_top_k: int = Field(default=12)
    qdrant_hybrid_top_k: int = Field(default=9)
    qdrant_rerank_top_k: int = Field(default=3)

    # ── Ingestion ───────────────────────────────────────────────────────────
    description_max_chars: int = Field(default=500)
    saleor_storefront_url: str = Field(default="")

    # ── Agent Behavior ──────────────────────────────────────────────────────
    max_agent_steps: int = Field(default=10)
    agent_fallback_threshold: int = Field(default=2)
    image_daily_limit: int = Field(default=10)

    # ── Thread Auto-Naming ──────────────────────────────────────────────────
    title_generation_max_attempts: int = Field(default=3)
    title_truncation_length: int = Field(default=50)

    # ── Rate Limiting ───────────────────────────────────────────────────────
    rate_limit_chat: str = Field(default="20/minute")
    rate_limit_thread_create: str = Field(default="10/minute")
    rate_limit_read: str = Field(default="60/minute")
    rate_limit_write: str = Field(default="10/minute")
    rate_limit_reindex: str = Field(default="2/hour")

    # ── Caching ─────────────────────────────────────────────────────────────
    thread_list_cache_ttl: int = Field(default=120)

    # ── Observability ───────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    langsmith_tracing: bool = Field(default=False)
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="agentic-rag-ecommerce")
    langsmith_endpoint: str = Field(default="https://aws.api.smith.langchain.com")

    # ── Computed Properties ─────────────────────────────────────────────────

    @property
    def valkey_rate_limit_url(self) -> str:
        """Valkey DB 0 — rate limiting (slowapi)."""
        return f"{self.valkey_url}/0"

    @property
    def valkey_cache_url(self) -> str:
        """Valkey DB 1 — response cache (fastapi-cache2)."""
        return f"{self.valkey_url}/1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance.

    The instance is constructed once and reused for the lifetime of the
    process.  Tests can override by calling ``get_settings.cache_clear()``
    before monkey-patching environment variables.
    """
    return Settings()  # type: ignore[call-arg]
