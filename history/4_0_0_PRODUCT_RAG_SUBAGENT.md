# ProductRAG Subagent — 3-Stage Hybrid Retrieval Pipeline

**Version**: 4.0.0
**Date**: 2026-06-10
**Status**: Implemented

## What

Replace the stub `run_product_rag` wrapper with a production-grade 3-stage
hybrid retrieval subgraph that searches the Qdrant `products` collection
using dense vectors + sparse BM25, then reranks candidates with an LLM,
returning the top-K products as `retrieved_products` in `AgentState`.

**Pipeline:**

```
START -> prepare_query_node -> hybrid_search_node -> llm_postprocess_node -> END
```

- **`prepare_query_node`** — LLM rewrites the latest user query to English
  with category/style/occasion/recipient intent embedded in the text
  (DRAFT 0.6 Option B), and extracts hard `available` + `price_max`
  metadata filters.
- **`hybrid_search_node`** — LlamaIndex + Qdrant with `enable_hybrid=True`
  and `Qdrant/bm25` sparse vectors via fastembed. Returns up to
  `qdrant_hybrid_top_k` (default 9) candidates.
- **`llm_postprocess_node`** — LLM rerank using `RERANK_MODEL`, returns
  up to `qdrant_rerank_top_k` (default 3) product IDs in relevance order,
  mapped back to full `ProductPayload` dicts.

**Wrapper `run_product_rag`** — Translates `AgentState` to
`ProductRAGState`, injects the Qdrant async client into subgraph config
via dual-path, and maps `retrieved_products` back to the parent state.

## Why

The orchestrator's `need_product_search` intent currently routes to a
stub that returns `[]`. Without a real product search path, the agent
cannot fulfil any product-related query. Phase 4 closes that gap and
unblocks the orchestrator's `sufficient` -> `synthesize` flow when
products are needed.

## How

### Files created

- `src/app/agent/subagents/product_rag/state.py` — `ProductRAGState`
  TypedDict (7 fields, `total=False`).
- `src/app/agent/subagents/product_rag/schemas.py` — `PrepareQueryOutput`
  Pydantic model.
- `src/app/agent/subagents/product_rag/nodes.py` — 3 node functions +
  2 module-level helpers.
- `src/app/agent/subagents/product_rag/fault_tolerance.py` —
  `RetryPolicy`, `TimeoutPolicy`, and 3 error handlers.
- `tests/unit/agent/subagents/test_prepare_query.py` — 7 tests.
- `tests/unit/agent/subagents/test_hybrid_search.py` — 7 tests.
- `tests/unit/agent/subagents/test_llm_postprocess.py` — 7 tests.
- `tests/unit/agent/subagents/test_product_rag_wrapper.py` — 7 tests
  (5 + 2 new for Path B fallback).
- `tests/unit/agent/subagents/test_error_handlers.py` — 4 tests.
- `tests/unit/agent/subagents/test_fault_tolerance_config.py` — 3 tests.

### Files modified

- `src/app/services/qdrant_service.py` — Vector names changed from
  `"dense"`/`"sparse"` to `"text-dense"`/`"text-sparse"` (match
  LlamaIndex defaults for `enable_hybrid=True`). `ensure_collection`
  now drops and recreates an existing collection when vector names
  mismatch.
- `src/app/agent/subagents/product_rag/agent.py` — Replaced stub with
  subgraph builder (`_build_product_rag_graph`) and `run_product_rag`
  wrapper using dual-path async-client injection.

### Files deleted

- `src/app/agent/subagents/product_rag/tools.py` — Obsolete ReAct
  scaffolding from an earlier design iteration. Nothing imports it;
  the 3-stage pipeline needs no tool definitions.

## Key Decisions

### 1. Fixed 3-stage pipeline, NOT ReAct

Determinism (exactly 2 LLM calls + 1 search per turn), predictable cost,
and DRAFT 0.6 alignment drive the choice of a `StateGraph` with three
sequential nodes over a ReAct agent loop. ReAct is overkill for a
structured retrieval task with known stages.

### 2. Metadata filters: only `available` and `price_max`

DRAFT 0.6 Option B. Category/collections/style intent goes into the
query text for hybrid (BM25 + dense) search to handle semantically.
Adding category metadata filters would require the LLM to know exact
catalog values (it does not), risking zero-result exact-match failures.
Hard scalar filters (`available`, `price_max`) are reliable and worth
the precision.

### 3. Fault tolerance via LangGraph native mechanisms

Instead of wrapping try/except inside each node, we use:

- `RetryPolicy(max_attempts=3, retry_on=default_retry_on)` on all 3
  nodes (handles `ConnectionError`, `TimeoutError`, httpx 5xx).
- `TimeoutPolicy(run_timeout=60, idle_timeout=30)` on all 3 nodes.
- One `error_handler` per node with a tailored fallback shape
  (`Command` returning partial state + `goto`):
  - `handle_prepare_query_error` -> `Command(update={query: raw_user_msg, filters: None}, goto="hybrid_search")`
  - `handle_hybrid_search_error` -> `Command(update={candidates: []}, goto="llm_postprocess")`
  - `handle_llm_postprocess_error` -> `Command(update={retrieved_products: top_k_by_score}, goto="__end__")`

`set_node_defaults(retry_policy=..., timeout=...)` applies the shared
policies; `add_node(..., error_handler=...)` wires the per-node
fallback. This requires `langgraph>=1.2` (project uses 1.2.2).

Node functions do NOT contain try/except — exceptions bubble to the
LangGraph runtime, which decides to retry or run the handler. The
separation keeps node code pure business logic and makes the fallback
strategy unit-testable in isolation.

### 4. AsyncQdrantClient injection: dual-path (agreed 2026-06-10)

The `run_product_rag` wrapper uses a **dual-path approach**:

- **Path A (primary):** Read `aclient` from
  `config["configurable"]["qdrant_aclient"]`. Uses the shared singleton
  from `QdrantService`.
- **Path B (fallback):** If absent, build a transient
  `AsyncQdrantClient` and close it in a `finally` block.

In Phase 4 only Path B actually executes (the chat endpoint
`api/chat.py` is a 501 stub — no caller injects the shared client
yet). Path A is wired, tested, and waiting for Phase 7.

**Phase 7 production migration (LOCKED):** When the chat endpoint is
implemented in Phase 7, the API handler MUST inject
`request.app.state.qdrant.client` into
`config["configurable"]["qdrant_aclient"]`. This switches to Path A
and eliminates the ~50ms per-call TCP handshake. See
`docs/analysis/05-IMPLEMENTATION-PLAN.md` Phase 7 — "Shared resources injection
into graph config" task #5.

### 5. Vector name migration: drop-and-recreate

LlamaIndex with `enable_hybrid=True` defaults to
`"text-dense"`/`"text-sparse"` for the dense/sparse vector names. The
existing project code used `"dense"`/`"sparse"`. The change is a
breaking schema change at the Qdrant collection level — sparse vectors
cannot be added to a dense-only collection without recreation.

Phase 6 (ingestion) has not run yet, so the collection is empty or
holds only dev test data. `ensure_collection` now:

1. Checks `collection_exists`; if False, creates with the new names.
2. If True, calls `get_collection` to inspect `vectors_config` and
   `sparse_vectors_config`.
3. If `"text-dense"` and `"text-sparse"` are present, returns (no-op).
4. Otherwise logs a WARNING and drops + recreates with the new config.

### 6. Subgraph compiled at module level, `checkpointer=None`

The `_build_product_rag_graph()` function is called once at import
time and cached as `_PRODUCT_RAG_GRAPH`. The subgraph topology is
static; only data is dynamic per call. `checkpointer=None` is correct
because subgraph state (query, filters, candidates, retrieved_products)
is transient — the parent graph's `AsyncPostgresSaver` handles
thread-level state.

## Impact

- **35 new unit tests** (7+7+7+7+4+3) bring the suite from 74 to
  109 passing tests.
- **New coverage** for ProductRAG subgraph (~80-87% per file).
- **Wire-compatible** with parent `AgentState` — no graph.py changes
  needed because `add_node("run_product_rag", run_product_rag)`
  signature stays `(state, config=None)`.
- **No API or ingestion changes** — Phase 4 only reads from Qdrant.
  If the collection is empty, `hybrid_search_node` returns
  `candidates=[]` and the graph routes to `synthesize` with no
  products.
- **Phase 7 dependency** — The chat endpoint MUST inject the shared
  `AsyncQdrantClient` via `config["configurable"]["qdrant_aclient"]` to
  activate Path A. This is documented in
  `docs/analysis/05-IMPLEMENTATION-PLAN.md` Phase 7 task #5 and
  `TEMP.md` Section 10.3.

## Cross-References

- **Detailed implementation plan**: `TEMP.md` (this phase)
- **Architecture source of truth**:
  `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md` Section 2.3
- **Implementation plan (master)**:
  `docs/analysis/05-IMPLEMENTATION-PLAN.md` Phase 4 + Phase 7 task #5
