"""Unit tests for the ProductRAG subgraph fault-tolerance policy configuration."""

# pyright: reportPrivateImportUsage=false

from __future__ import annotations

import pytest
from langgraph.types import (  # type: ignore[attr-defined]
    RetryPolicy,
    TimeoutPolicy,
    default_retry_on,
)

from app.agent.subagents.product_rag.fault_tolerance import (
    _PRODUCT_RAG_RETRY_POLICY,
    _PRODUCT_RAG_TIMEOUT_POLICY,
)


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


def test_retry_policy_uses_default_retry_on_with_three_attempts() -> None:
    """_PRODUCT_RAG_RETRY_POLICY must retry up to 3 times using the default condition."""
    assert isinstance(_PRODUCT_RAG_RETRY_POLICY, RetryPolicy)
    assert _PRODUCT_RAG_RETRY_POLICY.max_attempts == 3
    assert _PRODUCT_RAG_RETRY_POLICY.retry_on is default_retry_on


def test_timeout_policy_runs_for_60_seconds_with_30s_idle() -> None:
    """_PRODUCT_RAG_TIMEOUT_POLICY must enforce a 60s run timeout and 30s idle timeout."""
    assert isinstance(_PRODUCT_RAG_TIMEOUT_POLICY, TimeoutPolicy)
    # TimeoutPolicy normalises datetimes; assert the integer value.
    assert _PRODUCT_RAG_TIMEOUT_POLICY.run_timeout == 60
    assert _PRODUCT_RAG_TIMEOUT_POLICY.idle_timeout == 30


def test_policies_apply_to_all_three_nodes_in_compiled_graph() -> None:
    """The compiled ProductRAG subgraph must have 3 user nodes plus 3 error-handler nodes."""
    from app.agent.subagents.product_rag.agent import _PRODUCT_RAG_GRAPH

    node_names = set(_PRODUCT_RAG_GRAPH.get_graph().nodes.keys())

    assert "prepare_query_node" in node_names
    assert "hybrid_search_node" in node_names
    assert "llm_postprocess_node" in node_names

    # LangGraph materialises each error_handler as a separate node.
    error_handlers = {n for n in node_names if n.startswith("__error_handler__")}
    assert len(error_handlers) == 3
