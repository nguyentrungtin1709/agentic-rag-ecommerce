# Use Case Analysis — Agentic RAG Ecommerce

**Project**: `agentic-rag-ecommerce` — AI POD Stylist & Recommendation System
- **Version**: 1.0
- **Date**: 2026-05-31
- **Status**: Confirmed — Based on Phase 1 & 2 review (TEMP.md, all 22 items confirmed)

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
| A-12 | LangSmith | External System | LLM/agent observability; receives LangGraph traces and LlamaIndex spans via OpenInference OTel bridge |

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

**Description**
The customer sends a natural language message expressing product needs (style preference,
occasion, recipient, budget, product type) within an existing thread. The system analyzes
context, performs hybrid search on the product catalog, and streams a personalized ranked
recommendation list. If `generate_image: true` is set and trigger conditions are met, the
system also generates a design image via DALL-E.

**Preconditions**
1. Customer has a valid Saleor JWT (Bearer token).
2. A thread has been explicitly created via `POST /api/v1/threads`.
3. Thread is in `idle` status (not `busy` or `deleting`).
4. Qdrant contains indexed product vectors (at least one full reindex completed).

**Main Flow**
1. Customer sends `POST /api/v1/threads/{thread_id}/runs/stream` with
   `Authorization: Bearer {saleor_jwt}` and body `{message, generate_image: bool}`.
2. System verifies JWT signature via cached JWKS (UC-S01); extracts `user_id` and `is_staff`.
3. System checks thread status — if `busy` returns 409 Conflict; if `deleting` returns 404.
4. System sets thread to `busy` status and updates `last_activity_at`.
5. System loads `AgentState` from LangGraph checkpointer (UC-S02).
6. **TitleGenerationNode** (first run only, parallel branch):
   - Checks `thread.title_generated == false`.
   - Calls LLM (`TITLE_MODEL`) with `first_user_message` to generate a short title (max 6 words).
   - Retries up to `TITLE_GENERATION_MAX_ATTEMPTS` times on failure.
   - On final failure: truncates `first_user_message` to `TITLE_TRUNCATION_LENGTH` as fallback.
   - Persists title to `threads` table, sets `title_generated = true`.
   - Emits `thread_title` SSE event immediately (does not wait for Response Generator).
7. **Profiler Node**: calls OpenAI with `{current_profile_json, latest_user_message}`;
   extracts updated style attributes; writes updated profile to LangGraph Store (UC-S03).
8. **Orchestrator Node**: reads `config["remaining_steps"]`; if
   `remaining_steps <= AGENT_FALLBACK_THRESHOLD` forces intent to `fallback`; otherwise
   classifies intent: `sufficient / clarification_needed / out_of_scope / fallback`.
9. **Product RAG Node** (if intent requires product search):
   - Formulates optimized English search query from profile + message.
   - Executes hybrid Qdrant search (dense semantic + sparse BM25 + metadata filter).
   - Stores top-k results in `AgentState.retrieved_products`.
   - Routes back to Orchestrator for re-evaluation.
10. Orchestrator re-evaluates; routes to Response Generator when intent is `sufficient`.
11. **Response Generator Node**: synthesizes profile + retrieved products into a personalized
    response; streams via SSE (`token` events for text, `products` event for product cards).
12. **Image Generation Node** (parallel, if `generate_image: true` and conditions met):
    - Synthesizes image prompt from trend summary and/or user description.
    - Calls OpenAI DALL-E API to generate image.
    - Uploads image to AWS S3 (`images/{user_id}/{thread_id}/{timestamp}.png`).
    - Inserts record into `generated_images` (`request_message_id = HumanMessage.id`).
    - Emits `image_ready` SSE event with public S3 URL.
13. LangGraph checkpointer auto-saves `AgentState` including all messages (UC-S05).
14. Thread status set back to `idle`.
15. `done` SSE event emitted with `{run_id, thread_id, intent, usage: {prompt_tokens, completion_tokens, cost_usd}}`.

**Alternative Flows**
- **9a** — No matching products found: Orchestrator routes to Response Generator with
  "no-results" context.
- **8a** — Intent `clarification_needed`: Response Generator asks a clarifying question.
- **8b** — Intent `out_of_scope`: Response Generator declines politely (not POD-related).
- **8c** — Intent `fallback`: Response Generator produces best-effort response from all
  data collected so far in `AgentState`, acknowledges results may be incomplete.
- **12a** — Image rate limit exceeded (`IMAGE_DAILY_LIMIT`): emits `image_failed` with
  `reason: "rate_limit_exceeded"`; text response is not affected.
- **12b** — Image generation API error: emits `image_failed` with `reason: "generation_failed"`.

**Exception Flows**
- **E1** — Qdrant unavailable: system returns `error` SSE event; logs error.
- **E2** — OpenAI timeout: retries up to 3 times with exponential backoff; returns `error` SSE
  event on final failure.
- **E3** — Thread not found or belongs to another user: 404 Not Found / 403 Forbidden.

**Postconditions**
- `AgentState` with full message history persisted in LangGraph checkpointer.
- Customer profile updated in LangGraph Store.
- Thread `last_activity_at` updated; thread title set (first run only).
- If image generated: S3 object uploaded, `generated_images` record inserted.
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
1. Same as UC-001 steps 1.
2. Same as UC-001 steps 2.
3. Same as UC-001 steps 3.
4. Same as UC-001 steps 4.
5. Same as UC-001 steps 5.
6. TitleGenerationNode (first run only, parallel) — same as UC-001 step 6.
7. **Profiler Node**: updates profile with detected trend topic interest.
8. **Orchestrator Node**: classifies intent as trend search.
9. **Trend Scout Node**: formulates optimized web search query; calls Tavily API
   (fallback: DuckDuckGo); summarizes top design themes; generates 3-5 text-to-image
   prompt suggestions; stores result in `AgentState.trend_summary`.
10. Orchestrator re-evaluates — `sufficient` — routes to Response Generator.
11. **Response Generator Node**: synthesizes trend summary + prompt suggestions into response.
12. Same as UC-001 steps 12.
13. Same as UC-001 steps 13.
14. Same as UC-001 steps 14.
15. Same as UC-001 steps 15.

**Alternative Flows**
- **9a** — Tavily unavailable: fall back to DuckDuckGo.
- **9b** — No relevant trend results found: Response Generator informs customer, suggests
  broader search terms.

**Postconditions**
- `AgentState.trend_summary` populated.
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
1. Same as UC-001 steps 1.
2. Same as UC-001 steps 2.
3. Same as UC-001 steps 3.
4. Same as UC-001 steps 4.
5. Same as UC-001 steps 5.
6. Same as UC-001 steps 6.
7. Same as UC-001 steps 7.
8. Same as UC-001 steps 8.
9. **Orchestrator Node**: classifies that both product search AND trend search are needed.
10. **Product RAG Node**: executes hybrid search; stores `retrieved_products`; returns to Orchestrator.
11. **Orchestrator Node**: re-evaluates — trend search still needed.
12. **Trend Scout Node**: executes web search; stores `trend_summary`; returns to Orchestrator.
13. **Orchestrator Node**: re-evaluates — `sufficient` — routes to Response Generator.
14. **Response Generator Node**: synthesizes products + design trends; pairs each product
    with matching design concepts.
15. Same as UC-001 steps 12.
16. Same as UC-001 steps 13.
17. Same as UC-001 steps 14.
18. Same as UC-001 steps 15.

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
1. Saleor sends `POST /webhooks/saleor` with `X-Saleor-Signature` header and JSON body
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
value in the `X-Saleor-Signature` header using a constant-time comparison to prevent timing
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
| Q5 | Is text-to-image prompt generation an MVP requirement? | Yes — `AgentState.trend_summary` includes prompt suggestions; image generation (DALL-E) is SHOULD priority |
| Q6 | What is the max agent iteration limit? | `MAX_AGENT_STEPS` env var maps to LangGraph `recursion_limit`; graceful fallback via `AGENT_FALLBACK_THRESHOLD` (not a hard error) |
