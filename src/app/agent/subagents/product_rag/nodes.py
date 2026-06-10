"""Node implementations for the ProductRAG 3-stage subgraph.

The subgraph has three sequential nodes:

1. ``prepare_query_node`` -- calls the orchestrator-tier LLM to rewrite
   the latest user message into an optimised English search query,
   embedding category/style/occasion/recipient intent in the text
   (DRAFT 0.6 Option B).  Extracts hard ``available`` and ``price_max``
   metadata filters as scalars.

2. ``hybrid_search_node`` -- runs LlamaIndex + Qdrant with
   ``enable_hybrid=True`` (dense + FastEmbed BM25 sparse).  Reads the
   shared ``AsyncQdrantClient`` from
   ``config["configurable"]["qdrant_aclient"]`` (Path A) or builds a
   transient one (Path B).  Returns the top
   ``qdrant_hybrid_top_k`` candidate payload dicts.

3. ``llm_postprocess_node`` -- calls the rerank-tier LLM with
   structured output (``list[str]`` of product IDs in relevance
   order).  Maps the IDs back to the full candidate payloads and
   caps the result at ``qdrant_rerank_top_k``.

Each node is pure business logic -- exception handling and timeout
retries are delegated to LangGraph via ``set_node_defaults`` and
per-node ``error_handler`` set up in ``agent.py``.

System prompt composition:

Both LLM-calling nodes build their ``SystemMessage`` by composing a
base prompt loaded from ``app/agent/prompts/`` with the persistent
context (conversation summary + user profile) injected by the
wrapper.  The base prompt documents which context slots are filled
in; the helper appends the filled sections below the base.

HumanMessage policy:

- ``prepare_query_node`` -- the ``HumanMessage``(s) are the actual
  conversation messages from ``state["messages"]``.  The LLM needs
  the full recent conversation to resolve anaphoric references
  (e.g. "and a matching one").
- ``llm_postprocess_node`` -- only the rewritten search ``query``
  produced by ``prepare_query_node`` is needed; raw conversation
  history is intentionally not forwarded because the query already
  encodes the resolved intent.

Helper functions:

- ``_build_metadata_filters`` -- translate the small scalar
  ``filters`` dict into a Qdrant ``Filter`` object.
- ``_build_prepare_query_system`` -- compose the prepare-query
  ``SystemMessage`` from base prompt + context injection.
- ``_build_rerank_system`` -- compose the rerank ``SystemMessage``
  from base prompt + context injection.
- ``_format_candidates_for_rerank`` -- render a compact text block
  listing each candidate's product_id, name, category, price_range,
  and availability for the rerank LLM.
"""

from __future__ import annotations

import json
from typing import cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from llama_index.core.vector_stores.types import (
    VectorStoreQuery,
    VectorStoreQueryMode,
)
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Condition, FieldCondition, Filter, MatchValue, Range

from app.agent.prompts import load_prompt
from app.agent.subagents.product_rag.schemas import PrepareQueryOutput
from app.agent.subagents.product_rag.state import ProductRAGState
from app.config import get_settings

logger = structlog.get_logger(__name__)


# ── Module-level helpers ─────────────────────────────────────────────────────


def _build_metadata_filters(filters: dict | None) -> Filter | None:
    """Convert the prepare-query ``filters`` dict into a Qdrant ``Filter``.

    Only two scalar fields are supported per DRAFT 0.6 Option B:

    - ``available`` (``bool``) -- when truthy, restrict to in-stock.
    - ``price_max`` (``float``) -- when set, restrict to products whose
      ``price_min`` is at or below this ceiling.

    Unknown keys are silently dropped (the prepare-query LLM may
    occasionally emit extra fields).  Returns ``None`` when no
    recognised filters are present.

    Args:
        filters: Dict from the prepare-query LLM, may be ``None``.

    Returns:
        Qdrant ``Filter`` with one ``FieldCondition`` per recognised
        key, or ``None`` when the dict is empty / has no recognised
        keys.
    """
    if not filters:
        return None

    must: list[Condition] = []
    if isinstance(filters.get("available"), bool) and filters["available"]:
        must.append(FieldCondition(key="available", match=MatchValue(value=True)))
    price_max = filters.get("price_max")
    if isinstance(price_max, (int, float)) and price_max > 0:
        must.append(FieldCondition(key="price_min", range=Range(lte=float(price_max))))

    if not must:
        return None
    return Filter(must=must)


def _build_prepare_query_system(
    summary: str,
    user_profile: dict | None,
) -> str:
    """Compose the prepare-query ``SystemMessage`` from base + context.

    The base prompt is loaded from ``prepare_query_system.md`` and
    documents the context slots.  The helper appends filled sections
    (conversation summary, user profile) when the corresponding
    context is non-empty so the LLM sees a complete instruction.

    Args:
        summary: Accumulated conversation summary from
            ``state["summary"]``; empty string when none.
        user_profile: Serialized ``UserProfile`` dict from
            ``state["user_profile"]``; ``None`` when not yet loaded.

    Returns:
        Full ``SystemMessage`` content with base prompt and any
        non-empty context sections.
    """
    base = load_prompt("prepare_query_system")
    parts: list[str] = [base]
    if summary:
        parts.append(f"\n## Conversation Summary\n{summary}")
    if user_profile:
        parts.append(f"\n## User Profile\n{json.dumps(user_profile, ensure_ascii=False, indent=2)}")
    return "\n".join(parts)


def _build_rerank_system(
    summary: str,
    user_profile: dict | None,
) -> str:
    """Compose the rerank ``SystemMessage`` from base + context.

    Mirrors ``_build_prepare_query_system`` for the rerank prompt:
    base prompt + conversation summary + user profile appended as
    explicit sections when non-empty.

    Args:
        summary: Accumulated conversation summary; empty string when
            none.
        user_profile: Serialized ``UserProfile`` dict; ``None`` when
            not yet loaded.

    Returns:
        Full ``SystemMessage`` content with base prompt and any
        non-empty context sections.
    """
    base = load_prompt("rerank_system")
    parts: list[str] = [base]
    if summary:
        parts.append(f"\n## Conversation Summary\n{summary}")
    if user_profile:
        parts.append(f"\n## User Profile\n{json.dumps(user_profile, ensure_ascii=False, indent=2)}")
    return "\n".join(parts)


def _format_candidates_for_rerank(candidates: list[dict]) -> str:
    """Render candidates as a compact text block for the rerank LLM.

    The rerank prompt needs each candidate's distinguishing fields
    visible without overwhelming the context window.  The format is::

        ID: <id>
        Name: <name>
        Category: <category>
        Price: <price_range>
        Available: <bool>

        ID: <id>
        ...

    Args:
        candidates: Product payload dicts from the hybrid search.

    Returns:
        Multi-line text block with one stanza per candidate.
    """
    lines: list[str] = []
    for p in candidates:
        product_id = p.get("product_id", "?")
        name = p.get("name", "?")
        category = p.get("category", "?")
        price_range = p.get("price_range", "?")
        available = p.get("available", True)
        lines.append(
            f"ID: {product_id}\n"
            f"Name: {name}\n"
            f"Category: {category}\n"
            f"Price: {price_range}\n"
            f"Available: {bool(available)}"
        )
    return "\n\n".join(lines)


# ── Stage 1 -- prepare query ────────────────────────────────────────────────


async def prepare_query_node(state: ProductRAGState) -> dict:
    """Rewrite the conversation into an English search query + filters.

    Builds a ``SystemMessage`` from the base prompt + injected
    context (summary, user profile) via
    ``_build_prepare_query_system``, then unpacks
    ``state["messages"]`` as the ``HumanMessage``(s) of the LLM
    call.  The full recent conversation is forwarded so the LLM can
    resolve anaphoric references when producing the rewritten
    query.

    Calls the orchestrator-tier LLM
    (``settings.orchestrator_model``) with structured output
    (``PrepareQueryOutput``) returning:

    - ``query`` -- concise English query with category / style /
      occasion intent embedded.
    - ``available`` -- ``bool | None`` filter.
    - ``price_max`` -- ``float | None`` filter.

    Args:
        state: Current ``ProductRAGState``.  Reads ``messages``,
            ``summary``, ``user_profile``; reads ``correlation_id``
            for tracing.

    Returns:
        Partial state update ``{"query": ..., "filters": ...}``.
    """
    settings = get_settings()
    messages = state.get("messages", [])
    summary = state.get("summary", "")
    user_profile = state.get("user_profile")

    llm = ChatOpenAI(model=settings.orchestrator_model).with_structured_output(PrepareQueryOutput)
    parsed = cast(
        PrepareQueryOutput,
        await llm.ainvoke(
            [
                SystemMessage(
                    content=_build_prepare_query_system(summary, user_profile),
                ),
                *messages,
            ],
            config={"metadata": {"correlation_id": state.get("correlation_id", "")}},
        ),
    )

    filters: dict | None = None
    if parsed.available is not None or parsed.price_max is not None:
        filters = {
            "available": parsed.available,
            "price_max": parsed.price_max,
        }

    logger.info(
        "product_rag_prepare_query_done",
        query_len=len(parsed.query),
        has_availability_filter=parsed.available is not None,
        has_price_max_filter=parsed.price_max is not None,
    )

    return {"query": parsed.query, "filters": filters}


# ── Stage 2 -- hybrid search ─────────────────────────────────────────────────


async def hybrid_search_node(
    state: ProductRAGState,
    config: RunnableConfig,
) -> dict:
    """Run hybrid (dense + BM25) search against the Qdrant products collection.

    Reads the shared ``AsyncQdrantClient`` from
    ``config["configurable"]["qdrant_aclient"]`` (Path A -- production,
    set by the API handler in Phase 7).  When the key is absent (Phase
    4 only -- no API caller yet), builds a transient client and closes
    it in a ``finally`` block (Path B).

    Embeds the query with ``EMBEDDING_MODEL`` (OpenAI), runs
    ``VectorStoreQueryMode.HYBRID`` against the products collection,
    and returns up to ``qdrant_hybrid_top_k`` candidate payload dicts.

    Args:
        state: Current ``ProductRAGState``.  Reads ``query`` and
            ``filters`` written by ``prepare_query_node``.
        config: LangGraph runtime config containing
            ``configurable.qdrant_aclient`` (Path A).

    Returns:
        Partial state update ``{"candidates": [...]}`` capped at
        ``qdrant_hybrid_top_k`` (the post-fusion count).
    """
    settings = get_settings()
    query = state.get("query", "")
    filters = state.get("filters")

    aclient: AsyncQdrantClient | None = config.get("configurable", {}).get("qdrant_aclient")
    owns_client = aclient is None
    if owns_client:
        aclient = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )

    try:
        vector_store = QdrantVectorStore(
            collection_name=settings.qdrant_collection_name,
            aclient=aclient,
            enable_hybrid=True,
            fastembed_sparse_model="Qdrant/bm25",
            dense_vector_name="text-dense",
            sparse_vector_name="text-sparse",
        )

        embed_model = OpenAIEmbedding(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
        query_embedding = await embed_model.aget_text_embedding(query)

        qdrant_filter = _build_metadata_filters(filters)

        vs_query = VectorStoreQuery(
            query_str=query,
            query_embedding=query_embedding,
            mode=VectorStoreQueryMode.HYBRID,
            sparse_top_k=settings.qdrant_sparse_top_k,
            similarity_top_k=settings.qdrant_similarity_top_k,
            hybrid_top_k=settings.qdrant_hybrid_top_k,
        )

        result = await vector_store.aquery(vs_query, qdrant_filters=qdrant_filter)

        candidates: list[dict] = []
        for node in result.nodes or []:
            payload = dict(node.metadata or {})
            payload.setdefault("product_id", node.node_id or node.id_)
            candidates.append(payload)

        logger.info(
            "product_rag_hybrid_search_done",
            query_len=len(query),
            candidate_count=len(candidates),
            sparse_top_k=settings.qdrant_sparse_top_k,
            similarity_top_k=settings.qdrant_similarity_top_k,
            hybrid_top_k=settings.qdrant_hybrid_top_k,
        )

        return {"candidates": candidates}
    finally:
        if owns_client and aclient is not None:
            await aclient.close()


# ── Stage 3 -- LLM rerank ────────────────────────────────────────────────────


async def llm_postprocess_node(state: ProductRAGState) -> dict:
    """Rerank candidates with the LLM and return the top-K product payloads.

    Builds a ``SystemMessage`` from the rerank base prompt + injected
    context (summary, user profile) via ``_build_rerank_system``.  The
    ``HumanMessage`` carries only the rewritten search ``query`` plus
    the formatted candidate list -- raw conversation history is
    intentionally not forwarded because the query already encodes
    the resolved intent.

    The LLM (``settings.rerank_model``) is called with structured
    output (``list[str]`` of product IDs in relevance order).  The
    returned IDs are mapped back to the corresponding candidate
    payload dicts and capped at ``qdrant_rerank_top_k``.

    Args:
        state: Current ``ProductRAGState``.  Reads ``query``,
            ``candidates``, ``summary``, ``user_profile``,
            ``correlation_id``.

    Returns:
        Partial state update ``{"retrieved_products": [...]}`` with
        at most ``qdrant_rerank_top_k`` payload dicts in relevance
        order.
    """
    settings = get_settings()
    query = state.get("query", "")
    candidates = state.get("candidates", [])
    summary = state.get("summary", "")
    user_profile = state.get("user_profile")
    top_k = settings.qdrant_rerank_top_k

    if not candidates:
        logger.info("product_rag_rerank_skipped_empty_candidates")
        return {"retrieved_products": []}

    by_id: dict[str, dict] = {}
    for p in candidates:
        pid = p.get("product_id", "")
        if pid:
            by_id[pid] = p

    candidates_text = _format_candidates_for_rerank(candidates)

    llm = ChatOpenAI(model=settings.rerank_model).with_structured_output(list[str])
    reranked = cast(
        list[str],
        await llm.ainvoke(
            [
                SystemMessage(content=_build_rerank_system(summary, user_profile)),
                HumanMessage(
                    content=(
                        f"## Rewritten search query\n{query}\n\n"
                        f"## Candidate products ({len(candidates)})\n"
                        f"{candidates_text}\n\n"
                        f"Return the top {top_k} product IDs in relevance order."
                    )
                ),
            ],
            config={"metadata": {"correlation_id": state.get("correlation_id", "")}},
        ),
    )

    retrieved: list[dict] = []
    for pid in reranked:
        if pid in by_id and len(retrieved) < top_k:
            retrieved.append(by_id[pid])
    if len(retrieved) < top_k:
        for p in candidates:
            pid = p.get("product_id", "")
            if pid and pid not in {r.get("product_id") for r in retrieved}:
                retrieved.append(p)
                if len(retrieved) >= top_k:
                    break

    logger.info(
        "product_rag_rerank_done",
        candidate_count=len(candidates),
        rerank_returned=len(reranked),
        retrieved_count=len(retrieved),
        top_k=top_k,
    )

    return {"retrieved_products": retrieved}
