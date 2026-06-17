# Requirements Specification — Agentic RAG Ecommerce

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 2.0
- **Date**: 2026-06-11
- **Status**: Confirmed — aligned with the multi-agent architecture (DRAFT 0.6)
  and the implementation plan

> **Scope of this document** — functional and non-functional requirements,
> system constraints, database schema, API contract summary, and the
> environment variable registry (WHAT the system must do).  For node
> designs, state shape, and the orchestrator's six intent values see
> [docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md).
> For phase status, audit, and test plans see
> [docs/analysis/05-IMPLEMENTATION-PLAN.md](05-IMPLEMENTATION-PLAN.md).

---

## 1. Functional Requirements

Functional requirements are grouped by feature domain. Each requirement is tagged with a
priority level: **[MUST]** (MVP), **[SHOULD]** (high value), **[COULD]** (nice to have).

---

### 1.1 Natural Language Understanding & Chat Interface

| ID | Requirement | Priority |
|---|---|---|
| FR-001 | The system must accept natural language text messages from customers via `POST /api/v1/threads/{thread_id}/runs/stream` | MUST |
| FR-002 | The system must support multi-turn conversations by loading the full `AgentState` (including all prior messages) from the LangGraph checkpointer for each request | MUST |
| FR-003 | The system must stream the agent response via Server-Sent Events (SSE) using exactly 7 event types: `token`, `products`, `image_ready`, `image_failed`, `thread_title`, `done`, `error` | MUST |
| FR-004 | The system must assign a unique `correlation_id` to each chat request for end-to-end traceability | MUST |
| FR-005 | The system must respond in the same language as the user's input (auto-detect via LLM; no fixed language configuration) | SHOULD |

**SSE Event Schema:**

| Event | Data Fields | When Emitted |
|---|---|---|
| `token` | `{"content": "..."}` | LLM is streaming text tokens |
| `products` | `{"items": [{id, name, price_range, saleor_url, tags, thumbnail_url}]}` | After intro text, before follow-up text (block separator) |
| `image_ready` | `{"url": "...", "prompt": "..."}` | After S3 upload completes |
| `image_failed` | `{"reason": "rate_limit_exceeded\|generation_failed"}` | If image generation fails |
| `thread_title` | `{"title": "..."}` | When TitleGenerationNode succeeds (first successful run, may be run 1–3 with retries) |
| `done` | `{"run_id", "thread_id", "intent", "usage": {"prompt_tokens", "completion_tokens", "cost_usd"}}` | End of stream |
| `error` | `{"code": "...", "message": "..."}` | Any pipeline error |

---

### 1.2 Thread Management

| ID | Requirement | Priority |
|---|---|---|
| FR-011 | The system must support explicit thread creation via `POST /api/v1/threads`; threads are not auto-created on first message | MUST |
| FR-012 | The system must store thread metadata (`thread_id`, `user_id`, `title`, `title_generated`, `title_generation_attempts`, `status`, `created_at`, `updated_at`, `last_activity_at`) in the custom `threads` PostgreSQL table | MUST |
| FR-013 | The system must enforce thread status transitions: `idle` → `busy` (on run start) → `idle` (on run complete) → `deleting` (on DELETE request) | MUST |
| FR-014 | The system must return `409 Conflict` if a `POST /runs/stream` is received on a thread with `status = 'busy'` | MUST |
| FR-015 | The system must expose `GET /api/v1/threads` to list all threads belonging to the authenticated user, sorted by `updated_at DESC`, using cursor-based pagination (`?before={thread_id}&limit=N`) | SHOULD |
| FR-016 | The system must expose `GET /api/v1/threads/{thread_id}` to retrieve thread metadata; customers can only access their own threads; admins can access all | SHOULD |
| FR-017 | The system must expose `DELETE /api/v1/threads/{thread_id}` which immediately sets `status = 'deleting'` and enqueues a Celery cleanup task; returns `202 Accepted` | SHOULD |
| FR-018 | Threads must expire after 30 days of inactivity (`last_activity_at < now() - INTERVAL '30 days'`); a Celery Beat scheduled task must delete expired thread S3 objects, image records, and thread records nightly | MUST |
| FR-019 | Thread history must be exposed via `GET /api/v1/threads/{thread_id}/history` with cursor-based pagination (`?before={message_id}&limit=N`); messages are read from the LangGraph checkpointer with app-layer pagination | SHOULD |
| FR-020 | Generated images must be attached to the AIMessage turn in thread history by matching `generated_images.request_message_id` with the corresponding `HumanMessage.id` | MUST |

---

### 1.3 Thread Auto-Naming

| ID | Requirement | Priority |
|---|---|---|
| FR-021 | The system must auto-generate a thread title from the first user message using `TitleGenerationNode` (a dedicated LangGraph node that runs in parallel with the main pipeline on the first run only) | SHOULD |
| FR-022 | Title generation must use the LLM specified by `TITLE_MODEL` env var with a lightweight prompt: generate a title of max 6 words from `first_user_message` | SHOULD |
| FR-023 | Title generation must retry up to `TITLE_GENERATION_MAX_ATTEMPTS` times (default: 3) across consecutive runs; if all attempts fail, the system must fall back to truncating `first_user_message` to `TITLE_TRUNCATION_LENGTH` characters | SHOULD |
| FR-024 | Once a title is finalized (LLM success or truncation fallback), `title_generated` must be set to `true`; no further title updates are made regardless of subsequent conversation | SHOULD |
| FR-025 | On successful title generation, the system must emit a `thread_title` SSE event immediately without waiting for the Response Generator | SHOULD |
| FR-026 | On successful title generation, the system must invalidate the thread list cache for the user (`threads:{user_id}:*`) | SHOULD |

---

### 1.4 User Profile Management

| ID | Requirement | Priority |
|---|---|---|
| FR-027 | The system must automatically extract and update customer attributes from conversation using the Profiler Node on every chat turn | MUST |
| FR-028 | The Profiler Node must use an LLM call with only two inputs: `{current_profile_json, latest_user_message}` — not the full conversation history | MUST |
| FR-029 | Extracted attributes must include: `age_group`, `style_preferences`, `product_interests`, occasion context, and recipient context | MUST |
| FR-030 | Customer profiles must be persisted in LangGraph Store (`AsyncPostgresStore`) under namespace `("profiles", user_id)` as a key-value entry; no custom `user_profiles` table | MUST |
| FR-031 | Profile updates must use an LLM-driven merge strategy: the LLM resolves conflicts (e.g., preference changes) using context from the latest message | MUST |
| FR-032 | The system must expose `GET /api/v1/users/{user_id}/profile` to retrieve a customer profile; this endpoint requires `is_staff: true` (Admin only) | SHOULD |

---

### 1.5 Product RAG Engine

| ID | Requirement | Priority |
|---|---|---|
| FR-033 | The system must index product data from Saleor into Qdrant using LlamaIndex as the data pipeline layer | MUST |
| FR-034 | Each product must be represented as a dense vector embedding generated from the concatenation of `product.name` and `product.description` using the model specified by `EMBEDDING_MODEL` env var (default: `text-embedding-3-small`, dims: `EMBEDDING_DIMS`, default: `1536`) | MUST |
| FR-035 | Each Qdrant point must store the following metadata payload: `product_id`, `name`, `description` (plain text — **full** cleaned description, **NOT** truncated; HTML tags stripped, no length cap), `slug`, `available` (bool), `price_min` (float), `price_max` (float), `currency` (ISO 4217), `price_range` (display string, e.g. "100,000 - 300,000 VND"), `collections[]` (Saleor collection slugs), `thumbnail_url`, `saleor_url` | MUST |
| FR-035a | The product indexer must build the **embedding text** as `f"{product.name}\n\n{product_description_for_embedding}"`, where `product_description_for_embedding` is the cleaned (HTML-stripped) description used as-is when `len(cleaned) <= DESCRIPTION_MAX_CHARS` (default 500) and the **`SUMMARIZE_MODEL`-generated summary** when `len(cleaned) > DESCRIPTION_MAX_CHARS`.  `DESCRIPTION_MAX_CHARS` is an **embedding-side** threshold only — it does NOT affect the `description` field stored in the Qdrant metadata payload (which is always the full cleaned text per FR-035).  See architecture doc §3.1 for rationale. | MUST |
| FR-036 | The system must perform hybrid search combining dense semantic vectors (`EMBEDDING_MODEL`) and sparse keyword vectors (Qdrant FastEmbed BM25 built-in) | MUST |
| FR-037 | Hybrid search must support metadata filtering by: `available == true` (hard filter, exclude out-of-stock) and `price_max <= {client_budget}` (when a budget is provided).  Category / collection / style attributes are NOT metadata filters — they are part of the query text rewritten by the LLM in `prepare_query_node` | MUST |
| FR-038 | The system must return a configurable top-k ranked product results.  The default pipeline uses four env vars: `QDRANT_SPARSE_TOP_K=12` (per-leg sparse retrieval), `QDRANT_SIMILARITY_TOP_K=12` (per-leg dense retrieval), `QDRANT_HYBRID_TOP_K=9` (post-RRF candidates fed to the reranker), `QDRANT_RERANK_TOP_K=3` (final results returned to the agent) | MUST |
| FR-039 | The Product RAG Engine must be implemented as a LangGraph `StateGraph` (3-stage fixed pipeline: `prepare_query_node` → `hybrid_search_node` → `llm_postprocess_node`).  It is NOT a ReAct agent — there are no internal tool-use decision points | MUST |
| FR-040 | The Product RAG Node must formulate the search query in English before calling Qdrant (regardless of user input language); multilingual handling is done by the LLM, not the embedding model | MUST |
| FR-041 | REMOVED — superseded by FR-039.  The Product RAG pipeline uses a fixed 3-stage flow; there is no LLM-driven strategy selection | — |

---

### 1.6 Trend Scout & Design Suggestion

| ID | Requirement | Priority |
|---|---|---|
| FR-042 | The Trend Scout Node must call the Tavily Search API to retrieve real-time design trend information | MUST |
| FR-043 | The system must fall back to DuckDuckGo if Tavily returns an error or is unavailable | MUST |
| FR-044 | The Trend Scout Node must summarize web search results into a structured trend report (top themes, color palettes, styles) stored in `AgentState.trend_summary` | MUST |
| FR-045 | The system must generate **at most one** text-to-image prompt from the trend summary using an LLM.  The prompt is stored in a **separate** `AgentState.image_prompt` field (or `None` if the trend report did not warrant an image).  The text-to-image prompt is NOT embedded inside `trend_summary` | SHOULD |
| FR-046 | Web search must be implemented as a LangChain `@tool` callable by the LangGraph agent | MUST |

---

### 1.7 Image Generation

| ID | Requirement | Priority |
|---|---|---|
| FR-047 | The system must support on-demand design image generation via OpenAI DALL-E when all trigger conditions are met: (1) `generate_image: true` in request, (2) user message references design or imagery, (3) design suggestions or user description are available | SHOULD |
| FR-048 | Image generation must run inline (synchronous within the LangGraph pipeline, parallel with the Response Generator); it must NOT use Celery | SHOULD |
| FR-049 | The Image Generation Node must synthesize a DALL-E prompt from `AgentState.trend_summary` and/or user message description; user description takes priority over trend suggestions | SHOULD |
| FR-050 | Generated images must be uploaded to AWS S3 with the key format `images/{user_id}/{thread_id}/{timestamp}.png` and stored with a public (permanent) URL | SHOULD |
| FR-051 | The system must insert a record into `generated_images` table with `request_message_id = HumanMessage.id` of the current turn immediately after S3 upload | SHOULD |
| FR-052 | The system must rate-limit image generation per user per day using a Valkey counter (`image_quota:{user_id}:{YYYY-MM-DD}` with 24h TTL); limit is configurable via `IMAGE_DAILY_LIMIT` env var | SHOULD |
| FR-053 | On image generation success, the system must emit an `image_ready` SSE event with `{url, prompt}`; on failure, an `image_failed` event with `{reason: "rate_limit_exceeded|generation_failed"}` | SHOULD |
| FR-054 | Images must follow the thread lifecycle: they exist as long as the thread exists; when a thread is deleted or expires (30-day inactivity), all associated S3 objects must be deleted by the cleanup job | SHOULD |

---

### 1.8 Agent Orchestration (LangGraph)

| ID | Requirement | Priority |
|---|---|---|
| FR-055 | The agent must use LangGraph to define a stateful multi-agent graph with eight primary nodes: `TitleGenerationNode`, `ProfilerNode`, `SummarizeNode`, `OrchestratorNode`, `ProductRAGAgent`, `TrendScoutNode`, `ImageGenerationNode`, `ResponseGeneratorNode` | MUST |
| FR-056 | Nodes may be implemented as one of three patterns depending on complexity: (a) LangGraph sub-graph (nested compiled graph for complex conditional flows, used by `ProductRAGAgent`), (b) LangChain `create_agent` ReAct agent (for LLM + tool binding with internal loop, used by `TrendScoutNode`), (c) plain Python function (for fixed logic, routing, transformations, used by the simple nodes) | MUST |
| FR-057 | The `AgentState` schema must include the FR-required fields plus the routing, memory, and generation control fields listed in [04-MULTI-AGENT-ARCHITECTURE-DESIGN.md §4](04-MULTI-AGENT-ARCHITECTURE-DESIGN.md).  FR fields: `messages: list[BaseMessage]`, `user_profile: dict`, `retrieved_products: list[ProductItem]`, `trend_summary: str \| None`, `thread_title: str \| None`, `correlation_id: str`.  Additional required fields: `user_id`, `thread_id`, `intent`, `title_generated`, `fallback_count`, `image_url`, `image_prompt`, `summary`, `generate_image`, `first_user_message` | MUST |
| FR-058 | The Orchestrator Node must classify user intent into one of six values: `need_product_search`, `need_trend_info`, `sufficient`, `clarification_needed`, `out_of_scope`, `fallback` | MUST |
| FR-059 | The Orchestrator Node must read `config["remaining_steps"]` before every routing decision; if `remaining_steps <= AGENT_FALLBACK_THRESHOLD`, it must force intent to `fallback` regardless of query content | MUST |
| FR-060 | The `fallback` intent routes to the Response Generator with a best-effort prompt; the Response Generator uses all data collected so far in `AgentState` and acknowledges that results may be incomplete | MUST |
| FR-061 | The `out_of_scope` intent routes to the Response Generator which declines politely; the Orchestrator must use POD fashion e-commerce as the domain boundary (style advice is in-scope; unrelated general queries are out-of-scope) | MUST |
| FR-062 | The graph must enforce a maximum iteration ceiling via `MAX_AGENT_STEPS` env var, which maps directly to LangGraph `recursion_limit`; this is the hard ceiling — `AGENT_FALLBACK_THRESHOLD` provides the graceful degradation threshold below it | MUST |
| FR-063 | `ProductRAGAgent` and `TrendScoutNode` must route back to the Orchestrator after completion for re-evaluation | MUST |
| FR-064 | `TitleGenerationNode` must run as a parallel branch on the first run only (when `title_generated == false`), independent of the main pipeline; it requires only `first_user_message` | SHOULD |
| FR-065 | `ImageGenerationNode` must run as a parallel branch when trigger conditions are met, independent of `ResponseGeneratorNode` | SHOULD |
| FR-066 | The graph must be compiled with `AsyncPostgresSaver` as checkpointer and `AsyncPostgresStore` as store | MUST |
| FR-067 | The system must log all node transitions with `correlation_id`, `thread_id`, node name, and elapsed time | MUST |

---

### 1.9 Response Generation & SSE Streaming

| ID | Requirement | Priority |
|---|---|---|
| FR-068 | The Response Generator Node must synthesize `user_profile`, `retrieved_products`, and `trend_summary` into a single coherent personalized response | MUST |
| FR-069 | Responses must use the interleaved SSE block model: text tokens → `products` event (block separator) → text tokens → `image_ready` event → `done` event | MUST |
| FR-070 | The `products` SSE event must include structured product data: `{id, name, price_min, price_max, currency, price_range, collections, saleor_url, thumbnail_url}`; product links are in the product block, not embedded in text tokens.  The legacy `tags` field is replaced by `collections` (Saleor collection slugs) | MUST |
| FR-071 | The Response Generator must use the model specified by `RESPONSE_MODEL` env var for response synthesis (heavier model) | MUST |
| FR-072 | The Orchestrator, Profiler, classification, summarize, and rerank tasks must use the lightweight model family (`ORCHESTRATOR_MODEL` / `SUMMARIZE_MODEL` / `RERANK_MODEL` env vars, all default to `gpt-5.4-mini`).  Each task's prompt is its own externalized `.md` file | MUST |

---

### 1.10 Saleor Integration

| ID | Requirement | Priority |
|---|---|---|
| FR-073 | The system must authenticate with Saleor GraphQL API using a configured app token (`SALEOR_APP_TOKEN`) env var | MUST |
| FR-074 | The system must fetch the full product catalog from Saleor using cursor-based paginated GraphQL queries | MUST |
| FR-075 | Each fetched product must include at minimum: `id`, `name`, `description`, `category.name`, `pricing.priceRange`, `isAvailable`, `tags`, `thumbnail.url` (illustrative — verify against Saleor schema at implementation) | MUST |
| FR-076 | The system must expose a single webhook endpoint `POST /webhooks/saleor` to receive all Saleor product lifecycle events | MUST |
| FR-077 | The webhook endpoint must handle `PRODUCT_CREATED`, `PRODUCT_UPDATED`, and `PRODUCT_DELETED` event types | MUST |
| FR-078 | `PRODUCT_CREATED` and `PRODUCT_UPDATED` events must enqueue a `process_webhook` Celery task that generates an embedding and upserts into Qdrant | MUST |
| FR-079 | `PRODUCT_DELETED` events must enqueue a `process_webhook` Celery task that deletes the corresponding vector from Qdrant | MUST |
| FR-080 | Webhook processing must be idempotent: re-processing the same event must not result in duplicate vectors in Qdrant (upsert semantics) | MUST |

---

### 1.11 Authentication & Security

| ID | Requirement | Priority |
|---|---|---|
| FR-081 | All `/api/v1/` endpoints must require `Authorization: Bearer {saleor_jwt}`; the `X-API-Key` header is not used | MUST |
| FR-082 | The system must verify Saleor JWT tokens locally using cached JWKS (`RS256` algorithm); no network call is made per request | MUST |
| FR-083 | JWKS must be fetched from `https://<saleor-domain>/.well-known/jwks.json` at startup and cached in memory; cache must be refreshed on unknown `kid` or on a scheduled interval (12–24h) | MUST |
| FR-084 | JWT verification must extract: `user_id` (Saleor base64 GraphQL Node ID as `TEXT`), `is_staff` (bool for admin role), `type` (must equal `"access"`); refresh tokens must be rejected with 401 | MUST |
| FR-085 | Admin-only endpoints (`GET /users/{id}/profile`, `POST /admin/reindex`, `GET /admin/threads`) must additionally require `is_staff: true` from JWT claims; return 403 Forbidden otherwise | MUST |
| FR-086 | All Saleor webhook requests must be authenticated via `HMAC-SHA256` signature validation using `SALEOR_WEBHOOK_SECRET`; comparison must use constant-time function to prevent timing attacks | MUST |
| FR-087 | All user input at API boundaries must be validated using Pydantic models with strict field definitions | MUST |
| FR-088 | User-supplied message content must be sanitized before injection into LLM prompts to defend against prompt injection attacks | MUST |
| FR-089 | LLM-generated content must be validated; any executable code or HTML must be escaped before returning to the client | SHOULD |

---

### 1.12 Rate Limiting

| ID | Requirement | Priority |
|---|---|---|
| FR-090 | The system must implement per-user rate limiting using `slowapi` with Valkey DB `/0` as the storage backend | MUST |
| FR-091 | Rate limit key must be `user_id` extracted from the verified JWT (not IP address, as the service runs behind a load balancer).  Wiring of rate limits to the API routers is implemented in Phase 5.1 | MUST |
| FR-092 | Rate limits must be configurable via environment variables; when a limit is exceeded the system must return `429 Too Many Requests` | MUST |
| FR-093 | Rate limits per endpoint group (all values are defaults; configurable via env vars): `POST /runs/stream` = `RATE_LIMIT_CHAT` (default: 20/min), `POST /threads` = `RATE_LIMIT_THREAD_CREATE` (default: 10/min), all GET endpoints (thread list, thread detail, history, profile) share `RATE_LIMIT_READ` (default: 60/min), write/delete = `RATE_LIMIT_WRITE` (default: 10/min), `POST /admin/reindex` = `RATE_LIMIT_REINDEX` (default: 2/hour) | MUST |
| FR-094 | Webhook endpoint (`POST /webhooks/saleor`) and infrastructure endpoints (`/health`, `/ready`, `/metrics`) must not have rate limiting applied | MUST |

---

### 1.13 Caching

| ID | Requirement | Priority |
|---|---|---|
| FR-095 | The system must cache `GET /api/v1/threads` responses using `fastapi-cache2` with Valkey DB `/1` as the backend | SHOULD |
| FR-096 | Cache key must be scoped per user and per pagination cursor: `threads:{user_id}:{before}:{limit}` | SHOULD |
| FR-097 | Cache TTL must be configurable via `THREAD_LIST_CACHE_TTL` env var (default: 120 seconds) | SHOULD |
| FR-098 | The cache must be invalidated immediately (not wait for TTL) when any of the following occurs: thread created, thread deleted, thread title generated; invalidation pattern: `threads:{user_id}:*` | SHOULD |

---

### 1.14 Async Task Infrastructure

| ID | Requirement | Priority |
|---|---|---|
| FR-099 | The system must use Celery with RabbitMQ as the message broker for all background task processing | MUST |
| FR-100 | Celery workers must handle the following task types on dedicated queues: `process_webhook` (queue: `webhook`), `reindex_products` (queue: `reindex`), `cleanup_expired_threads` (queue: `cleanup`) | MUST |
| FR-101 | Celery Beat must schedule the `cleanup_expired_threads` task to run nightly at 2:00 AM | MUST |
| FR-102 | Image generation must NOT use Celery — it runs inline within the LangGraph pipeline via SSE stream | MUST |

---

### 1.15 Administration & Indexing

| ID | Requirement | Priority |
|---|---|---|
| FR-103 | The system must expose `POST /api/v1/admin/reindex` to trigger a full Saleor → Qdrant product catalog reindex (async, returns 202 Accepted) | MUST |
| FR-104 | The system must expose `GET /api/v1/admin/threads` to list all threads across all users with pagination (admin only) | SHOULD |
| FR-105 | The system must expose `GET /health` returning `200 OK` when the service is running (liveness probe) | MUST |
| FR-106 | The system must expose `GET /ready` returning `200 OK` only when all external dependencies (Qdrant, PostgreSQL, Valkey) are reachable (readiness probe) | MUST |

---

### 1.16 Observability

| ID | Requirement | Priority |
|---|---|---|
| FR-107 | LangGraph and LangChain traces must be automatically sent to LangSmith by setting `LANGSMITH_TRACING=true`; the SDK picks up the project from `LANGSMITH_PROJECT` and the endpoint from `LANGSMITH_ENDPOINT` (default: `https://aws.api.smith.langchain.com`).  No additional code instrumentation required.  The legacy `LANGCHAIN_TRACING_V2` env var is not used | MUST |
| FR-108 | LlamaIndex operations (retrieval, embedding, reranking) inside LangGraph nodes must be bridged to LangSmith via `openinference-instrumentation-llama-index` (OTel spans → LangSmith OTel endpoint) | MUST |
| FR-109 | The system must expose Prometheus metrics at `GET /metrics` using `prometheus-fastapi-instrumentator` (zero-configuration auto-instrumentation) | MUST |
| FR-110 | All application logs must use `structlog` with JSON renderer; output to stdout for Docker log capture | MUST |
| FR-111 | Each log record must include standard fields: `timestamp`, `level`, `service`, `correlation_id`, `thread_id`, `user_id`, `node`, `message` | MUST |
| FR-112 | Promtail must scrape Docker container logs (stdout), parse JSON fields, and push to Loki with labels: `level`, `service` | SHOULD |
| FR-113 | Grafana must be configured with two data sources (Prometheus and Loki) and include dashboards for: API latency p50/p95/p99, error rate, LLM token usage and cost, log explorer | SHOULD |

---

## 2. Non-Functional Requirements

### 2.1 Performance & Latency

| ID | Requirement | Metric |
|---|---|---|
| NFR-001 | First-token response latency for standard product recommendation queries | < 3 seconds (p95) |
| NFR-002 | Hybrid search query execution latency in Qdrant | < 500ms (p95) |
| NFR-003 | Webhook processing latency (receive → 200 OK) | < 200ms |
| NFR-004 | Webhook Celery task latency (enqueue → Qdrant upsert complete) | < 5 seconds (p95) |
| NFR-005 | Full product reindex throughput for a catalog of 1,000 products | < 10 minutes |

---

### 2.2 Scalability

| ID | Requirement |
|---|---|
| NFR-006 | The FastAPI application layer must be stateless to support horizontal scaling via Docker replica sets |
| NFR-007 | All conversation state and profile data must be stored in external datastores (LangGraph checkpointer + Store in PostgreSQL); no in-memory state between requests |
| NFR-008 | The Qdrant collection must support a minimum of 100,000 product vectors without degradation in search latency |
| NFR-009 | Celery workers must be independently scalable; worker count configurable without code changes |

---

### 2.3 Reliability & Error Handling

| ID | Requirement |
|---|---|
| NFR-010 | All outbound calls to LLM APIs, search APIs, and Saleor must implement retry logic with exponential backoff (max 3 retries, base delay 1s) |
| NFR-011 | The system must implement a circuit breaker pattern for calls to OpenAI and Tavily to prevent cascade failures |
| NFR-012 | The LangGraph agent must degrade gracefully when approaching the max iteration limit: `fallback` intent is triggered at `AGENT_FALLBACK_THRESHOLD` remaining steps; LangGraph `recursion_limit` (`MAX_AGENT_STEPS`) is the hard ceiling |
| NFR-013 | Webhook processing must be idempotent: re-processing the same `PRODUCT_CREATED` or `PRODUCT_UPDATED` event must not result in duplicate vectors in Qdrant |
| NFR-014 | Database connection pools must be properly sized and released; no connection leaks |
| NFR-015 | Thread deletion and expiry cleanup must be atomic: S3 objects deleted before database records to avoid orphaned storage |

---

### 2.4 Security

| ID | Requirement |
|---|---|
| NFR-016 | No API keys, secrets, or credentials may appear in source code, commit history, or application logs |
| NFR-017 | All secrets must be managed via environment variables (`.env` locally; CI/CD secrets in production) |
| NFR-018 | Dependencies must be regularly audited for known CVEs using `uv audit` |
| NFR-019 | The system must not log raw user message content at INFO level or above (only at DEBUG level with explicit opt-in) |
| NFR-020 | All HTTP communication with external APIs must use TLS (HTTPS) |

---

### 2.5 Observability

| ID | Requirement |
|---|---|
| NFR-021 | Every LLM API call must be traced in LangSmith, capturing: `prompt_tokens`, `completion_tokens`, `latency_ms`, `model`, `node_name`, `correlation_id`, `thread_id` |
| NFR-022 | All application logs must be structured JSON format; parseable by Promtail with label extraction for `level` and `service` |
| NFR-023 | Prometheus metrics must include: request count (by endpoint), request latency histogram (p50/p95/p99), error rate, in-flight requests |
| NFR-024 | All webhook events must be logged at INFO level with `product_id`, `event_type`, and processing duration |

---

### 2.6 Maintainability & Code Quality

| ID | Requirement |
|---|---|
| NFR-025 | All LLM prompt templates must be externalized as `.md` files in the `prompts/` directory; no hard-coded prompt strings in Python code |
| NFR-026 | All model names, configuration values, and API URLs must be loaded from environment variables via `pydantic-settings`; see Section 6 for the full environment variable registry |
| NFR-027 | Dependency versions must be pinned with exact versions in `pyproject.toml`; `uv.lock` must be committed |
| NFR-028 | Code quality must be enforced with `ruff` (linting + formatting) and `pyright` (type checking) via pre-commit hooks |
| NFR-029 | Unit test coverage must be maintained at >= 80% for all source code in `src/` |
| NFR-030 | Each agent node must be implemented as a standalone module testable in isolation with mocked LLM responses |

---

## 3. System Constraints

| ID | Constraint |
|---|---|
| SC-001 | Python 3.12 is the required runtime |
| SC-002 | LangGraph is the agent orchestration framework; the workflow must be a compiled LangGraph state graph with `AsyncPostgresSaver` and `AsyncPostgresStore` |
| SC-003 | LlamaIndex is the RAG pipeline framework for product indexing and retrieval inside LangGraph nodes |
| SC-004 | OpenAI is the primary LLM and embedding provider; model names are configurable via env vars |
| SC-005 | Qdrant is the vector database; no alternative vector DB is supported in the initial architecture |
| SC-006 | PostgreSQL 16 is the relational database |
| SC-007 | Saleor is the e-commerce backend; the system has read-only product access (no write-back to Saleor) |
| SC-008 | Valkey (Redis-compatible) is used for both rate limiting and response caching |
| SC-009 | Celery + RabbitMQ is the async task infrastructure |
| SC-010 | AWS S3 is the object storage for generated images |
| SC-011 | All services must be deployable via Docker Compose; no Kubernetes for MVP |

---

## 4. Database Schema

### 4.1 LangGraph-Managed Tables (do not modify manually)

| Table | Managed By | Purpose |
|---|---|---|
| `checkpoints` | `AsyncPostgresSaver` | Full `AgentState` snapshot per thread per checkpoint |
| `checkpoint_blobs` | `AsyncPostgresSaver` | Binary data for large state values |
| `checkpoint_writes` | `AsyncPostgresSaver` | Pending writes buffer |
| `store` | `AsyncPostgresStore` | Key-value entries for long-term cross-thread memory (user profiles) |

**LangGraph persistence initialization:**
```python
async with (
    AsyncPostgresStore.from_conn_string(settings.DATABASE_URL) as store,
    AsyncPostgresSaver.from_conn_string(settings.DATABASE_URL) as checkpointer,
):
    graph = builder.compile(checkpointer=checkpointer, store=store)
```

**User profile access pattern (LangGraph Store):**
- Write: `await store.aput(("profiles", user_id), "profile", profile_dict)`
- Read: `await store.asearch(("profiles", user_id))`
- Namespace: `("profiles", user_id)` — cross-thread, per user

### 4.2 Custom PostgreSQL Tables

**Table: `threads`**

```sql
CREATE TABLE IF NOT EXISTS threads (
    id                        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                   TEXT        NOT NULL,
    title                     TEXT,
    status                    TEXT        NOT NULL DEFAULT 'idle'
                                          CHECK (status IN ('idle', 'busy', 'deleting')),
    title_generated           BOOLEAN     NOT NULL DEFAULT FALSE,
    title_generation_attempts SMALLINT    NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_threads_user_id ON threads (user_id);
CREATE INDEX IF NOT EXISTS ix_threads_last_activity_at ON threads (last_activity_at);
```

**Column notes:**
- `id` is used as the `thread_id` in LangGraph checkpointer config (`{"configurable": {"thread_id": str(id)}}`)
- `user_id` is `TEXT` (not UUID) — Saleor uses base64 GraphQL Node ID (e.g., `VXNlcjoxMjM=`)
- `title = NULL` + `title_generated = false`: not yet attempted or retries pending
- `title = "..."` + `title_generated = true`: finalized (LLM success or fallback truncation)
- `title_generation_attempts`: `SMALLINT`; incremented after each failed attempt; reset not needed after success

**Thread status transitions:**

| Status | Meaning |
|---|---|
| `idle` | Thread ready to accept a new run |
| `busy` | An active run is currently processing |
| `deleting` | Cleanup enqueued; no new runs accepted (returns 404/410) |

**Table: `generated_images`**

```sql
CREATE TABLE IF NOT EXISTS generated_images (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id          UUID        NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
    user_id            TEXT        NOT NULL,
    prompt             TEXT        NOT NULL,  -- image generation prompt
    s3_key             TEXT        NOT NULL,  -- images/{user_id}/{thread_id}/{timestamp}.png
    s3_url             TEXT        NOT NULL,  -- public S3 URL (permanent until object deleted)
    model              TEXT        NOT NULL,  -- model used (e.g. dall-e-3)
    request_message_id TEXT,                  -- soft ref: HumanMessage.id of the requesting turn
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_generated_images_thread_id
    ON generated_images (thread_id);
CREATE INDEX IF NOT EXISTS ix_generated_images_user_id_date
    ON generated_images (user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_generated_images_request_message_id
    ON generated_images (request_message_id);
```

**Column notes:**
- `prompt`: full prompt string sent to the image model; `NOT NULL` (always recorded)
- `model`: name of the image model used (e.g., `dall-e-3`); allows future model attribution
- `request_message_id`: `TEXT` (nullable), soft reference (no FK) — stores the `HumanMessage.id`
  of the turn that triggered image generation; nullable because it may not always be available
- History API maps images to AIMessage turns: `image_map[human_msg.id]` → attached to the
  subsequent AIMessage in the `(HumanMessage, AIMessage)` pair
- `ON DELETE CASCADE` from `threads` — deleting a thread record deletes image records automatically
- `ix_generated_images_user_id_date`: composite index on `(user_id, created_at)` to support
  per-user image history queries ordered by date

### 4.3 Qdrant Collection Schema (`products`)

```python
# Qdrant point payload (matches ProductItem / ProductPayload models)
{
    "product_id":    "string",         # Saleor product ID (base64 GraphQL Node ID)
    "name":          "string",         # product display name
    "description":   "string",         # plain text, truncated to DESCRIPTION_MAX_CHARS
    "slug":          "string",         # Saleor product slug
    "available":     bool,             # isAvailable from Saleor
    "price_min":     float,            # min price in `currency`
    "price_max":     float,            # max price in `currency`
    "currency":      "string",         # ISO 4217 (e.g. "VND", "USD")
    "price_range":   "string",         # display string (e.g. "100,000 - 300,000 VND")
    "collections":   ["string"],       # Saleor collection slugs (replaces legacy `tags`)
    "thumbnail_url": "string",         # product thumbnail for SSE products event
    "saleor_url":    "string",         # direct product page URL
}
```

**Vector configuration** (matches `qdrant_service.py`):

- Dense vector name: `text-dense` (size = `EMBEDDING_DIMS` default 1536, COSINE, HNSW m=16, ef_construct=100).  The name `text-dense` aligns with the LlamaIndex `QdrantVectorStore` default when `enable_hybrid=True`.
- Sparse vector name: `text-sparse` (BM25 via Qdrant's built-in FastEmbed, `on_disk=False`).
- Metadata filterable fields: `available` (bool, hard filter `available == true`) and `price_max` (float, LTE filter when a budget is provided).
- `ensure_collection` is drop-and-recreate tolerant: a collection with mismatched `vectors_config` / `sparse_vectors_config` (e.g. legacy `dense` / `sparse` names) is recreated.  Safe before the first reindex run.

---

## 5. API Contract Summary

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/threads` | Bearer JWT | Create a new conversation thread |
| `GET` | `/api/v1/threads` | Bearer JWT | List user's threads (cursor-based pagination) |
| `GET` | `/api/v1/threads/{thread_id}` | Bearer JWT | Get thread metadata and status |
| `GET` | `/api/v1/threads/{thread_id}/history` | Bearer JWT | Get paginated message history |
| `DELETE` | `/api/v1/threads/{thread_id}` | Bearer JWT | Delete thread (async, 202 Accepted) |
| `POST` | `/api/v1/threads/{thread_id}/runs/stream` | Bearer JWT | Send message; receive SSE stream |
| `GET` | `/api/v1/users/{user_id}/profile` | Bearer JWT (admin) | Get customer profile |
| `POST` | `/api/v1/admin/reindex` | Bearer JWT (admin) | Trigger full product reindex (async) |
| `GET` | `/api/v1/admin/threads` | Bearer JWT (admin) | List all threads (admin view) |
| `POST` | `/webhooks/saleor` | HMAC-SHA256 | Receive Saleor product lifecycle events |
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/ready` | None | Readiness probe |
| `GET` | `/metrics` | None | Prometheus metrics |

**HTTP Status Codes:**

| Code | Meaning |
|---|---|
| `200 OK` | GET success |
| `201 Created` | POST created resource successfully |
| `202 Accepted` | Async task enqueued (reindex, DELETE thread) |
| `400 Bad Request` | Invalid request body |
| `401 Unauthorized` | JWT missing, expired, or invalid signature |
| `403 Forbidden` | Valid JWT but insufficient permissions |
| `404 Not Found` | Thread or resource not found |
| `409 Conflict` | Thread is busy (active run in progress) |
| `410 Gone` | Thread is being deleted |
| `429 Too Many Requests` | Rate limit exceeded |
| `500 Internal Server Error` | Unexpected server error |

---

## 6. Environment Variables

All configuration is loaded from environment variables via `pydantic-settings`. No values
are hard-coded in source code.

### 6.1 Core Infrastructure

| Variable | Description | Default | Required |
|---|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (async psycopg v3 DSN) | — | YES |
| `QDRANT_URL` | Qdrant instance URL | `http://qdrant:6333` | YES |
| `QDRANT_API_KEY` | Qdrant API key (if auth enabled) | — | conditional |
| `QDRANT_COLLECTION_NAME` | Qdrant collection name for products | `products` | YES |
| `VALKEY_URL` | Valkey (Redis-compatible) base URL (no DB index) | `redis://valkey:6379` | YES |
| `CELERY_BROKER_URL` | RabbitMQ AMQP URL for Celery | `amqp://guest:guest@rabbitmq:5672//` | YES |
| `CELERY_RESULT_BACKEND` | Valkey DB 2 (Celery result backend) | `redis://valkey:6379/2` | YES |

### 6.2 External API Credentials

| Variable | Description | Default | Required |
|---|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | — | YES |
| `TAVILY_API_KEY` | Tavily Search API key | — | YES |
| `SALEOR_URL` | Saleor GraphQL API base URL | — | YES |
| `SALEOR_APP_TOKEN` | Saleor app token for GraphQL auth | — | YES |
| `SALEOR_WEBHOOK_SECRET` | HMAC-SHA256 secret for webhook validation | — | YES |

### 6.3 AWS S3

| Variable | Description | Default | Required |
|---|---|---|---|
| `AWS_S3_BUCKET` | S3 bucket name for generated images | — | YES |
| `AWS_ACCESS_KEY_ID` | AWS access key | — | YES |
| `AWS_SECRET_ACCESS_KEY` | AWS secret access key | — | YES |
| `AWS_REGION` | AWS region (e.g., `ap-southeast-1`) | — | YES |

### 6.4 LLM & Embedding Models

| Variable | Description | Default | Required |
|---|---|---|---|
| `RESPONSE_MODEL` | LLM for the Response Generator (heavier model) | `gpt-5.4` | YES |
| `ORCHESTRATOR_MODEL` | LLM for Orchestrator + Profiler + prepare_query + trend scout | `gpt-5.4-mini` | YES |
| `TITLE_MODEL` | LLM for TitleGenerationNode (cheapest model) | `gpt-5.4-nano` | YES |
| `SUMMARIZE_MODEL` | LLM for SummarizeNode + Profiler merge | `gpt-5.4-mini` | YES |
| `RERANK_MODEL` | LLM for Product RAG `llm_postprocess_node` reranking | `gpt-5.4-mini` | YES |
| `EMBEDDING_MODEL` | OpenAI embedding model name | `text-embedding-3-small` | YES |
| `EMBEDDING_DIMS` | Embedding vector dimensions | `1536` | YES |

### 6.5 Agent Behavior

| Variable | Description | Default | Required |
|---|---|---|---|
| `MAX_AGENT_STEPS` | LangGraph `recursion_limit` — hard ceiling on agent iterations | `10` | YES |
| `AGENT_FALLBACK_THRESHOLD` | Remaining steps at which Orchestrator forces `fallback` intent | `2` | YES |
| `IMAGE_DAILY_LIMIT` | Max generated images per user per day | `10` | YES |
| `MESSAGE_SUMMARIZE_THRESHOLD` | When `len(messages) > THRESHOLD`, `SummarizeNode` collapses the oldest messages | `12` | YES |
| `MESSAGE_SUMMARIZE_COUNT` | Number of oldest messages removed and replaced with a summary per pass | `8` | YES |

### 6.6 Thread Auto-Naming

| Variable | Description | Default | Required |
|---|---|---|---|
| `TITLE_GENERATION_MAX_ATTEMPTS` | Max LLM retry attempts for title generation across runs | `3` | YES |
| `TITLE_TRUNCATION_LENGTH` | Character length for fallback title truncation | `50` | YES |
| `SALEOR_STOREFRONT_URL` | Base URL used to build `saleor_url` in the Qdrant payload (e.g. `https://shop.example.com`) | — | conditional |

### 6.7 Rate Limiting

| Variable | Description | Default | Required |
|---|---|---|---|
| `RATE_LIMIT_CHAT` | Rate limit for `POST /runs/stream` per user | `20/minute` | YES |
| `RATE_LIMIT_THREAD_CREATE` | Rate limit for `POST /threads` per user | `10/minute` | YES |
| `RATE_LIMIT_READ` | Rate limit for GET endpoints (threads list, thread detail, history) | `60/minute` | YES |
| `RATE_LIMIT_WRITE` | Rate limit for DELETE operations | `10/minute` | YES |
| `RATE_LIMIT_REINDEX` | Rate limit for `POST /admin/reindex` | `2/hour` | YES |

### 6.8 Caching

| Variable | Description | Default | Required |
|---|---|---|---|
| `THREAD_LIST_CACHE_TTL` | TTL in seconds for `GET /threads` response cache | `120` | YES |

### 6.9 Observability

| Variable | Description | Default | Required |
|---|---|---|---|
| `LANGSMITH_TRACING` | Enable LangSmith tracing (`true`/`false`) | `false` | YES (prod) |
| `LANGSMITH_API_KEY` | LangSmith API key | — | conditional |
| `LANGSMITH_PROJECT` | LangSmith project name for trace grouping | `agentic-rag-ecommerce` | conditional |
| `LANGSMITH_ENDPOINT` | LangSmith API endpoint URL (region-aware: `api.smith.langchain.com`, `eu.api.smith.langchain.com`, `apac.api.smith.langchain.com`, `aws.api.smith.langchain.com`) | `https://aws.api.smith.langchain.com` | conditional |
| `LOG_LEVEL` | Structlog log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) | `INFO` | YES |

### 6.10 Qdrant Search Top-K (Product RAG)

| Variable | Description | Default | Required |
|---|---|---|---|
| `QDRANT_SPARSE_TOP_K` | Top-k results returned by the sparse (BM25) leg of hybrid search | `12` | YES |
| `QDRANT_SIMILARITY_TOP_K` | Top-k results returned by the dense leg of hybrid search | `12` | YES |
| `QDRANT_HYBRID_TOP_K` | Top-k RRF-fused candidates fed to the reranker stage | `9` | YES |
| `QDRANT_RERANK_TOP_K` | Top-k final results returned by the reranker to the agent | `3` | YES |

### 6.11 Ingestion

| Variable | Description | Default | Required |
|---|---|---|---|
| `DESCRIPTION_MAX_CHARS` | Max characters of the product description stored in the Qdrant payload | `500` | YES |

---

## 7. Assumptions & Dependencies

### Assumptions
1. The Saleor instance is the authoritative source of product data; no product data is
   managed directly in Qdrant outside of the sync pipeline.
2. Customer `user_id` is the Saleor base64 GraphQL Node ID extracted from JWT claims;
   the system does not issue its own user IDs.
3. The initial Qdrant collection is populated via the admin reindex endpoint before the
   chat feature is used for the first time.
4. The system runs behind a load balancer or reverse proxy; `/metrics` does not require
   authentication (adjust for production network controls).
5. Conversation message sequences follow a strict alternating pattern: `HumanMessage` →
   `AIMessage` → `HumanMessage` → ...; the History API relies on this for turn-based
   grouping and image attachment.

### External Dependencies

| Dependency | Version | Required For |
|---|---|---|
| LangGraph | latest stable | Agent orchestration, checkpointer, store |
| LlamaIndex | latest stable | Product RAG pipeline (retrieval, reranking) |
| FastAPI | latest stable | REST API + SSE streaming |
| OpenAI API | gpt-*, text-embedding-3-*, dall-e-* | LLM, embeddings, image generation |
| Saleor | >= 3.x (GraphQL API + Webhooks) | Product catalog source |
| Qdrant | latest stable | Vector search |
| PostgreSQL | 16 | Checkpointer, Store, custom tables |
| Valkey | latest stable (Redis-compatible) | Rate limiting, response caching |
| Celery | latest stable | Async task processing |
| RabbitMQ | latest stable | Celery message broker |
| AWS S3 | — | Generated image storage |
| Tavily API | v1 | Web search for trend data |
| LangSmith | Cloud-hosted | LLM tracing and observability |
| Prometheus + Grafana + Loki + Promtail | latest stable | Metrics, dashboards, log aggregation |
| `slowapi` | latest stable | FastAPI rate limiting |
| `fastapi-cache2` | latest stable | Response caching |
| `openinference-instrumentation-llama-index` | latest stable | LlamaIndex → OTel → LangSmith bridge |
| `prometheus-fastapi-instrumentator` | latest stable | FastAPI auto-metrics |
| `structlog` | latest stable | Structured JSON logging |
| `PyJWT` + `cryptography` | latest stable | JWT verification (JWKS/RS256) |
