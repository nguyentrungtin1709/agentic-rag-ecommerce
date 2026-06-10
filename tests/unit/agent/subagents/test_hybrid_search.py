"""Unit tests for app.agent.subagents.product_rag.nodes.hybrid_search_node."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from app.agent.subagents.product_rag.state import ProductRAGState


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> ProductRAGState:
    """Build a minimal ProductRAGState dict for hybrid_search tests."""
    base: dict = {
        "raw_user_message": "I want a t-shirt",
        "query": "minimalist cotton t-shirt",
        "filters": None,
    }
    base.update(kwargs)
    return cast("ProductRAGState", base)


def _make_config(aclient: object | None = None) -> RunnableConfig:
    """Build a RunnableConfig for the hybrid_search_node test."""
    cfg: dict = {"configurable": {}}
    if aclient is not None:
        cfg["configurable"]["qdrant_aclient"] = aclient
    return cast("RunnableConfig", cfg)


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    """Inject minimal env vars and clear settings cache around each test."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    yield
    get_settings.cache_clear()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_hybrid_search_returns_candidate_payloads() -> None:
    """hybrid_search_node must return product payload dicts under 'candidates'."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state()
    aclient = MagicMock()

    fake_node = MagicMock()
    fake_node.node_id = "prod_001"
    fake_node.id_ = "prod_001"
    fake_node.metadata = {
        "product_id": "prod_001",
        "name": "Cotton Tee",
        "category": "t-shirt",
        "price_min": 19.99,
        "price_range": "100k-200k VND",
        "available": True,
    }
    fake_node2 = MagicMock()
    fake_node2.node_id = "prod_002"
    fake_node2.id_ = "prod_002"
    fake_node2.metadata = {
        "product_id": "prod_002",
        "name": "Linen Tee",
        "category": "t-shirt",
        "price_min": 29.99,
        "price_range": "200k-300k VND",
        "available": True,
    }
    fake_result = MagicMock()
    fake_result.nodes = [fake_node, fake_node2]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
        mock_embed_cls.return_value = mock_embed

        result = await hybrid_search_node(state, _make_config(aclient=aclient))

    assert "candidates" in result
    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["product_id"] == "prod_001"
    assert result["candidates"][0]["name"] == "Cotton Tee"
    assert result["candidates"][1]["product_id"] == "prod_002"


async def test_hybrid_search_uses_path_b_when_no_aclient_in_config() -> None:
    """hybrid_search_node must build a transient AsyncQdrantClient when none is injected."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state()
    fake_node = MagicMock()
    fake_node.node_id = "prod_x"
    fake_node.id_ = "prod_x"
    fake_node.metadata = {"product_id": "prod_x", "name": "X"}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.AsyncQdrantClient") as mock_aclient_cls,
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_transient = MagicMock()
        mock_transient.close = AsyncMock()
        mock_aclient_cls.return_value = mock_transient

        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        result = await hybrid_search_node(state, _make_config(aclient=None))

    assert result["candidates"][0]["product_id"] == "prod_x"
    mock_aclient_cls.assert_called_once()
    mock_transient.close.assert_awaited_once()


async def test_hybrid_search_does_not_close_injected_aclient() -> None:
    """hybrid_search_node must NOT call .close() on an injected (Path A) client."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state()
    injected = MagicMock()
    injected.close = AsyncMock()

    fake_node = MagicMock()
    fake_node.node_id = "prod_a"
    fake_node.id_ = "prod_a"
    fake_node.metadata = {"product_id": "prod_a"}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        await hybrid_search_node(state, _make_config(aclient=injected))

    injected.close.assert_not_called()


async def test_hybrid_search_uses_hybrid_mode_with_correct_settings() -> None:
    """hybrid_search_node must build a QdrantVectorStore with enable_hybrid=True."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state()
    aclient = MagicMock()

    fake_node = MagicMock()
    fake_node.node_id = "x"
    fake_node.id_ = "x"
    fake_node.metadata = {}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        await hybrid_search_node(state, _make_config(aclient=aclient))

        call_kwargs = mock_store_cls.call_args.kwargs
        assert call_kwargs["enable_hybrid"] is True
        assert call_kwargs["fastembed_sparse_model"] == "Qdrant/bm25"
        assert call_kwargs["dense_vector_name"] == "text-dense"
        assert call_kwargs["sparse_vector_name"] == "text-sparse"


async def test_hybrid_search_applies_metadata_filters() -> None:
    """hybrid_search_node must translate filters into a Qdrant Filter and pass it to aquery."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state(query="any", filters={"available": True, "price_max": 200.0})
    aclient = MagicMock()

    fake_node = MagicMock()
    fake_node.node_id = "prod_1"
    fake_node.id_ = "prod_1"
    fake_node.metadata = {"product_id": "prod_1"}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        await hybrid_search_node(state, _make_config(aclient=aclient))

        aquery_kwargs = mock_store.aquery.call_args.kwargs
        qdrant_filter = aquery_kwargs["qdrant_filters"]
        assert qdrant_filter is not None
        must_keys = {c.key for c in qdrant_filter.must}
        assert "available" in must_keys
        assert "price_min" in must_keys


async def test_hybrid_search_omits_filter_when_filters_is_none() -> None:
    """hybrid_search_node must pass qdrant_filters=None when state has no filters."""
    from app.agent.subagents.product_rag.nodes import hybrid_search_node

    state = _make_state(query="any", filters=None)
    aclient = MagicMock()

    fake_node = MagicMock()
    fake_node.node_id = "prod_1"
    fake_node.id_ = "prod_1"
    fake_node.metadata = {}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        await hybrid_search_node(state, _make_config(aclient=aclient))

        aquery_kwargs = mock_store.aquery.call_args.kwargs
        assert aquery_kwargs["qdrant_filters"] is None


async def test_hybrid_search_uses_three_top_k_settings() -> None:
    """hybrid_search_node must pass all 3 top_k values from settings to VectorStoreQuery.

    Per DRAFT 0.6 design:
    - sparse_top_k   = qdrant_sparse_top_k   (default 12) -- BM25 candidate count
    - similarity_top_k = qdrant_similarity_top_k (default 12) -- dense candidate count
    - hybrid_top_k   = qdrant_hybrid_top_k   (default 9)  -- post-fusion count
    """
    from app.agent.subagents.product_rag.nodes import hybrid_search_node
    from app.config import get_settings

    state = _make_state(query="any")
    aclient = MagicMock()

    fake_node = MagicMock()
    fake_node.node_id = "x"
    fake_node.id_ = "x"
    fake_node.metadata = {}
    fake_result = MagicMock()
    fake_result.nodes = [fake_node]

    with (
        patch("app.agent.subagents.product_rag.nodes.QdrantVectorStore") as mock_store_cls,
        patch("app.agent.subagents.product_rag.nodes.OpenAIEmbedding") as mock_embed_cls,
    ):
        mock_store = MagicMock()
        mock_store.aquery = AsyncMock(return_value=fake_result)
        mock_store_cls.return_value = mock_store

        mock_embed = MagicMock()
        mock_embed.aget_text_embedding = AsyncMock(return_value=[0.1])
        mock_embed_cls.return_value = mock_embed

        await hybrid_search_node(state, _make_config(aclient=aclient))

        vs_query = mock_store.aquery.call_args[0][0]
        settings = get_settings()
        assert vs_query.sparse_top_k == settings.qdrant_sparse_top_k
        assert vs_query.similarity_top_k == settings.qdrant_similarity_top_k
        assert vs_query.hybrid_top_k == settings.qdrant_hybrid_top_k
        from llama_index.core.vector_stores.types import VectorStoreQueryMode

        assert vs_query.mode == VectorStoreQueryMode.HYBRID
