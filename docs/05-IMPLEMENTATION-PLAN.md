# Implementation Plan — AI POD Stylist

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 1.0
- **Date**: 2026-06-07
- **Status**: Active

> This document is the authoritative implementation guide for Phase 5 and beyond.
> It is based on `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md` (DRAFT 0.6)
> as the primary source of truth. Where older documents conflict with DRAFT 0.6,
> DRAFT 0.6 takes precedence.

---

## Current State

All nodes in `src/app/agent/` are stubs returning empty dicts or raising `NotImplementedError`.
The graph topology in `graph.py` is a simplified sequential chain, not the final conditional
routing topology. Config and state are partially complete — 10 new env vars and 3 new AgentState
fields are missing.

### Files requiring real implementation

| File | Current Status |
|---|---|
| `src/app/config.py` | Missing 10 env vars |
| `src/app/agent/state.py` | Missing `summary`, `generate_image`, `first_user_message` |
| `src/app/agent/graph.py` | Simplified stub chain; needs full conditional topology |
| `src/app/agent/nodes/orchestrate.py` | Stub |
| `src/app/agent/nodes/profiler.py` | Stub (reads profile, skips LLM merge) |
| `src/app/agent/nodes/synthesize.py` | Stub |
| `src/app/agent/nodes/generate_image.py` | Stub |
| `src/app/agent/nodes/generate_title.py` | Stub |
| `src/app/agent/subagents/product_rag/agent.py` | Stub |
| `src/app/agent/subagents/trend_scout/agent.py` | Stub |
| `src/app/rag/indexer.py` | Stub |
| `src/app/rag/retriever.py` | Stub |
| `src/app/api/chat.py` | Returns 501 |

---

## Phases Overview

| Phase | Name | Scope |
|---|---|---|
| 1 | Foundation Fixes | Config, AgentState, graph topology, prompt scaffolding |
| 2 | Profile + Memory Management | ProfilerNode, SummarizeNode |
| 3 | Orchestration | OrchestratorNode, conditional routing |
| 4 | Product RAG | ProductRAGAgent subgraph + ProductRetriever |
| 5 | Trend Scout | TrendScoutNode create_agent + tools |
| 6 | RAG Ingestion | ProductIndexer, Celery tasks, Qdrant collection setup |
| 7 | Response + SSE | ResponseGeneratorNode, TitleGenerationNode, SSE streaming |
| 8 | Image Generation | ImageGenerationNode, DALL-E, S3, Valkey quota |

Each phase must be completed and all tests passing before proceeding to the next.

---

## Phase 1 — Foundation Fixes

### Objective

Bring `config.py`, `state.py`, `graph.py`, and the prompts directory to a consistent,
complete baseline that all downstream phases can depend on. No LLM calls are made in this phase.

### Tasks

#### 1.1 Add missing env vars to `src/app/config.py`

Add the 10 new fields to `Settings` (from Section 6 of DRAFT 0.6):

```python
# src/app/config.py — new fields to add inside Settings class

# -- Message summarization --
message_summarize_threshold: int = Field(default=12)
message_summarize_count: int = Field(default=8)

# -- LLM model names --
rerank_model: str = Field(default="gpt-4o-mini")
summarize_model: str = Field(default="gpt-4o-mini")

# -- Qdrant search top-k --
qdrant_sparse_top_k: int = Field(default=12)
qdrant_similarity_top_k: int = Field(default=12)
qdrant_hybrid_top_k: int = Field(default=9)
qdrant_rerank_top_k: int = Field(default=3)

# -- Ingestion --
description_max_chars: int = Field(default=500)
saleor_storefront_url: str = Field(default="")
```

#### 1.2 Add missing fields to `src/app/agent/state.py`

Add three fields that are referenced by multiple nodes but absent from the current definition:

```python
# Fields to add to AgentState

summary: str               # default "", overwritten by SummarizeNode (Section 1.2 of DRAFT 0.6)
generate_image: bool       # from HTTP request body (FR-047)
first_user_message: str | None  # first HumanMessage.content; used by TitleGenerationNode (FR-022)
```

#### 1.3 Rebuild graph topology in `src/app/agent/graph.py`

Replace the stub sequential chain with the full conditional topology per
`docs/diagrams/05-agent-workflow.mermaid` and Section 1.3 of DRAFT 0.6:

```
START --> profiler  (main pipeline)
START --> title_gen (parallel branch — only fires when title_generated=False)
title_gen --> END

profiler --> summarize
summarize --> orchestrate
orchestrate --[conditional edge]--> {
    need_product_search → run_product_rag
    need_trend_info     → run_trend_scout
    sufficient
    clarification_needed
    out_of_scope
    fallback            → synthesize
}
run_product_rag --> orchestrate   (loop back)
run_trend_scout --> orchestrate   (loop back)
synthesize --> image_generation   (parallel branch)
synthesize --> END
image_generation --> END
```

The `route_orchestrate` routing function must read `state["intent"]` and return the next
node key. The `SummarizeNode` placeholder node must be added and wired.

#### 1.4 Create prompt template directory structure

All LLM prompt templates must be externalized as `.md` files (NFR-025). Create the
following structure under `src/app/agent/prompts/`:

```
src/app/agent/prompts/
    orchestrator_system.md    # OrchestratorNode system prompt
    profiler_system.md        # ProfilerNode system prompt
    summarize_system.md       # SummarizeNode summarization prompt
    prepare_query_system.md   # ProductRAG prepare_query_node system prompt
    rerank_system.md          # ProductRAG llm_postprocess_node system prompt
    title_system.md           # TitleGenerationNode prompt
    synthesize_system.md      # ResponseGeneratorNode system prompt
    trend_scout_system.md     # TrendScoutNode dynamic SystemMessage template
```

Create a `load_prompt(name: str) -> str` utility in
`src/app/agent/prompts/__init__.py` that reads the file at module path:

```python
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

def load_prompt(name: str) -> str:
    """Load a prompt template by filename (without .md extension)."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
```

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/test_config.py` | All 10 new fields have correct defaults; `Settings` loads without error with required vars set |
| `tests/unit/agent/test_state.py` | `AgentState` has all expected fields; `summary` defaults to `""`; `generate_image` and `first_user_message` are present |
| `tests/unit/agent/test_graph.py` | Graph compiles without error; node list matches expected set; `route_orchestrate` returns correct node key for each intent value including unknown intent |
| `tests/unit/agent/test_prompts.py` | `load_prompt` returns non-empty string for each template name; raises `FileNotFoundError` for unknown name |

---

## Phase 2 — Profile + Memory Management

### Objective

Implement `ProfilerNode` and `SummarizeNode` with real LLM calls. These two nodes run on
every turn and are the foundation of personalization and context management.

### ProfilerNode Implementation

**File**: `src/app/agent/nodes/profiler.py`

**How to implement**:

1. Define `UserProfile` Pydantic model in `src/app/models/profile.py` if not already present:
   ```python
   class UserProfile(BaseModel):
       age_group: str | None = None
       style_preferences: list[str] = []
       product_interests: list[str] = []
       occasion_context: str | None = None
       recipient_context: str | None = None
       budget_range: str | None = None
   ```

2. Load existing profile from `AsyncPostgresStore` under namespace `("profiles", user_id)` key
   `"profile"`. Default to empty `UserProfile()` if not found.

3. Extract the latest `HumanMessage` content from `state["messages"][-1]` (last message).
   If no `HumanMessage` exists, return the existing profile unchanged.

4. Call `SUMMARIZE_MODEL` (not `ORCHESTRATOR_MODEL`) with structured output
   (`response_format=UserProfile`) using only:
   - `current_profile_json`: `current_profile.model_dump_json()`
   - `latest_user_message`: message text

   Prompt template: `profiler_system.md`

5. Write merged profile back to store with `await store.aput(namespace, "profile", merged.model_dump())`.

6. Return `{"user_profile": merged.model_dump()}`.

**LLM call pattern** (use `langchain_openai.ChatOpenAI` with `.with_structured_output(UserProfile)`):
```python
llm = ChatOpenAI(model=settings.summarize_model).with_structured_output(UserProfile)
merged: UserProfile = await llm.ainvoke([SystemMessage(...), HumanMessage(...)])
```

**Key constraint** (FR-028): The LLM receives ONLY `current_profile_json` and
`latest_user_message`. Never pass the full conversation history to ProfilerNode.

### SummarizeNode Implementation

**File**: `src/app/agent/nodes/summarize.py` (new file)

**How to implement**:

1. Check `len(state["messages"]) < settings.message_summarize_threshold`. If true, return `{}`.

2. Take the oldest `settings.message_summarize_count` messages from `state["messages"]`.

3. Build summarization prompt:
   - If `state.get("summary")` is non-empty:
     `f"Existing summary:\n{summary}\n\nIncorporate the new messages below and update the summary:"`
   - Else: `"Summarize the following conversation:"`

4. Call `SUMMARIZE_MODEL` with `messages_to_summarize + [HumanMessage(content=prompt)]`.

5. Build delete ops: `[RemoveMessage(id=m.id) for m in messages_to_summarize]`.

6. Return `{"summary": response.content, "messages": delete_ops}`.

**Note**: `SummarizeNode` must also be added to `graph.py` node list and wired
`profiler → summarize → orchestrate`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_profiler.py` | Profile created when none exists; existing profile is merged with new message; LLM called with only 2 fields (assert call args); profile written back to store; returns `user_profile` in state update |
| `tests/unit/agent/nodes/test_summarize.py` | Returns `{}` when message count < threshold; triggers summarization when >= threshold; uses extend prompt when summary already exists; produces correct RemoveMessage list; returns new summary string |

**Mocking pattern**: Use `unittest.mock.AsyncMock` for LLM calls and store operations.
Do not call real OpenAI APIs in unit tests.

---

## Phase 3 — Orchestration

### Objective

Implement `OrchestratorNode` with real LLM tool binding and `route_orchestrate` conditional
edge function. After this phase, the graph can route turns to the correct branch.

### OrchestratorNode Implementation

**File**: `src/app/agent/nodes/orchestrate.py`

**How to implement**:

1. Define the `update_intent` tool as a `@tool`-decorated function:
   ```python
   from langchain_core.tools import tool

   IntentType = Literal[
       "need_product_search",
       "need_trend_info",
       "sufficient",
       "clarification_needed",
       "out_of_scope",
       "fallback",
   ]

   @tool
   def update_intent(intent: IntentType) -> str:
       """Update the conversation routing intent."""
       return intent
   ```

2. Check `config["remaining_steps"]` FIRST:
   ```python
   remaining = config.get("remaining_steps", settings.max_agent_steps)
   if remaining <= settings.agent_fallback_threshold:
       return {"intent": "fallback"}
   ```

3. Build the LLM with tool bound:
   ```python
   llm = ChatOpenAI(model=settings.orchestrator_model).bind_tools([update_intent])
   ```

4. Build messages for the LLM:
   - System message from `orchestrator_system.md` (includes product-first priority rule)
   - Current `state["messages"]` (conversation context)
   - If `state.get("retrieved_products")`: append a note that product search already ran
   - If `state.get("trend_summary")`: append a note that trend search already ran

5. Invoke LLM, extract tool call result from the response to get `intent` string.

6. Return `{"intent": intent}`.

**Product-first rule** (must be in `orchestrator_system.md`):
> When both product search AND trend information are needed, dispatch `need_product_search`
> first. Only after `retrieved_products` is populated dispatch `need_trend_info`.

### `route_orchestrate` Function

**File**: `src/app/agent/graph.py`

```python
def route_orchestrate(state: AgentState) -> str:
    intent = state.get("intent")
    if intent == "need_product_search":
        return "run_product_rag"
    if intent == "need_trend_info":
        return "run_trend_scout"
    # sufficient, clarification_needed, out_of_scope, fallback, None
    return "synthesize"
```

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_orchestrate.py` | Forces `fallback` when `remaining_steps <= threshold`; routes to `run_product_rag` for product queries; routes to `run_trend_scout` for trend queries; routes to `synthesize` for `sufficient`/`out_of_scope`/`fallback`; `update_intent` tool is called with correct intent value |
| `tests/unit/agent/test_graph.py` (extend) | `route_orchestrate` maps every intent value to the expected node; unknown intent maps to `synthesize` |

---

## Phase 4 — Product RAG

### Objective

Implement the full `ProductRAGAgent` subgraph with three sequential pipeline stages:
query preparation, hybrid search, and LLM reranking.

### State and Models

Create `src/app/agent/subagents/product_rag/state.py`:

```python
from typing import TypedDict
from langchain_core.messages import BaseMessage

class ProductRAGState(TypedDict):
    # Context injected by wrapper (from parent AgentState)
    messages: list[BaseMessage]
    summary: str
    user_profile: dict
    # Pipeline fields
    query: str
    filters: dict | None
    candidates: list[dict]
    retrieved_products: list[dict]
```

Create `src/app/agent/subagents/product_rag/schemas.py`:

```python
from pydantic import BaseModel

class PrepareQueryOutput(BaseModel):
    query: str              # English search query with category/style intent embedded
    available: bool | None  # None means "do not filter"
    price_max: float | None # None means "do not filter"
```

### Stage 1 — `prepare_query_node`

**File**: `src/app/agent/subagents/product_rag/nodes.py`

**How to implement**:

1. Build context from `ProductRAGState`:
   - Recent messages: `state["messages"][-4:]` (last 2 turns)
   - Summary: `state.get("summary", "")`
   - User profile: `state.get("user_profile", {})`

2. Call `ORCHESTRATOR_MODEL` with structured output (`response_format=PrepareQueryOutput`).
   Prompt template: `prepare_query_system.md`

3. Build `filters: dict | None`:
   ```python
   filter_items = {}
   if output.available is not None:
       filter_items["available"] = output.available
   if output.price_max is not None:
       filter_items["price_max"] = output.price_max
   filters = filter_items or None
   ```

4. Return `{"query": output.query, "filters": filters}`.

**Key constraint** (DRAFT 0.6 Option B): Only `available` and `price_max` are extracted as
metadata filters. Category/collections/style intent must be embedded into the `query` text.

### Stage 2 — `hybrid_search_node`

**File**: `src/app/agent/subagents/product_rag/nodes.py`

**How to implement**:

1. Build `MetadataFilters` from `state["filters"]` (if not None):
   ```python
   from llama_index.core.vector_stores import MetadataFilter, MetadataFilters, FilterOperator, FilterCondition

   filter_list = []
   if "available" in filters:
       filter_list.append(MetadataFilter(key="available", operator=FilterOperator.EQ, value=filters["available"]))
   if "price_max" in filters:
       filter_list.append(MetadataFilter(key="price_max", operator=FilterOperator.LTE, value=filters["price_max"]))
   metadata_filters = MetadataFilters(filters=filter_list, condition=FilterCondition.AND) if filter_list else None
   ```

2. Create `QdrantVectorStore`:
   ```python
   from llama_index.vector_stores.qdrant import QdrantVectorStore
   from qdrant_client import AsyncQdrantClient

   client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
   vector_store = QdrantVectorStore(
       client=client,
       collection_name=settings.qdrant_collection_name,
       enable_hybrid=True,
       fastembed_sparse_model="Qdrant/bm25",
   )
   ```

3. Create `VectorStoreIndex` and query engine:
   ```python
   from llama_index.core import VectorStoreIndex, Settings as LISettings
   from llama_index.embeddings.openai import OpenAIEmbedding

   LISettings.embed_model = OpenAIEmbedding(model=settings.embedding_model)
   index = VectorStoreIndex.from_vector_store(vector_store)
   retriever = index.as_retriever(
       vector_store_query_mode="hybrid",
       sparse_top_k=settings.qdrant_sparse_top_k,
       similarity_top_k=settings.qdrant_similarity_top_k,
       hybrid_top_k=settings.qdrant_hybrid_top_k,
       filters=metadata_filters,
   )
   ```

4. Retrieve: `nodes = await retriever.aretrieve(state["query"])`.

5. Convert each `NodeWithScore` to dict preserving all metadata fields.

6. Return `{"candidates": [node_to_dict(n) for n in nodes]}`.

### Stage 3 — `llm_postprocess_node`

**File**: `src/app/agent/subagents/product_rag/nodes.py`

**How to implement**:

1. Build a prompt listing all candidates with their metadata (name, category, price_range,
   description excerpt). Prompt template: `rerank_system.md`.

2. Call `RERANK_MODEL` with structured output — a `list[str]` of selected `product_id` values,
   ordered by relevance (most relevant first). Length: up to `settings.qdrant_rerank_top_k`.

3. Map each selected `product_id` back to the full candidate dict.

4. Return `{"retrieved_products": selected_candidates}`.

### Wrapper `run_product_rag`

**File**: `src/app/agent/subagents/product_rag/agent.py`

**How to implement**:

1. Build `ProductRAGState` input from parent `AgentState`:
   ```python
   subgraph_input: ProductRAGState = {
       "messages": list(state["messages"]),
       "summary": state.get("summary", ""),
       "user_profile": state.get("user_profile") or {},
       "query": "",
       "filters": None,
       "candidates": [],
       "retrieved_products": [],
   }
   ```

2. Compile the `ProductRAGAgent` subgraph with `checkpointer=None`.

3. `result = await product_rag_graph.ainvoke(subgraph_input, config)`.

4. Return `{"retrieved_products": result["retrieved_products"]}`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/subagents/test_prepare_query.py` | Returns English query; embeds category intent in query text (not in filters); extracts `price_max` filter when budget mentioned; extracts `available=True` filter; returns `filters=None` when no budget/availability signal |
| `tests/unit/agent/subagents/test_hybrid_search.py` | Builds correct `MetadataFilters` for available+price_max; passes `None` filters when no filters; retrieves correct number of candidates (mock Qdrant) |
| `tests/unit/agent/subagents/test_llm_postprocess.py` | Selects top-K products by product_id; preserves full ProductPayload metadata; handles case where LLM returns fewer IDs than requested |
| `tests/unit/agent/subagents/test_product_rag_wrapper.py` | Injects correct context fields into subgraph state; maps `retrieved_products` back to parent state; returns empty list when subgraph returns empty |

---

## Phase 5 — Trend Scout

### Objective

Implement `TrendScoutNode` as a LangChain `create_agent` with ReAct tool-use loop, wrapped
by `run_trend_scout` in the parent graph.

### State and Models

Create `src/app/agent/subagents/trend_scout/state.py`:

```python
from langchain.agents import AgentState

class TrendScoutState(AgentState):
    # messages: list[BaseMessage] — inherited; messages[0] = dynamic SystemMessage
    generate_image: bool
```

Create `src/app/agent/subagents/trend_scout/schemas.py`:

```python
from pydantic import BaseModel

class TrendScoutOutput(BaseModel):
    trend_summary: str        # 2-3 sentence trend report
    image_prompt: str | None  # 1 DALL-E prompt; None if not applicable
```

### Tools

**File**: `src/app/agent/subagents/trend_scout/tools.py`

```python
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool

tavily_search = TavilySearchResults(max_results=5)

@tool
def duckduckgo_search(query: str) -> str:
    """Search the web for trend information using DuckDuckGo."""
    return DuckDuckGoSearchRun().run(query)
```

### `_build_trend_scout_system` Helper

**File**: `src/app/agent/subagents/trend_scout/agent.py`

Builds the dynamic system message injecting parent context (per Section 1.1 of DRAFT 0.6):
```python
def _build_trend_scout_system(
    summary: str,
    user_profile: dict,
    retrieved_products: list[dict],
    generate_image: bool,
) -> str:
    parts = [load_prompt("trend_scout_system")]  # base instructions
    if summary:
        parts.append(f"## Conversation history summary\n{summary}")
    if user_profile:
        parts.append(f"## User preferences\n{json.dumps(user_profile, ensure_ascii=False)}")
    if retrieved_products:
        names = [p.get("name", p.get("product_id", "?")) for p in retrieved_products]
        parts.append(f"## Products already recommended\n{', '.join(names)}")
    if generate_image:
        parts.append("## Output note\nInclude exactly one text-to-image prompt.")
    return "\n\n".join(parts)
```

### Agent and Wrapper

**File**: `src/app/agent/subagents/trend_scout/agent.py`

1. Create agent:
   ```python
   from langchain.agents import create_react_agent

   trend_scout_agent = create_react_agent(
       model=ChatOpenAI(model=settings.orchestrator_model),
       tools=[tavily_search, duckduckgo_search],
       state_schema=TrendScoutState,
   )
   ```

2. Wrapper `run_trend_scout`:
   ```python
   async def run_trend_scout(state: AgentState, config: RunnableConfig) -> dict:
       system_content = _build_trend_scout_system(...)
       subgraph_input = {
           "messages": [SystemMessage(content=system_content)] + list(state["messages"][-4:]),
           "generate_image": state.get("generate_image", False),
       }
       result = await trend_scout_agent.ainvoke(subgraph_input, config)
       output = extract_structured_output(result, TrendScoutOutput)
       return {
           "trend_summary": output.trend_summary,
           "image_prompt": output.image_prompt,
       }
   ```

3. Fallback: If `TavilyToolException` is raised and DuckDuckGo also fails, return
   `{"trend_summary": None, "image_prompt": None}`.

**Content guardrails** must be in `trend_scout_system.md`:
- No copyright/trademark-infringing content
- No content violating law or community standards

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/subagents/test_trend_scout_tools.py` | `duckduckgo_search` is a valid LangChain tool; `tavily_search` tool is configured with `max_results=5` |
| `tests/unit/agent/subagents/test_trend_scout_system.py` | System message includes summary section when summary non-empty; includes user_profile JSON; includes product names when retrieved_products non-empty; includes image prompt instruction when `generate_image=True`; omits sections when data is empty |
| `tests/unit/agent/subagents/test_trend_scout_wrapper.py` | Dynamic SystemMessage is prepended as `messages[0]`; `generate_image` flag is correctly set in subgraph state; `trend_summary` and `image_prompt` are mapped back to parent state; fallback returns `None` for both fields when all tools fail |

---

## Phase 6 — RAG Ingestion Pipeline

### Objective

Implement the full Saleor → LlamaIndex → Qdrant ingestion pipeline:
`ProductIndexer`, Qdrant collection setup, and the Celery task wiring.

### Qdrant Collection Setup

**File**: `src/app/services/qdrant_service.py` (extend existing file)

The collection must be created with BOTH dense and sparse vector configs before first insert
(per Section 3.3 of DRAFT 0.6):

```python
from qdrant_client import models

async def ensure_collection(client: AsyncQdrantClient, settings: Settings) -> None:
    """Create the products collection if it does not exist."""
    collections = await client.get_collections()
    if settings.qdrant_collection_name in [c.name for c in collections.collections]:
        return
    await client.create_collection(
        collection_name=settings.qdrant_collection_name,
        vectors_config={
            "text-dense": models.VectorParams(
                size=settings.embedding_dims,
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "text-sparse": models.SparseVectorParams(
                index=models.SparseIndexParams()
            )
        },
    )
```

**Important**: Call `ensure_collection` at startup in the FastAPI `lifespan` function
AND at the start of `reindex_all`. Never call `recreate_collection` in production — it
drops all existing data.

### TextNode Construction

**File**: `src/app/rag/indexer.py`

Implement `_build_text_node(product: ProductPayload, settings: Settings) -> TextNode`:

1. Parse `product.description` from EditorJS JSON: extract `paragraph` and `header` block
   text values and concatenate.

2. If `len(cleaned_description) > settings.description_max_chars`:
   - Call `SUMMARIZE_MODEL` to summarize. Use prompt similar to:
     `"Summarize this product description in under 150 words, preserving key features and style keywords: {text}"`
   - Use summarized text.

3. Build `node_text = f"{product.name}\n\n{final_description}"`.

4. Return:
   ```python
   TextNode(
       text=node_text,
       id_=product.product_id,  # enables idempotent upserts
       metadata={
           "product_id": product.product_id,
           "name": product.name,
           "slug": product.slug,
           "category": product.category,
           "collections": product.collections,
           "price_min": product.price_min,
           "price_max": product.price_max,
           "currency": product.currency,
           "price_range": product.price_range,
           "available": product.available,
           "saleor_url": product.saleor_url,
           "thumbnail_url": product.thumbnail_url,
       },
   )
   ```

### IngestionPipeline

**File**: `src/app/rag/indexer.py`

Implement `reindex_all`:

1. Fetch all products from Saleor via `SaleorClient.fetch_all_products()` (cursor-based
   pagination, loop until no `pageInfo.hasNextPage`).

2. For each product: convert to `ProductPayload`, call `_build_text_node`.

3. Run `IngestionPipeline`:
   ```python
   pipeline = IngestionPipeline(
       transformations=[OpenAIEmbedding(model=settings.embedding_model)],
       vector_store=vector_store,  # QdrantVectorStore with enable_hybrid=True
   )
   await pipeline.arun(nodes=text_nodes)
   ```
   Sparse vectors are generated automatically by `QdrantVectorStore` via fastembed.

4. Return `{"products_indexed": count, "duration_seconds": elapsed}`.

### Celery Task Wiring

**File**: `src/app/tasks/reindex_products.py`

```python
@celery_app.task(queue="reindex")
def reindex_products() -> None:
    indexer = ProductIndexer(settings)
    asyncio.run(indexer.reindex_all())
```

**File**: `src/app/tasks/process_webhook.py`

Ensure `PRODUCT_CREATED` and `PRODUCT_UPDATED` events call `indexer.upsert_product(data)`;
`PRODUCT_DELETED` events call `indexer.delete_product(product_id)`.

### Saleor GraphQL Query

The confirmed query for product fetching (from DRAFT 0.6 / session history):

```graphql
query Products($after: String) {
  products(first: 100, after: $after, channel: "default-channel") {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        name
        slug
        description
        isAvailable
        category { name }
        collections { edges { node { name } } }
        pricing {
          priceRange {
            start { gross { amount currency } }
            stop  { gross { amount currency } }
          }
        }
        thumbnail(size: 512, format: WEBP) { url }
      }
    }
  }
}
```

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/services/test_qdrant_service.py` | `ensure_collection` skips creation if collection already exists; creates collection with correct vector configs when missing |
| `tests/unit/test_indexer.py` | `_build_text_node` produces correct metadata fields; description is summarized when over `DESCRIPTION_MAX_CHARS`; description is not summarized when under threshold; `node.id_` equals `product_id` for idempotent upsert |
| `tests/unit/tasks/test_process_webhook.py` | `PRODUCT_CREATED` triggers `upsert_product`; `PRODUCT_UPDATED` triggers `upsert_product`; `PRODUCT_DELETED` triggers `delete_product`; unknown event type is safely ignored |
| `tests/integration/test_qdrant.py` (extend) | Full round-trip: index one product → search by name → product is in results |

---

## Phase 7 — Response Generation + SSE Streaming

### Objective

Implement `ResponseGeneratorNode`, `TitleGenerationNode`, the SSE streaming infrastructure,
and the `/api/v1/threads/{thread_id}/runs/stream` endpoint.

### SSE Infrastructure

**How it works** (DRAFT 0.6, Q-7 RESOLVED): Each HTTP request creates one `asyncio.Queue`.
The queue is passed via `config["configurable"]["sse_queue"]`. All nodes that emit SSE events
call `await queue.put(event_dict)`. The FastAPI SSE generator consumes from the queue and
yields formatted SSE lines to the client.

**Implementation steps**:

1. Define SSE event schemas in `src/app/schemas/chat.py` (7 event types per FR-003):
   ```python
   class TokenEvent(BaseModel):
       content: str

   class ProductsEvent(BaseModel):
       items: list[dict]  # {id, name, price_range, saleor_url, collections, thumbnail_url}

   class ImageReadyEvent(BaseModel):
       url: str
       prompt: str

   class ImageFailedEvent(BaseModel):
       reason: Literal["rate_limit_exceeded", "generation_failed"]

   class ThreadTitleEvent(BaseModel):
       title: str

   class DoneEvent(BaseModel):
       run_id: str
       thread_id: str
       intent: str | None
       usage: dict  # {prompt_tokens, completion_tokens, cost_usd}

   class ErrorEvent(BaseModel):
       code: str
       message: str
   ```

2. Create SSE formatter in `src/app/api/chat.py`:
   ```python
   def format_sse(event: str, data: dict) -> str:
       return f"event: {event}\ndata: {json.dumps(data)}\n\n"
   ```

3. In the endpoint handler:
   ```python
   @router.post("/{thread_id}/runs/stream")
   async def stream_run(thread_id: uuid.UUID, body: ChatRequest, current_user: CurrentUserDep):
       sse_queue: asyncio.Queue = asyncio.Queue()
       config = {
           "configurable": {
               "thread_id": str(thread_id),
               "sse_queue": sse_queue,
           }
       }

       async def run_graph():
           try:
               await graph.ainvoke(initial_state, config)
           except Exception as e:
               await sse_queue.put({"event": "error", "data": {"code": "internal", "message": str(e)}})
           finally:
               await sse_queue.put(None)  # sentinel

       asyncio.create_task(run_graph())

       async def event_generator():
           while True:
               item = await sse_queue.get()
               if item is None:
                   break
               yield format_sse(item["event"], item["data"])

       return StreamingResponse(event_generator(), media_type="text/event-stream")
   ```

4. Thread status guard (FR-014): Check `thread.status == "busy"` before creating the task.
   Return `409 Conflict` if busy. Set `status = "busy"` atomically before starting; set
   back to `"idle"` in a `finally` block after graph completes.

### ResponseGeneratorNode

**File**: `src/app/agent/nodes/synthesize.py`

**How to implement**:

1. Build system prompt from `synthesize_system.md` with injected context:
   - `user_profile` (personalization)
   - `retrieved_products` (formatted product list — name, price_range, saleor_url)
   - `trend_summary` (if not None)
   - `summary` (if non-empty)
   - `intent` (to adjust tone)

2. Stream LLM response using `RESPONSE_MODEL.astream(messages)`:
   ```python
   async for chunk in llm.astream(messages):
       await sse_queue.put({"event": "token", "data": {"content": chunk.content}})
   ```

3. When streaming is complete, emit `products` event if `retrieved_products` is non-empty:
   ```python
   await sse_queue.put({"event": "products", "data": {"items": formatted_products}})
   ```

4. Emit `done` event with token usage.

### TitleGenerationNode

**File**: `src/app/agent/nodes/generate_title.py`

**How to implement**:

1. Guard: if `state["title_generated"]` is `True`, return `{}` immediately.

2. Check `thread.title_generation_attempts >= settings.title_generation_max_attempts`:
   - If true: use truncation fallback: `state["first_user_message"][:settings.title_truncation_length]`
   - Set `title_generated = True` and persist to DB.
   - Emit `thread_title` SSE event.
   - Return `{"thread_title": title, "title_generated": True}`.

3. Call `TITLE_MODEL` with `first_user_message`: generate title ≤ 6 words.
   Prompt template: `title_system.md`.

4. On success: persist to DB, invalidate Valkey cache `threads:{user_id}:*`, emit SSE.

5. On failure: increment `title_generation_attempts` in DB; return `{}` (retry next run).

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_synthesize.py` | Token events are put to SSE queue; products event is emitted after streaming; done event includes token usage; system prompt includes user_profile, products, summary, and intent |
| `tests/unit/agent/nodes/test_generate_title.py` | Returns `{}` when `title_generated=True`; truncation fallback used when max attempts reached; SSE `thread_title` event is emitted on success; Valkey cache is invalidated on success; `title_generation_attempts` incremented on failure |
| `tests/unit/api/test_chat.py` | Returns 409 when thread is busy; SSE stream starts correctly; `error` SSE event emitted on graph exception; thread status set back to `idle` after completion |
| `tests/integration/test_sse_stream.py` | Full end-to-end: POST to `/runs/stream` → receive `token` events → receive `done` event (mocked graph) |

---

## Phase 8 — Image Generation

### Objective

Implement `ImageGenerationNode` with DALL-E, S3 upload, Valkey quota, and SSE events.

### ImageGenerationNode Implementation

**File**: `src/app/agent/nodes/generate_image.py`

**How to implement**:

1. Check trigger conditions (ALL must be true — FR-047):
   - `state.get("generate_image") == True`
   - `state.get("image_prompt") is not None` (set by TrendScoutNode or user description)
   If conditions not met, return `{}`.

2. Check Valkey daily quota:
   ```python
   quota_key = f"image_quota:{state['user_id']}:{datetime.utcnow().strftime('%Y-%m-%d')}"
   count = await valkey.get(quota_key)
   if count and int(count) >= settings.image_daily_limit:
       await sse_queue.put({"event": "image_failed", "data": {"reason": "rate_limit_exceeded"}})
       return {}
   ```

3. Determine prompt: `state.get("image_prompt")` from TrendScoutNode.
   (User description takes priority if both are available — FR-049; this requires
   the orchestrator/synthesize node to have set `image_prompt` from user description
   before TrendScout runs.)

4. Call DALL-E:
   ```python
   from openai import AsyncOpenAI
   client = AsyncOpenAI(api_key=settings.openai_api_key)
   response = await client.images.generate(prompt=prompt, n=1, size="1024x1024")
   image_url = response.data[0].url
   ```

5. Download image bytes and upload to S3:
   ```
   S3 key: images/{user_id}/{thread_id}/{timestamp}.png
   ```

6. Insert into `generated_images` table:
   ```
   {request_message_id: HumanMessage.id from current turn, url: s3_url, prompt: used_prompt}
   ```

7. Increment Valkey counter with 24h TTL.

8. Emit SSE event:
   ```python
   await sse_queue.put({"event": "image_ready", "data": {"url": s3_url, "prompt": prompt}})
   ```

9. Return `{"image_url": s3_url, "image_prompt": prompt}`.

10. On any DALL-E or S3 error: emit `image_failed {reason: "generation_failed"}`, return `{}`.

### Tests to Write

| Test File | Test Cases |
|---|---|
| `tests/unit/agent/nodes/test_generate_image.py` | Returns `{}` when `generate_image=False`; returns `{}` when `image_prompt=None`; emits `image_failed` with `rate_limit_exceeded` when quota exceeded; calls DALL-E with correct prompt; uploads to S3 with correct key pattern; increments Valkey counter; emits `image_ready` with S3 URL; emits `image_failed` with `generation_failed` on DALL-E error; inserts `generated_images` row |
| `tests/unit/services/test_s3_service.py` | Upload generates correct key; download returns bytes |

---

## Cross-Cutting Concerns

### Observability (applies from Phase 2 onwards)

All nodes must follow the logging pattern established in existing stubs (FR-067, FR-111):
```python
structlog.contextvars.bind_contextvars(
    correlation_id=state["correlation_id"],
    node="<node_name>",
)
logger.info("<action>", thread_id=state["thread_id"], ...)
```

LangSmith tracing is automatic when `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` is set.
No additional code instrumentation is needed for LangChain/LangGraph calls (FR-107).

LlamaIndex operations inside LangGraph nodes must be bridged via
`openinference-instrumentation-llama-index` (FR-108). Set up at startup:
```python
from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
LlamaIndexInstrumentor().instrument()
```

### Error Handling

All LLM API calls must use retry logic (NFR-010):
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def call_llm_with_retry(llm, messages):
    return await llm.ainvoke(messages)
```

### Prompt Safety

User message content passed to LLM prompts must be sanitized to defend against prompt
injection (FR-088). At minimum, truncate to a max length and strip null bytes before
embedding in system prompts.

### Dependencies

When a new library is needed in a phase, add it to `pyproject.toml` with an exact pinned
version and run `uv lock` to update `uv.lock`. Never install unpinned dependencies.

Key libraries to verify are present before Phase 4:
- `llama-index-vector-stores-qdrant`
- `llama-index-embeddings-openai`
- `langchain-community` (for Tavily/DuckDuckGo tools)
- `tavily-python`
- `duckduckgo-search`
- `fastembed` (pulled automatically by qdrant-client with `[fastembed]` extra)

---

## Test Coverage Requirements

- All source code in `src/` must maintain >= 80% coverage (NFR-029)
- Run after each phase: `uv run pytest --cov=src --cov-report=term-missing`
- Each node must be testable in isolation with mocked LLM responses (NFR-030)
- Do not call real OpenAI, Qdrant, or Saleor APIs in unit tests
- Use `pytest-asyncio` with `asyncio_mode = "auto"` (already configured in `pytest.ini`)
- Integration tests may use real service containers via docker-compose test profile

---

## Definition of Done (per phase)

A phase is considered complete when:
1. All listed tasks are implemented
2. All listed tests pass
3. `uv run pytest` passes with no failures
4. `uv run ruff check src/ tests/` passes with no errors
5. `uv run pyright src/` reports no type errors
6. `uv run pytest --cov=src --cov-report=term` shows >= 80% overall coverage
