# Use Case Analysis — Agentic RAG Ecommerce

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 2.1
- **Date**: 2026-06-11
- **Status**: Confirmed — use case catalogue aligned with the multi-agent
  architecture (DRAFT 0.6) and the implementation plan
- **Last revised**: UC-001 main flow expanded to include
  `SummarizeNode`, all six `OrchestratorNode` intents, and the
  three-stage `run_product_rag` pipeline (Phases 2–4 actual
  implementation in `src/app/agent/`).

> **Scope of this document** — actors, use cases, and high-level flows (WHAT
> the system does for each user, not HOW it is built).  For node designs,
> state shape, and the orchestrator's six intent values see
> [docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md).
> For phase status, audit, and test plans see
> [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md).

---

## 1. System Overview

The **Agentic RAG Ecommerce** system is an AI-powered personal stylist chatbot microservice
for the Print-on-Demand (POD) industry. It acts as an intelligent consultant that:

- Accepts natural language messages from customers via a thread-based chat API
  (`POST /api/v1/threads/{thread_id}/runs/stream`).
- Responds in the same language as the user (auto-detect; no fixed language configuration).
- Maintains multi-turn conversation state using LangGraph native checkpointer
  (`AsyncPostgresSaver`) — full `AgentState` including all messages persisted automatically.
- Builds long-term customer style profiles using LangGraph Store (`AsyncPostgresStore`),
  updated incrementally with each turn using only the profile snapshot + latest message.
- Retrieves relevant POD products from a Qdrant vector catalog using hybrid search
  (dense semantic + sparse BM25 + metadata filter).
- Searches the web for real-time seasonal design trends via Tavily (DuckDuckGo as fallback).
- Generates on-demand design images via OpenAI DALL-E when `generate_image: true` is set
  and trigger conditions are met; images are stored in AWS S3 with public URLs.
- Streams all responses via Server-Sent Events (7 event types: `token`, `products`,
  `image_ready`, `image_failed`, `thread_title`, `done`, `error`).
- Stays synchronized with Saleor via HMAC-SHA256-validated webhooks; heavy sync tasks
  are processed asynchronously by Celery + RabbitMQ workers.
- Authenticates all customers using Saleor JWT tokens verified locally via cached JWKS
  (no separate auth system).

---

## 2. Actor Identification

### 2.1 Primary Actors (initiate use cases)

| Actor ID | Actor Name | Type | Description |
|---|---|---|---|
| A-01 | Customer | Human | End user interacting via chat to get personalized POD product recommendations, design trend information, and optional generated design images |
| A-02 | System Administrator | Human | Technical operator who manages product catalog reindexing, monitors system health, and accesses user profiles (requires `is_staff: true` in Saleor JWT) |

### 2.2 Secondary Actors / External Systems

| Actor ID | Actor Name | Type | Description |
|---|---|---|---|
| A-03 | Saleor | External System | E-commerce platform; product catalog source via GraphQL API; sends HMAC-signed product lifecycle webhooks |
| A-04 | OpenAI API | External System | LLM inference (model names configurable via env vars), text embeddings (`EMBEDDING_MODEL`), and DALL-E image generation |
| A-05 | Tavily Search API | External System | Primary web search provider for real-time design trend data |
| A-06 | DuckDuckGo | External System | Fallback web search when Tavily is unavailable |
| A-07 | Qdrant Vector DB | External System | Stores product embeddings; executes hybrid dense + sparse BM25 search with metadata filtering; stores `thumbnail_url` in payload |
| A-08 | PostgreSQL 16 | External System | Hosts LangGraph checkpointer tables (agent state + messages), LangGraph Store (user profiles), and custom tables (`threads`, `generated_images`) |
| A-09 | Valkey | External System | Redis-compatible cache and rate limiting backend; DB `/0` for SlowAPI rate limiting, DB `/1` for fastapi-cache2 response caching |
| A-10 | AWS S3 | External System | Object storage for generated design images; public URLs with thread-based lifecycle |
| A-11 | RabbitMQ | External System | Message broker for Celery async task queue (webhook processing, product reindex, thread cleanup) |
| A-12 | LangSmith | External System | LLM/agent observability; receives LangGraph traces and LlamaIndex spans via OpenInference OTel bridge.  Tracing toggle: `LANGSMITH_TRACING` env var (boolean). |

### 2.3 Customer Personas

| Persona | Description | Typical Query |
|---|---|---|
| Gift Buyer | Purchasing a POD product for a friend or family member; focused on occasion, recipient preferences, and budget | "I want a printed mug for my mom's birthday, she likes floral patterns" |
| Style Explorer | Browsing for self-purchase; interested in current design trends and unique aesthetics | "What are the trending minimalist designs for t-shirts this summer?" |
| POD Seller | A designer or small seller researching product + design combinations | "What design themes pair best with canvas prints for Christmas?" |
| Comparison Shopper | Has a specific product in mind; wants to compare options by price, material, or category | "Show me all available t-shirts under 200k with eco-friendly materials" |

---

## 3. Use Case Catalog

### 3.1 Use Case Summary Table

| UC ID | Use Case Name | Primary Actor | Priority |
|---|---|---|---|
| UC-001 | Request Product Recommendations | Customer (A-01) | Must Have |
| UC-002 | Request Design Trend Information | Customer (A-01) | Must Have |
| UC-003 | Request Combined Product + Design Suggestion | Customer (A-01) | Must Have |
| UC-004 | Conduct Multi-Turn Conversation | Customer (A-01) | Must Have |
| UC-005 | View Thread History | Customer (A-01) | Should Have |
| UC-006 | Delete Thread | Customer (A-01) | Should Have |
| UC-007 | View Customer Profile | System Administrator (A-02) | Should Have |
| UC-008 | Trigger Full Product Reindex | System Administrator (A-02) | Must Have |
| UC-009 | Sync Product on Create | Saleor (A-03) | Must Have |
| UC-010 | Sync Product on Update | Saleor (A-03) | Must Have |
| UC-011 | Sync Product on Delete | Saleor (A-03) | Must Have |
| UC-S01 | Authenticate API Request | — (included) | Must Have |
| UC-S02 | Load Thread Context | — (included) | Must Have |
| UC-S03 | Update Customer Profile | — (included) | Must Have |
| UC-S04 | Validate Webhook HMAC Signature | — (included) | Must Have |
| UC-S05 | Persist Agent State | — (included) | Must Have |

### 3.2 Use Case Relationships

- **UC-003** extends **UC-001** and **UC-002** (combines both product and trend flows).
- **UC-004** extends **UC-001 / UC-002 / UC-003** (continuity across turns within a thread).
- **UC-001, UC-002, UC-003, UC-004** all include **UC-S01, UC-S02, UC-S03, UC-S05**.
- **UC-005, UC-006, UC-007, UC-008** include **UC-S01**.
- **UC-009, UC-010, UC-011** include **UC-S04**.

### 3.3 Intent-to-Node Mapping

The orchestrator's six intent values (defined in
[04-MULTI-AGENT-ARCHITECTURE-DESIGN.md §2.1](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md))
map onto the use cases as follows:

| Orchestrator intent value | Triggered use case branch | Next node |
|---|---|---|
| `need_product_search` | UC-001, UC-003 | `run_product_rag` |
| `need_trend_info` | UC-002, UC-003 | `run_trend_scout` |
| `sufficient` | All (terminal) | `synthesize` |
| `clarification_needed` | All (alternative flow) | `synthesize` |
| `out_of_scope` | All (alternative flow) | `synthesize` |
| `fallback` | All (forced when step budget low) | `synthesize` |

---

## 4. Detailed Use Case Descriptions

---

### UC-001: Request Product Recommendations

| Field | Detail |
|---|---|
| **ID** | UC-001 |
| **Name** | Request Product Recommendations |
| **Primary Actor** | Customer (A-01) |
| **Secondary Actors** | OpenAI (A-04), Qdrant (A-07), PostgreSQL (A-08), AWS S3 (A-10) |
| **Priority** | Must Have |
| **API Trigger** | `POST /api/v1/threads/{thread_id}/runs/stream` |
| **Implements FRs** | FR-001..FR-005, FR-027..FR-032, FR-033..FR-040, FR-055..FR-067, FR-068..FR-072, FR-081..FR-089 |
| **Internal graph** | `START → profiler → summarize → orchestrate → (run_product_rag ⇆ orchestrate)* → synthesize → END` (with `generate_title` parallel branch and `generate_image` parallel branch) |

**Description**
The customer sends a natural language message expressing product needs (style preference,
occasion, recipient, budget, product type) within an existing thread. The system analyzes
context, performs a 3-stage hybrid retrieval (LLM query prep → Qdrant dense+sparse search →
LLM rerank) on the product catalog, and streams a personalized ranked recommendation list.
If `generate_image: true` is set and trigger conditions are met, the system also generates
a design image via DALL-E.

**Preconditions**
1. Customer has a valid Saleor JWT (Bearer token).
2. A thread has been explicitly created via `POST /api/v1/threads`.
3. Thread is in `idle` status (not `busy` or `deleting`).
4. Qdrant contains indexed product vectors (at least one full reindex completed).
5. The parent LangGraph graph `profiler → summarize → orchestrate` is compiled
   with `AsyncPostgresSaver` (checkpointer) and `AsyncPostgresStore` (profile store).

**Main Flow**

1. Customer sends `POST /api/v1/threads/{thread_id}/runs/stream` with
   `Authorization: Bearer {saleor_jwt}` and body `{message, generate_image: bool}`.
2. System verifies JWT signature via cached JWKS (UC-S01); extracts `user_id` and `is_staff`.
3. System checks thread status — if `busy` returns 409 Conflict; if `deleting` returns 404.
4. System sets thread to `busy` status and updates `last_activity_at`.
5. System loads `AgentState` from the LangGraph checkpointer (UC-S02); populates
   `correlation_id`, `user_id`, `thread_id`, and `first_user_message` at the API boundary.
6. **TitleGenerationNode** (parallel branch, runs on every turn but no-ops when
   `title_generated == true`):
   - On the first turn only: calls LLM (`TITLE_MODEL`) with `first_user_message` to
     generate a short title (max 6 words).
   - Retries up to `TITLE_GENERATION_MAX_ATTEMPTS` times on failure.
   - On final failure: truncates `first_user_message` to `TITLE_TRUNCATION_LENGTH`
     as fallback.
   - Persists title to `threads` table, sets `title_generated = true`.
   - Emits `thread_title` SSE event immediately (does not wait for Response Generator).
   - Invalidates the `threads:{user_id}:*` cache.
7. **ProfilerNode** (per the actual implementation in
   `src/app/agent/nodes/profiler.py`, Phase 2):
   - Loads existing profile from `AsyncPostgresStore` under namespace
     `("profiles", user_id)`, key `"profile"`; defaults to `UserProfile()` when absent.
   - Scans `state["messages"]` in reverse for the latest `HumanMessage`; returns
     `{}` (no-op) when no human message is present.
   - Calls LLM (`SUMMARIZE_MODEL`) with `.with_structured_output(UserProfile)` and a
     two-field payload only: `current_profile` (JSON) + `latest_message`.  Full
     conversation history is NEVER passed (FR-028).
   - Persists the merged profile via `store.aput` and writes
     `user_profile` into `AgentState` (UC-S03).
8. **SummarizeNode** (per the actual implementation in
   `src/app/agent/nodes/summarize.py`, Phase 2):
   - Returns `{}` (no-op) when `len(messages) < MESSAGE_SUMMARIZE_THRESHOLD`
     (default 12).  Otherwise:
   - Sets initial cut to `MESSAGE_SUMMARIZE_COUNT` (default 8) and walks the
     boundary backward until `messages[cut]` is a `HumanMessage` (boundary
     alignment — never splits a Human/AI exchange pair).
   - Builds a "extend" or "new" summary instruction based on whether
     `state["summary"]` is already non-empty.
   - Calls LLM (`SUMMARIZE_MODEL`) with
     `[SystemMessage, *messages_to_summarize, HumanMessage(instruction)]`.
   - Issues `RemoveMessage` delete ops for the summarized slice and writes
     the new `summary` into `AgentState`.
9. **OrchestratorNode** (per the actual implementation in
   `src/app/agent/nodes/orchestrate.py`, Phase 3):
   - **Step-budget guard**: reads
     `config["configurable"]["remaining_steps"]`; if
     `remaining_steps <= AGENT_FALLBACK_THRESHOLD` (default 2) returns
     `{"intent": "fallback"}` immediately, without making an LLM call.
   - Binds the `update_intent` `@tool` to a `ChatOpenAI(model=ORCHESTRATOR_MODEL)`.
   - Assembles messages locally: `[SystemMessage(orchestrator_system), *state["messages"]]`
     plus optional `HumanMessage` context notes (as LOCAL variables — NEVER
     returned in the state update) about already-retrieved products
     (one line per product: `Name | Category | Price`) and any existing
     `trend_summary` text.  This invariant prevents context notes from
     accumulating on every loop iteration.
   - Forwards `correlation_id` to the LLM call via
     `config["metadata"]["correlation_id"]` (NFR-021, LangSmith trace linkage).
   - Invokes the LLM and extracts the chosen intent from the `update_intent`
     tool call via `_extract_intent` (falls back to `"fallback"` on any
     extraction failure).
   - **Classifies intent into one of six values**:
     - `need_product_search`  — needs to call `run_product_rag`
     - `need_trend_info`      — needs to call `run_trend_scout`
     - `sufficient`           — terminal; route to `synthesize`
     - `clarification_needed` — terminal; route to `synthesize`
     - `out_of_scope`         — terminal; route to `synthesize`
     - `fallback`             — terminal; route to `synthesize` (forced when
       step budget is low)
   - Returns `{"intent": <value>}` ONLY.  The node never returns a
     `"messages"` key (CRITICAL invariant enforced by
     `test_orchestrate_does_not_mutate_state_messages`).
10. **Conditional routing** (handled by `route_orchestrate` in `graph.py`):
    - `need_product_search` → `run_product_rag`
    - `need_trend_info`     → `run_trend_scout`
    - the four terminal intents → `synthesize`
11. **run_product_rag** (the `ProductRAGAgent` subgraph, per the actual implementation in
    `src/app/agent/subagents/product_rag/`, Phase 4) runs the fixed
    **3-stage `StateGraph(ProductRAGState)` pipeline**:
    - The wrapper node `run_product_rag` translates parent `AgentState` into
      a sub-state (`messages`, `correlation_id`, `summary`, `user_profile`),
      forwards the shared `AsyncQdrantClient` via
      `config["configurable"]["qdrant_aclient"]` when present, and maps
      the resulting `retrieved_products` back to the parent.
    - **Stage 1 — `prepare_query_node`**:
      - Composes a `SystemMessage` from `load_prompt("prepare_query_system")`
        + injected `## Conversation Summary` and `## User Profile` sections
        (omitted when the corresponding context is empty).
      - Unpacks `state["messages"]` as the `HumanMessage`(s) of the LLM call.
      - Calls `ChatOpenAI(model=ORCHESTRATOR_MODEL).with_structured_output(PrepareQueryOutput)`.
      - Extracts a clean English `query` (category / style / collection intent
        embedded in the query text per DRAFT 0.6 §2.3 Option B) plus
        optional metadata filters `{available: True, price_max: float}`.
      - `prepare_query` error handler falls back to the most recent
        `HumanMessage` content as the search query, with filters dropped.
    - **Stage 2 — `hybrid_search_node`**:
      - Builds a `QdrantVectorStore(enable_hybrid=True,
        fastembed_sparse_model="Qdrant/bm25", dense_vector_name="text-dense",
        sparse_vector_name="text-sparse")` (vector names match the Qdrant
        collection created by `qdrant_service.py`).
      - Embeds the query, runs `aquery(mode=HYBRID,
        similarity_top_k=QDRANT_SIMILARITY_TOP_K=12, sparse_top_k=QDRANT_SPARSE_TOP_K=12,
        hybrid_top_k=QDRANT_HYBRID_TOP_K=9)`, then fuses results via
        Relative Score Fusion (the LlamaIndex QdrantVectorStore default).
      - Translates `filters` into a Qdrant `Filter` — only `available == true`
        and `price_max <= budget` are filtered; category / collections
        ride the query text.
      - Stores the fused candidate payloads under
        `ProductRAGState.candidates`.  The
        `hybrid_search` error handler returns `candidates=[]` so the
        pipeline continues to rerank (which short-circuits to no products).
    - **Stage 3 — `llm_postprocess_node`**:
      - Composes a `SystemMessage` from `load_prompt("rerank_system")` +
        injected `## Conversation Summary` and `## User Profile` sections.
      - Puts the rewritten search `query` + the formatted candidate list
        in a single `HumanMessage`.  Raw conversation history is
        intentionally NOT forwarded (the rewritten query already encodes
        the resolved intent).
      - Calls `ChatOpenAI(model=RERANK_MODEL).with_structured_output(list[str])`.
      - Maps the returned product IDs back to the original
        `ProductPayload` dicts (description, slug, price_min, price_max,
        currency, price_range, collections, thumbnail_url, saleor_url)
        and caps the result at `QDRANT_RERANK_TOP_K` (default 3).
      - The `llm_postprocess` error handler falls back to the top-K
        candidates by raw Qdrant score order.
    - Shared LangGraph fault tolerance (`set_node_defaults` on the
      builder): `RetryPolicy(max_attempts=3, retry_on=default_retry_on)`
      for `ConnectionError` / `TimeoutError` / httpx 5xx, plus
      `TimeoutPolicy(run_timeout=60, idle_timeout=30)`.
    - Subgraph checkpointer: `checkpointer=None` (per-invocation).  The
      subgraph is compiled once at module import as
      `_PRODUCT_RAG_GRAPH` and reused for every call.
12. The parent graph wires `run_product_rag` back to `orchestrate` for
    re-evaluation.  Steps 9–11 may iterate 2–4 times per turn (e.g. product
    search first, then trend search on the next loop).
13. **Synthesize / ResponseGeneratorNode** (when orchestrator intent is
    `sufficient | clarification_needed | out_of_scope | fallback`):
    - Injects `user_profile`, `retrieved_products`, `trend_summary`, and
      `summary` into the system prompt.
    - Calls LLM (`RESPONSE_MODEL`) and streams tokens via SSE.
    - Emits a `products` SSE event block (after intro text, before
      follow-up text) when `retrieved_products` is non-empty.
    - Emits `done` with `{run_id, thread_id, intent, usage}` at the end.
14. **ImageGenerationNode** (parallel branch from `synthesize`, when
    `generate_image: true` and trigger conditions are met):
    - Checks Valkey quota counter `image_quota:{user_id}:{YYYY-MM-DD}`
      against `IMAGE_DAILY_LIMIT`.
    - Synthesizes DALL-E prompt from `AgentState.image_prompt` (set by
      TrendScout) or the user description.
    - Calls OpenAI DALL-E API, uploads result to AWS S3 at
      `images/{user_id}/{thread_id}/{timestamp}.png` (public URL).
    - Inserts a record into `generated_images` with
      `request_message_id = HumanMessage.id` of the current turn.
    - Emits `image_ready` SSE event with `{url, prompt}` (or
      `image_failed {reason}` on quota / generation failure).
15. LangGraph `AsyncPostgresSaver` auto-saves the full `AgentState`
    (including all `HumanMessage` and `AIMessage` objects with their
    UUIDs) after each node (UC-S05).
16. Thread status set back to `idle`.
17. SSE connection closed after `done` event.

**Alternative Flows**
- **9a** — Orchestrator forces `intent = "fallback"` because the step
  budget is exhausted: Response Generator runs with whatever data has
  accumulated in `AgentState` so far and acknowledges the result may be
  incomplete.
- **9b** — Intent `clarification_needed`: Response Generator asks a
  focused clarifying question before any product search.
- **9c** — Intent `out_of_scope`: Response Generator declines politely
  (the request falls outside the POD fashion domain boundary).
- **9d** — Intent `need_trend_info` (this turn also requires a trend
  lookup — UC-003 territory): orchestrator loops to `run_trend_scout`
  after `run_product_rag` returns; the next orchestration re-evaluates
  and routes to `synthesize` once both are populated.
- **11a** — `prepare_query_node` fails after retries: fallback query is
  the latest `HumanMessage` content (no metadata filters).
- **11b** — `hybrid_search_node` fails after retries: `candidates = []`;
  rerank stage short-circuits and the orchestrator proceeds with
  `retrieved_products = []` (typically routes to `clarification_needed`
  or `out_of_scope` on the next re-evaluation).
- **11c** — `llm_postprocess_node` fails after retries: top-K candidates
  are returned in raw Qdrant score order.
- **11d** — No matching products found: `retrieved_products = []` and
  the orchestrator routes to `synthesize` with "no-results" context.
- **14a** — Image rate limit exceeded (`IMAGE_DAILY_LIMIT`): emits
  `image_failed {reason: "rate_limit_exceeded"}`; the text response is
  unaffected.
- **14b** — Image generation API error: emits
  `image_failed {reason: "generation_failed"}`.

**Exception Flows**
- **E1** — Qdrant unavailable (after retries): `error` SSE event;
  structured log with `correlation_id`.
- **E2** — OpenAI timeout (orchestrator / prepare_query / rerank / synthesize):
  LangGraph `RetryPolicy` retries up to 3 times; on final failure the
  parent turn fails to `error` SSE.
- **E3** — Thread not found or belongs to another user: 404 / 403.
- **E4** — Orchestrator LLM emits no `update_intent` tool call: defensive
  `_extract_intent` returns `"fallback"`; turn continues to `synthesize`
  with whatever data was previously accumulated.

**Postconditions**
- `AgentState` with full message history (minus summarized slice) and
  accumulated `summary` persisted in LangGraph checkpointer.
- Customer profile updated in LangGraph Store (cross-thread, namespace
  `("profiles", user_id)`).
- Thread `last_activity_at` updated; thread title set (first run only).
- `retrieved_products` populated with at most `QDRANT_RERANK_TOP_K=3`
  `ProductPayload` dicts (or empty).
- If image generated: S3 object uploaded, `generated_images` record
  inserted.
- All SSE events delivered; connection closed after `done` event.

---

### UC-002: Request Design Trend Information

| Field | Detail |
|---|---|
| **ID** | UC-002 |
| **Name** | Request Design Trend Information |
| **Primary Actor** | Customer (A-01) |
| **Secondary Actors** | OpenAI (A-04), Tavily (A-05), DuckDuckGo (A-06), PostgreSQL (A-08), AWS S3 (A-10) |
| **Priority** | Must Have |
| **API Trigger** | `POST /api/v1/threads/{thread_id}/runs/stream` |

**Description**
The customer asks about design trends for a specific season, theme, or occasion. The system
searches the web in real time, summarizes findings, and generates text-to-image prompt
suggestions. Optionally generates a design image if `generate_image: true`.

**Main Flow**
1. Same as UC-001 steps 1–5.
2. **TitleGenerationNode** (parallel branch) — same as UC-001 step 6.
3. **ProfilerNode** — same as UC-001 step 7.
4. **SummarizeNode** — same as UC-001 step 8.
5. **OrchestratorNode**: step-budget guard + `update_intent` tool call
   (same as UC-001 step 9) classifies the intent as `need_trend_info`.
6. **Conditional routing** routes the intent to `run_trend_scout`.
7. **run_trend_scout** (the `TrendScoutNode`, per the actual implementation in
   `src/app/agent/nodes/trend_scout.py`, Phase 5 — currently stubbed):
   - Built with `langchain.agents.create_agent` as a ReAct loop with the
     `ORCHESTRATOR_MODEL` and toolset `[tavily_search, duckduckgo_search]`.
   - Composes a `SystemMessage` that includes
     `## Conversation Summary` + `## User Profile` +
     `## Retrieved Products` sections (each omitted when empty).
   - When the run is part of UC-003, `retrieved_products` is already
     populated — the system prompt explicitly lists them so the trend
     research can target those product categories.
   - Tavily is the primary search tool; DuckDuckGo is the fallback.
   - Returns a `TrendScoutOutput` with two fields:
     - `trend_summary` (str): 2–3 sentence trend report, written into
       `AgentState.trend_summary`.
     - `image_prompt` (str | None): **at most one** text-to-image prompt
       (DRAFT 0.6 §2.2), written into `AgentState.image_prompt`.  The
       prompt is a **separate** state field — it is NOT embedded inside
       `trend_summary`.
8. The parent graph wires `run_trend_scout` back to `orchestrate` for
   re-evaluation.  Orchestrator returns `sufficient` (step-budget guard
   permitting) and routes to `synthesize`.
9. **Synthesize / ResponseGeneratorNode** — same as UC-001 step 13.
10. **ImageGenerationNode** (parallel branch) — same as UC-001 step 14.
11. LangGraph checkpointer auto-saves `AgentState` — same as UC-001 step 15.
12. Thread status set back to `idle` — same as UC-001 step 16.
13. SSE connection closed after `done` event — same as UC-001 step 17.

**Alternative Flows**
- **9a** — Tavily unavailable: fall back to DuckDuckGo.
- **9b** — No relevant trend results found: Response Generator informs customer, suggests
  broader search terms.

**Postconditions**
- `AgentState.trend_summary` populated (2–3 sentence trend report).
- `AgentState.image_prompt` set to **at most one** prompt (or `None` if the
  trend report did not warrant an image).  The text-to-image prompt is a
  **separate** state field — it is NOT embedded inside `trend_summary`.
- Customer profile updated.
- Optional design image generated and stored in S3.

---

### UC-003: Request Combined Product + Design Suggestion

| Field | Detail |
|---|---|
| **ID** | UC-003 |
| **Name** | Request Combined Product + Design Suggestion |
| **Primary Actor** | Customer (A-01) |
| **Secondary Actors** | OpenAI (A-04), Qdrant (A-07), Tavily (A-05), DuckDuckGo (A-06), AWS S3 (A-10) |
| **Priority** | Must Have |
| **API Trigger** | `POST /api/v1/threads/{thread_id}/runs/stream` |

**Description**
Extends UC-001 and UC-002. The Orchestrator drives both Product RAG and Trend Scout before
routing to Response Generator, delivering a unified product + design recommendation.

**Main Flow**
1. Same as UC-001 steps 1–5 (request, JWT verify, status check, busy,
   load AgentState).
2. **TitleGenerationNode** (parallel branch) — same as UC-001 step 6.
3. **ProfilerNode** — same as UC-001 step 7.
4. **SummarizeNode** — same as UC-001 step 8.
5. **OrchestratorNode** (same as UC-001 step 9): step-budget guard +
   `update_intent` tool call classifies the intent as `need_product_search`.
6. **run_product_rag** (3-stage pipeline) — same as UC-001 step 11.
   Stores `retrieved_products` and returns to Orchestrator.
7. **OrchestratorNode** (re-evaluated loop): with products in hand, the
   LLM next classifies the intent as `need_trend_info` and the
   `route_orchestrate` edge routes the request to `run_trend_scout`.
8. **run_trend_scout** — same as UC-002 step 7, with the system prompt
   `## Retrieved Products` section now non-empty so the trend research
   targets the categories the user actually asked about.
9. **OrchestratorNode** (re-evaluated loop): with both
   `retrieved_products` and `trend_summary` populated, the LLM
   classifies the intent as `sufficient` and routes to `synthesize`.
10. **Synthesize / ResponseGeneratorNode**: synthesizes products +
    design trends; pairs each product with matching design concepts.
    Emits a `products` SSE event block plus streamed text via
    `RESPONSE_MODEL` — same as UC-001 step 13.
11. **ImageGenerationNode** (parallel branch) — same as UC-001 step 14.
12. LangGraph checkpointer auto-saves `AgentState` — same as UC-001
    step 15.
13. Thread status set back to `idle` — same as UC-001 step 16.
14. SSE connection closed after `done` event — same as UC-001 step 17.

**Postconditions**
- Both `AgentState.retrieved_products` and `AgentState.trend_summary` populated.
- Combined product + design response streamed to customer.

---

### UC-004: Conduct Multi-Turn Conversation

| Field | Detail |
|---|---|
| **ID** | UC-004 |
| **Name** | Conduct Multi-Turn Conversation |
| **Primary Actor** | Customer (A-01) |
| **Priority** | Must Have |
| **API Trigger** | `POST /api/v1/threads/{thread_id}/runs/stream` (repeated) |

**Description**
The customer sends multiple messages within the same thread. LangGraph checkpointer persists
the full `AgentState` (including all prior messages) across turns automatically. The
Profiler Node receives only the latest message + current profile snapshot — the profile
acts as compressed long-term memory, no full history re-read needed.

**Key Behavior**
- Same `thread_id` used across all turns; `AgentState` accumulates automatically.
- Thread title generated only on the first run (`title_generated == false`); skipped thereafter.
- Profiler: `{current_profile_json, latest_user_message}` only — token cost is fixed per turn.

**Example Interaction**
```
Turn 1: "I want a t-shirt for my boyfriend who likes streetwear"
Turn 2: "His budget is around 300k"
Turn 3: "Do you have anything with a retro skateboard design?"
```
Each turn builds on prior context without the customer needing to repeat themselves.

---

### UC-005: View Thread History

| Field | Detail |
|---|---|
| **ID** | UC-005 |
| **Name** | View Thread History |
| **Primary Actor** | Customer (A-01) |
| **Priority** | Should Have |
| **API Trigger** | `GET /api/v1/threads/{thread_id}/history` |

**Description**
The customer retrieves paginated message history for a given thread. Messages are loaded from
the LangGraph checkpointer; generated images are merged from the `generated_images` table.

**Main Flow**
1. Customer sends `GET /api/v1/threads/{thread_id}/history?before={message_id}&limit=20`.
2. System verifies JWT (UC-S01); confirms thread belongs to this user (403 if not).
3. System loads `AgentState` from checkpointer: `state = await graph.aget_state(config)`.
4. System queries `generated_images WHERE thread_id = ?` (1 query) to build
   `image_map: {request_message_id → [s3_url]}`.
5. System applies app-layer cursor pagination using `before` cursor on `message.id`.
6. System builds turn-based response: for each `(HumanMessage, AIMessage)` pair,
   attaches `image_map[human_msg.id]` to the assistant turn's `images` field.
7. Returns `{messages: [{human, assistant: {content, images}}], has_more, next_cursor}`.

**Exception Flows**
- Thread not found → 404 Not Found.
- Thread belongs to another user (non-admin) → 403 Forbidden.

---

### UC-006: Delete Thread

| Field | Detail |
|---|---|
| **ID** | UC-006 |
| **Name** | Delete Thread |
| **Primary Actor** | Customer (A-01) |
| **Priority** | Should Have |
| **API Trigger** | `DELETE /api/v1/threads/{thread_id}` |

**Description**
The customer deletes a thread. The system immediately marks it as `deleting` to block new
runs, then enqueues a Celery cleanup task to remove S3 objects and database records
asynchronously. Returns 202 Accepted immediately.

**Main Flow**
1. Customer sends `DELETE /api/v1/threads/{thread_id}`.
2. System verifies JWT (UC-S01); confirms thread belongs to this user (403 if not).
3. System sets `threads.status = 'deleting'` immediately.
4. System enqueues Celery task: delete S3 objects for thread → delete `generated_images`
   records → delete `threads` record (LangGraph checkpointer data cascades via FK).
5. System returns `202 Accepted {task_id, status: "queued"}`.

**Exception Flows**
- Thread not found → 404 Not Found.
- Thread already `deleting` → 410 Gone.

---

### UC-007: View Customer Profile

| Field | Detail |
|---|---|
| **ID** | UC-007 |
| **Name** | View Customer Profile |
| **Primary Actor** | System Administrator (A-02) |
| **Priority** | Should Have |
| **API Trigger** | `GET /api/v1/users/{user_id}/profile` |

**Description**
Admin retrieves the long-term customer profile accumulated from conversation history and
stored in LangGraph Store. Only users with `is_staff: true` in JWT claims may access this
endpoint. End customers cannot read their own raw profile via API.

**Main Flow**
1. Admin sends `GET /api/v1/users/{user_id}/profile` with `Authorization: Bearer {saleor_jwt}`.
2. System verifies JWT and confirms `is_staff: true` (UC-S01); returns 403 if not admin.
3. System reads profile from LangGraph Store namespace `("profiles", user_id)`.
4. System returns `{user_id, profile: {...}, updated_at}`.

**Exception Flows**
- Profile not found → 404 Not Found.
- Caller is not admin → 403 Forbidden.

---

### UC-008: Trigger Full Product Reindex

| Field | Detail |
|---|---|
| **ID** | UC-008 |
| **Name** | Trigger Full Product Reindex |
| **Primary Actor** | System Administrator (A-02) |
| **Secondary Actors** | Saleor (A-03), OpenAI (A-04), Qdrant (A-07), RabbitMQ (A-11) |
| **Priority** | Must Have |
| **API Trigger** | `POST /api/v1/admin/reindex` |

**Description**
Admin triggers a complete synchronization of the Saleor product catalog into Qdrant.
The endpoint enqueues a Celery task and returns 202 immediately; the reindex worker
processes products asynchronously.

**Preconditions**
1. Admin has `is_staff: true` in JWT claims.
2. Saleor GraphQL API is accessible (from Celery worker).
3. Qdrant is accessible (from Celery worker).

**Main Flow**
1. Admin sends `POST /api/v1/admin/reindex` with `Authorization: Bearer {saleor_jwt}`.
2. System verifies JWT and confirms `is_staff: true` (UC-S01).
3. System enqueues `reindex_products` Celery task on `reindex` queue.
4. System returns `202 Accepted {task_id, status: "queued"}`.
5. (Async — Celery worker): fetches all active products from Saleor GraphQL API using
   cursor-based pagination → generates embeddings (OpenAI `EMBEDDING_MODEL`) → upserts
   into Qdrant `products` collection with full metadata payload.

**Exception Flows**
- Saleor unavailable mid-reindex: worker logs failed batch, continues with remaining;
  partial results reported in task result.
- OpenAI rate limit: exponential backoff; retries failed batches.

**Postconditions**
- All active Saleor products indexed in Qdrant with current embeddings and metadata.
- Reindex task result logged with `indexed_count`, `failed_count`, `duration_seconds`.

---

### UC-009: Sync Product on Create

| Field | Detail |
|---|---|
| **ID** | UC-009 |
| **Name** | Sync Product on Create |
| **Primary Actor** | Saleor (A-03) |
| **Secondary Actors** | OpenAI (A-04), Qdrant (A-07), RabbitMQ (A-11) |
| **Priority** | Must Have |
| **API Trigger** | `POST /webhooks/saleor` (event: `PRODUCT_CREATED`) |

**Description**
When a new product is created in Saleor, the system validates the HMAC signature,
enqueues a Celery task to generate the embedding and index it in Qdrant, and returns
200 immediately to Saleor.

**Preconditions**
1. Saleor is configured with the webhook URL pointing to this system.
2. `SALEOR_WEBHOOK_SECRET` is configured in the system environment.

**Main Flow**
1. Saleor sends `POST /webhooks/saleor` with `Saleor-Signature` header and JSON body
   `{event_type: "PRODUCT_CREATED", product: {...}}`.
2. System validates HMAC-SHA256 signature using `SALEOR_WEBHOOK_SECRET` (UC-S04).
3. System enqueues `process_webhook` Celery task with payload on `webhook` queue.
4. System returns `200 OK` to Saleor immediately.
5. (Async — Celery worker): parses product payload → generates embedding (OpenAI) →
   upserts product vector with metadata into Qdrant.

**Alternative Flows**
- **2a** — Invalid HMAC: return `401 Unauthorized`; log security warning with request
  metadata; do not enqueue task.

**Postconditions**
- New product indexed in Qdrant and available for search.

---

### UC-010: Sync Product on Update

| Field | Detail |
|---|---|
| **ID** | UC-010 |
| **Name** | Sync Product on Update |
| **Primary Actor** | Saleor (A-03) |
| **Priority** | Must Have |
| **API Trigger** | `POST /webhooks/saleor` (event: `PRODUCT_UPDATED`) |

**Description**
When a product is updated in Saleor (price change, description update, availability change),
the system re-generates the embedding and upserts the new vector into Qdrant.

**Main Flow**
Identical to UC-009 with `event_type: "PRODUCT_UPDATED"`. The Qdrant upsert operation
overwrites the existing vector for the same product ID.

---

### UC-011: Sync Product on Delete

| Field | Detail |
|---|---|
| **ID** | UC-011 |
| **Name** | Sync Product on Delete |
| **Primary Actor** | Saleor (A-03) |
| **Secondary Actors** | Qdrant (A-07), RabbitMQ (A-11) |
| **Priority** | Must Have |
| **API Trigger** | `POST /webhooks/saleor` (event: `PRODUCT_DELETED`) |

**Description**
When a product is deleted in Saleor, the system enqueues a Celery task to remove the
corresponding vector from Qdrant.

**Main Flow**
1. Saleor sends `POST /webhooks/saleor` with `event_type: "PRODUCT_DELETED"`.
2. System validates HMAC signature (UC-S04).
3. System enqueues `process_webhook` Celery task.
4. System returns `200 OK` immediately.
5. (Async — Celery worker): deletes point with `id = product_id` from Qdrant.

---

## 5. Support Use Case Descriptions

### UC-S01: Authenticate API Request

All `/api/v1/` endpoints require `Authorization: Bearer {saleor_jwt}`.

**Verification flow (per request, no network call):**
1. Decode JWT header to extract `kid`.
2. Look up matching public key in cached JWKS.
3. Verify RS256 signature + `exp` claim.
4. Verify `type == "access"` (reject refresh tokens with 401).
5. Extract `user_id` (Saleor base64 GraphQL Node ID), `is_staff` (bool), and `email` (for audit).

**JWKS cache management:**
- JWKS fetched from `https://<saleor-domain>/.well-known/jwks.json` at service startup.
- Cache refreshed when an unknown `kid` is encountered (key rotation) or on a scheduled
  interval (12–24 hours).
- Saleor downtime does not affect JWT verification — cached keys are used.

**Access control:**
- `is_staff: false` → Customer role (access own threads only).
- `is_staff: true` → Admin role (access all threads and admin endpoints).

### UC-S02: Load Thread Context

Given a `thread_id`, load the latest `AgentState` from the LangGraph checkpointer:

```python
state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
```

If no checkpointer state exists (first run on this thread), LangGraph starts with an empty
`AgentState`. Thread `busy` status is set before graph invocation; any concurrent request
to the same thread receives `409 Conflict`.

### UC-S03: Update Customer Profile

Profiler Node receives only `{current_profile_json, latest_user_message}` — not full
conversation history. Calls OpenAI with a merge prompt:

```
Given the current user profile: <profile>{current_profile_json}</profile>
And the latest message: "{latest_message}"
Update the profile to reflect any new or changed preferences. Return updated profile as JSON.
```

Writes updated profile to LangGraph Store:
```python
await store.aput(("profiles", user_id), "profile", updated_profile)
```

This design keeps per-turn token cost fixed regardless of conversation length. The profile
snapshot is the compressed representation of all prior long-term preferences.

### UC-S04: Validate Webhook HMAC Signature

System computes `HMAC-SHA256(request_body, SALEOR_WEBHOOK_SECRET)` and compares it to the
value in the `Saleor-Signature` header using a constant-time comparison to prevent timing
attacks. Any mismatch results in an immediate `401 Unauthorized`.

### UC-S05: Persist Agent State

LangGraph `AsyncPostgresSaver` checkpointer automatically saves the full `AgentState`
(including both `HumanMessage` and `AIMessage` objects with their UUIDs) after each node
execution. No explicit application-level save step is required. All conversation history is
accessible via `graph.aget_state()`.

---

## 6. Resolved Questions

All open questions from the initial draft have been resolved:

| # | Question | Resolution |
|---|---|---|
| Q1 | Should the system support anonymous sessions (no user_id)? | Not supported — `user_id` extracted from Saleor JWT; anonymous access not allowed |
| Q2 | What is the session TTL? | Threads expire after 30 days of inactivity (`last_activity_at`); cleaned by Celery Beat scheduled task (nightly at 2:00 AM) |
| Q3 | Should the reindex endpoint be synchronous or asynchronous? | Async — returns `202 Accepted`, Celery worker processes in background |
| Q4 | How to handle partial Saleor outages during webhook processing? | Celery retry with exponential backoff; failed webhooks retried automatically |
| Q5 | Is text-to-image prompt generation an MVP requirement? | Yes — the Trend Scout produces **at most one** text-to-image prompt and stores it in a separate `AgentState.image_prompt` field (it is NOT embedded in `trend_summary`); image generation (DALL-E) is SHOULD priority |
| Q6 | What is the max agent iteration limit? | `MAX_AGENT_STEPS` env var maps to LangGraph `recursion_limit`; graceful fallback via `AGENT_FALLBACK_THRESHOLD` (not a hard error) |
| Q7 | How many Orchestrator intents are there? | Six: `need_product_search`, `need_trend_info`, `sufficient`, `clarification_needed`, `out_of_scope`, `fallback` (the last two only trigger `synthesize`; `fallback` is also forced when the step budget is low) |

---

## 7. Cross-References

The Main Flow steps in this document describe **WHAT** the system does.
For the matching **HOW** (node designs, prompts, state shape, error
handlers) refer to the design and implementation ground truth:

- **Architecture design** —
  [04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md)
  - §1.3 Parent Graph Topology — `profiler → summarize → orchestrate` edge
    order and parallel `generate_title` / `generate_image` branches.
  - §2.1 OrchestratorNode — the six-intent `update_intent` tool, the
    step-budget guard (`AGENT_FALLBACK_THRESHOLD`), and the local
    context-note pattern.
  - §2.2 TrendScoutNode — LangChain `create_agent` ReAct loop with
    `TrendScoutOutput(trend_summary, image_prompt)`.
  - §2.3 ProductRAGAgent — 3-stage `StateGraph` pipeline
    (prepare_query → hybrid_search → llm_postprocess).
  - §2.4 ProfilerNode — `SUMMARIZE_MODEL`, two-field payload
    (current_profile + latest_message).
- **Phase tracking** (chronological log of what was actually built):
  - [temp/phase-2-profile-and-memory-management.md](../../temp/phase-2-profile-and-memory-management.md)
    — `ProfilerNode` and `SummarizeNode` actual implementation
    (Phases 2.4 and 2.5).
  - [temp/phase-3-orchestration.md](../../temp/phase-3-orchestration.md)
    — `OrchestratorNode` actual implementation, including the
    `test_orchestrate_does_not_mutate_state_messages` invariant.
  - [temp/phase-4-product-rag.md](../../temp/phase-4-product-rag.md)
    — `ProductRAGAgent` 3-stage pipeline, fault tolerance, Qdrant
    vector names (`text-dense` / `text-sparse`), and the four
    `QDRANT_*_TOP_K` env vars.
- **Master plan** —
  [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md)
  — phase status (Phases 1–4 DONE; 5–14 PENDING), audit notes, test
  plans, Definition of Done.
