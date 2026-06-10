"""ProductRAG subgraph state definition.

Defines the per-call state object used by the 3-stage hybrid retrieval
subgraph (prepare_query -> hybrid_search -> llm_postprocess).  Each
field is populated by one node and consumed by the next, so we keep
the state small and transient.

The subgraph state is intentionally separate from the parent
``AgentState`` so the parent graph does not need to know the internal
stages of product retrieval.  The ``run_product_rag`` wrapper performs
the translation between the two.

State layout follows Section 2.3 of the multi-agent architecture
design: the wrapper injects a snapshot of the conversation
(``messages``), the accumulated summary, and the user profile; each
node writes only its own slice of the pipeline fields.
"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import BaseMessage


class ProductRAGState(TypedDict, total=False):
    """Transient state for the ProductRAG subgraph.

    Populated incrementally as the 3 nodes execute:

    - ``messages`` — recent conversation messages snapshot from the
      parent ``AgentState``.  Used by ``prepare_query_node`` to
      resolve conversational references (e.g. "and a matching one")
      when rewriting the search query.
    - ``correlation_id`` — per-request UUID4 forwarded by the
      wrapper.  Used for structured logging inside the subgraph.
    - ``summary`` — accumulated conversation summary produced by
      the ``SummarizeNode``.  Empty string when no summary exists.
      Injected into the prepare-query and rerank ``SystemMessage``
      as persistent context.
    - ``user_profile`` — serialized ``UserProfile`` from the parent
      state.  Optional; ``None`` when not yet loaded.  Injected into
      the prepare-query and rerank ``SystemMessage`` as persistent
      context for personalization and budget hint.
    - ``query`` — English search query produced by
      ``prepare_query_node`` (DRAFT 0.6 Option B — category /
      style / occasion / recipient intent embedded in text).
    - ``filters`` — Qdrant metadata filter dict with optional
      ``available`` (bool) and ``price_max`` (float) keys, or
      ``None`` when no hard filters apply.
    - ``candidates`` — list of product payload dicts returned by
      the hybrid search, capped at ``qdrant_hybrid_top_k``.
    - ``retrieved_products`` — final list of full product payload
      dicts in relevance order.  Written back to the parent
      ``AgentState`` by the wrapper.
    """

    messages: list[BaseMessage]
    correlation_id: str
    summary: str
    user_profile: dict | None
    query: str
    filters: dict | None
    candidates: list[dict]
    retrieved_products: list[dict]
