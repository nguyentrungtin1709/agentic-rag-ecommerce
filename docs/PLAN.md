# Development Plan — Agentic RAG Ecommerce
## AI POD Stylist & Recommendation System

**Project**: `agentic-rag-ecommerce`
- **Updated**: 2026-06-01
- **Status**: In Progress — Phase 1 & 2 complete

> Detailed requirements and architecture decisions are captured in:
> - `docs/analysis/01-USE-CASE-ANALYSIS.md`
> - `docs/analysis/02-REQUIREMENTS-SPECIFICATION.md`
> - `docs/diagrams/` (5 Mermaid diagrams)
> - `history/` (decision records, one per feature/change)

---

## Phase 1 — Requirements & Use Case Analysis [DONE]

**Goal**: Define what the system does, who uses it, and under what constraints.

**Deliverables:**
- Actor catalog (12 actors: 2 human, 10 external systems)
- 11 primary use cases + 5 system use cases documented
- 4 customer personas defined
- Functional requirements (FR-001 to FR-113) covering all feature domains
- Non-functional requirements (performance, scalability, security, observability)
- System constraints (language, frameworks, infra)
- Full environment variable registry (Section 6 of requirements spec)

**Key decisions confirmed:**
- Thread-based API model (`POST /api/v1/threads/{thread_id}/runs/stream`) — not session-based
- Saleor JWT (RS256, JWKS-verified) for auth — no custom auth system
- 7 SSE event types: `token`, `products`, `image_ready`, `image_failed`, `thread_title`, `done`, `error`
- `request_message_id` (HumanMessage.id) as the soft link between `generated_images` and conversation history

---

## Phase 2 — System Architecture Design [DONE]

**Goal**: Define the full technical blueprint — agent graph, data model, API contract, and infrastructure.

**Deliverables:**
- LangGraph agent graph with 7 nodes: `TitleGenerationNode`, `ProfilerNode`, `OrchestratorNode`, `ProductRAGAgent`, `TrendScoutNode`, `ImageGenerationNode`, `ResponseGeneratorNode`
- `AgentState` schema: `messages`, `user_profile`, `retrieved_products`, `trend_summary`, `thread_title`, `correlation_id`
- PostgreSQL schema: LangGraph-managed tables + custom `threads` + `generated_images`
- Qdrant collection schema: hybrid dense + sparse BM25, payload includes `thumbnail_url`
- Full API contract (13 endpoints)
- Technology stack confirmed (see Phase 3)
- Docker Compose service topology (11 services)
- 5 architecture diagrams in `docs/diagrams/`

**Key decisions confirmed:**
- LangGraph `AsyncPostgresSaver` for short-term memory (AgentState + all messages per thread)
- LangGraph `AsyncPostgresStore` for long-term user profiles (namespace: `("profiles", user_id)`)
- No custom `user_profiles` table — profiles live in LangGraph Store
- Celery + RabbitMQ for async webhook processing and nightly cleanup (image gen is inline, not Celery)
- Orchestrator 4 intents: `sufficient`, `clarification_needed`, `out_of_scope`, `fallback`
- `fallback` triggered when `remaining_steps <= AGENT_FALLBACK_THRESHOLD`

---

## Phase 3 — Technology & Tooling Selection [DONE]

**Goal**: Finalize the full tech stack.

| Layer | Choice |
|---|---|
| Language / Runtime | Python 3.12 |
| Web Framework | FastAPI + SSE |
| Agent Orchestration | LangGraph (`AsyncPostgresSaver` + `AsyncPostgresStore`) |
| LLM Provider | OpenAI (model names via env vars: `RESPONSE_MODEL`, `ORCHESTRATOR_MODEL`, `TITLE_MODEL`) |
| Embedding | OpenAI `text-embedding-3-small` (default), configurable via `EMBEDDING_MODEL` |
| RAG Framework | LlamaIndex (inside LangGraph nodes) |
| Vector DB | Qdrant (hybrid dense + sparse BM25) |
| Relational DB | PostgreSQL 16 |
| Cache / Rate Limit | Valkey (DB `/0` rate limiting, DB `/1` response cache) |
| Async Tasks | Celery + RabbitMQ |
| Image Storage | AWS S3 |
| Web Search | Tavily (primary), DuckDuckGo (fallback) |
| Observability | LangSmith + OpenInference + Prometheus + Grafana + structlog + Loki |
| Dependency Mgmt | `uv` + `pyproject.toml` |
| Linting / Formatting | `ruff` |
| Type Checking | `pyright` |
| Testing | `pytest` + `pytest-asyncio` + `pytest-cov` |
| Containerization | Docker + Docker Compose |
| CI | GitHub Actions |

---

## Phase 4 — Project Scaffold & Infrastructure Setup

**Goal**: Initialize the repository structure, wire all external connections, and verify the dev environment works end-to-end.

**Tasks:**
- Initialize `pyproject.toml`, `uv.lock`, `.env.example`, pre-commit hooks (`ruff`, `pyright`)
- Set up `config.py` using `pydantic-settings` with all env vars from the registry
- Define `AgentState` and Pydantic request/response schemas
- Set up Docker Compose with all 11 services: `app`, `postgres`, `qdrant`, `valkey`, `rabbitmq`, `celery-worker`, `celery-beat`, `prometheus`, `grafana`, `loki`, `promtail`
- Implement health (`GET /health`) and readiness (`GET /ready`) endpoints
- Implement Saleor GraphQL client (product fetch + JWKS fetch for JWT verification)
- Implement Qdrant service (collection creation, upsert, delete, hybrid search)
- Write integration tests for all external connections

---

## Phase 5 — Product RAG Engine

**Goal**: Build and validate the product indexing and retrieval pipeline.

**Tasks:**
- Implement LlamaIndex pipeline: Saleor GraphQL → parse nodes → embed → Qdrant upsert
- Implement hybrid search (dense semantic + sparse BM25 + metadata filter)
- Implement `POST /api/v1/admin/reindex` endpoint (async, returns 202, delegates to Celery)
- Implement Celery `reindex_products` task
- Write unit and integration tests; evaluate retrieval quality on sample queries

---

## Phase 6 — Thread Management & Chat API

**Goal**: Implement the thread lifecycle and the core SSE streaming endpoint.

**Tasks:**
- Implement thread CRUD: `POST /threads`, `GET /threads`, `GET /threads/{id}`, `DELETE /threads/{id}`
- Implement thread status transitions (`idle` → `busy` → `idle` / `deleting`)
- Implement `POST /threads/{id}/runs/stream` with JWT verification and SSE streaming
- Implement `GET /threads/{id}/history` with cursor-based pagination (read from LangGraph checkpointer)
- Implement `GET /api/v1/users/{user_id}/profile` (admin only)
- Implement thread list cache (`fastapi-cache2` + Valkey DB `/1`, TTL = `THREAD_LIST_CACHE_TTL`)
- Implement rate limiting via `slowapi` + Valkey DB `/0`
- Write integration tests for all thread and chat endpoints

---

## Phase 7 — LangGraph Agent Pipeline

**Goal**: Implement all agent nodes and assemble the full LangGraph graph.

**Tasks:**
- Implement `ProfilerNode`: LLM-driven incremental profile merge using `{current_profile_json, latest_message}`; write to `AsyncPostgresStore`
- Implement `OrchestratorNode`: 4-intent classification (`sufficient`, `clarification_needed`, `out_of_scope`, `fallback`); `remaining_steps` guard
- Implement `ProductRAGAgent`: query rewrite to English + hybrid Qdrant search; store in `AgentState.retrieved_products`
- Implement `TrendScoutNode`: Tavily search + LLM summarization; store in `AgentState.trend_summary`
- Implement `ResponseGeneratorNode`: synthesize profile + products + trends; stream SSE (`token`, `products`, `done`, `error`)
- Implement `TitleGenerationNode`: parallel branch (first run only); retry logic; fallback truncation; `thread_title` SSE event
- Implement `ImageGenerationNode`: parallel branch; DALL-E call; S3 upload; `generated_images` insert with `request_message_id`; Valkey quota check; `image_ready`/`image_failed` SSE events
- Compile graph with `AsyncPostgresSaver` + `AsyncPostgresStore`; configure `MAX_AGENT_STEPS` as `recursion_limit`
- Externalize all LLM prompts to `prompts/` directory
- Write unit tests for each node in isolation (mocked LLM); agent-level routing evaluation tests

---

## Phase 8 — Saleor Webhook Sync

**Goal**: Implement real-time product catalog synchronization from Saleor.

**Tasks:**
- Implement `POST /webhooks/saleor` endpoint: HMAC-SHA256 validation (constant-time comparison); return 200 immediately
- Implement Celery `process_webhook` task: handle `PRODUCT_CREATED`, `PRODUCT_UPDATED` (embed + Qdrant upsert), `PRODUCT_DELETED` (Qdrant delete)
- Ensure idempotency: re-processing same event must not create duplicate vectors
- Write integration tests with valid and invalid HMAC signatures; test idempotency

---

## Phase 9 — Observability & Logging

**Goal**: Wire up full observability stack.

**Tasks:**
- Configure `structlog` JSON logging with standard fields (`timestamp`, `level`, `service`, `correlation_id`, `thread_id`, `user_id`, `node`)
- Enable LangSmith tracing via `LANGSMITH_TRACING=true`
- Wire LlamaIndex → OTel → LangSmith via `openinference-instrumentation-llama-index`
- Expose Prometheus metrics via `prometheus-fastapi-instrumentator`
- Configure Promtail → Loki log shipping
- Set up Grafana dashboards: API latency, error rate, LLM token/cost, log explorer
- Implement Celery Beat schedule: `cleanup_expired_threads` nightly at 2:00 AM (delete expired thread records, S3 objects, image records)

---

## Phase 10 — Security Review

**Goal**: Verify all security controls are in place before deployment.

**Checklist:**
- JWT verification uses local JWKS cache (RS256); no raw token logged
- Webhook HMAC uses constant-time comparison
- All user input validated via Pydantic at API boundaries
- User message content sanitized before LLM injection (prompt injection defense)
- LLM-generated content escaped before returning to client
- No secrets in source code, commit history, or logs
- `uv audit` passes with no known CVEs
- Admin endpoints enforce `is_staff: true` from JWT claims

---

## Phase 11 — Testing & Evaluation

**Goal**: Achieve coverage targets and validate agent quality.

**Tasks:**
- Unit tests: all agent nodes, tools, repositories — mocked LLM and external APIs (coverage >= 80%)
- Integration tests: Qdrant pipeline, PostgreSQL CRUD, Saleor client, webhook endpoint, all API endpoints
- Agent evaluation (`tests/evaluation/`): routing accuracy, RAG retrieval quality on sample queries, prompt regression baseline
- End-to-end scenarios: 5 representative customer journeys against a seeded local environment

---

## Phase 12 — CI/CD & Deployment

**Goal**: Automate build, test, and deployment.

**Tasks:**
- GitHub Actions CI: lint (`ruff`) → type check (`pyright`) → unit tests → integration tests → Docker build
- Multi-stage Dockerfile: builder (install deps) + runtime (minimal image)
- Push image to GHCR on `main` merge
- `docker-compose.yml` for single-server production deployment
- Secrets via `.env` on host (not committed); document in `.env.example`

---

## Phase 13 — Monitoring & Iteration

**Goal**: Operate the system and continuously improve agent quality.

**Tasks:**
- Monitor LangSmith traces for underperforming agent steps (high latency, wrong routing, poor retrieval)
- Update prompt templates in `prompts/` based on trace evidence
- Run prompt regression tests before merging prompt changes
- Monitor Grafana dashboards: API latency p95, error rate, LLM cost per thread
- Curate evaluation dataset from production traces for ongoing quality measurement

---

## Progress Tracker

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Requirements & Use Case Analysis | DONE |
| Phase 2 | System Architecture Design | DONE |
| Phase 3 | Technology & Tooling Selection | DONE |
| Phase 4 | Project Scaffold & Infrastructure Setup | DONE |
| Phase 5 | Product RAG Engine | Not Started |
| Phase 6 | Thread Management & Chat API | Not Started |
| Phase 7 | LangGraph Agent Pipeline | Not Started |
| Phase 8 | Saleor Webhook Sync | Not Started |
| Phase 9 | Observability & Logging | Not Started |
| Phase 10 | Security Review | Not Started |
| Phase 11 | Testing & Evaluation | Not Started |
| Phase 12 | CI/CD & Deployment | Not Started |
| Phase 13 | Monitoring & Iteration | Not Started |
