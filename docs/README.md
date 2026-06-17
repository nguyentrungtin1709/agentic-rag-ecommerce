# Documentation — Agentic RAG Ecommerce

This directory holds all project documentation.  Each document has one
role; the contents do not duplicate across files.

## Top-level plan

| Document | Role |
|---|---|
| [analysis/05-IMPLEMENTATION-PLAN.md](analysis/05-IMPLEMENTATION-PLAN.md) | **Master plan.** Phases 1–14, audit, tasks, tests, Definition of Done, cross-cutting concerns.  The source of truth for "what phase are we in" and "what is left to build." |

## Analysis

| Document | Role |
|---|---|
| [analysis/00-SALEOR-APP-WEBHOOK-SETUP.md](analysis/00-SALEOR-APP-WEBHOOK-SETUP.md) | **Saleor App + Webhook setup.** Saleor dashboard steps to create the App, generate the token, register the `product_created` / `product_updated` / `product_deleted` webhooks, and verify delivery. |
| [analysis/01-USE-CASE-ANALYSIS.md](analysis/01-USE-CASE-ANALYSIS.md) | **Use cases** (actors A-01..A-12, UC-001..UC-011, UC-S01..UC-S05).  Focus on WHAT the system does for each user. |
| [analysis/02-REQUIREMENTS-SPECIFICATION.md](analysis/02-REQUIREMENTS-SPECIFICATION.md) | **Requirements** (FR-001..FR-113, NFR-001..NFR-030, SC-001..SC-011).  System constraints, database schema, API contract, environment variable registry. |
| [analysis/03-PROJECT-SCAFFOLD.md](analysis/03-PROJECT-SCAFFOLD.md) | **Scaffold reference.** Directory structure, dependencies, Docker Compose, Alembic, pre-commit hooks, Qdrant + Saleor service details.  Describes what was built, not what to build. |
| [analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md) | **Architecture design** (DRAFT 0.6).  Graph topology, node designs, `AgentState` schema, RAG pipeline, env-var conventions.  The source of truth for "how the agent is wired." |
| [analysis/06-OBSERVABILITY-DESIGN.md](analysis/06-OBSERVABILITY-DESIGN.md) | **Observability design.**  Four pillars (AI traces, logs, metrics, dashboards) + the Grafana Alloy collector.  Component inventory, communication flows, data inventory, cross-cutting concerns.  The source of truth for "how is the system observed." |
| [analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md](analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md) | **Observability implementation plan.**  Locked decisions D1–D10, four-phase rollout (LangSmith tracing, log pipeline, metrics expansion, dashboards), per-phase tasks, tests, Definition of Done.  The source of truth for "how do we ship the observability stack." |

## Diagrams

| Diagram | Purpose |
|---|---|
| [diagrams/01-use-case-overview.mermaid](diagrams/01-use-case-overview.mermaid) | Use case catalog (actors + relationships). |
| [diagrams/02-system-context.mermaid](diagrams/02-system-context.mermaid) | C4 Level 1 — system + external dependencies. |
| [diagrams/03-customer-chat-sequence.mermaid](diagrams/03-customer-chat-sequence.mermaid) | Customer chat run — auth, profiler, orchestrator, sub-agents, response generation, image generation. |
| [diagrams/04-webhook-sync-sequence.mermaid](diagrams/04-webhook-sync-sequence.mermaid) | Saleor → webhook → Celery → Qdrant flow. |
| [diagrams/05-agent-workflow.mermaid](diagrams/05-agent-workflow.mermaid) | LangGraph parent graph + sub-agent internals. |

## How the documents relate

```
analysis/05-IMPLEMENTATION-PLAN.md  (master plan, phases, audit)
        |
        +-- references --> analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md
        |                       (graph topology, node designs)
        |
        +-- references --> analysis/06-OBSERVABILITY-DESIGN.md
        |                       (observability architecture)
        |                           |
        |                           +-- rollout --> analysis/07-OBSERVABILITY-IMPLEMENTATION-PLAN.md
        |                                                       (4-phase delivery of the observability stack)
        |
        +-- references --> analysis/01-USE-CASE-ANALYSIS.md
        |                       (use cases driving acceptance criteria)
        |
        +-- references --> analysis/02-REQUIREMENTS-SPECIFICATION.md
        |                       (FRs / NFRs that phases must satisfy)
        |
        +-- references --> analysis/03-PROJECT-SCAFFOLD.md
        |                       (scaffold the phases build on)
        |
        +-- references --> analysis/00-SALEOR-APP-WEBHOOK-SETUP.md
        |                       (Saleor App + webhook registration)
        |
        +-- references --> diagrams/*.mermaid
                                (visual summaries)
```

## Reading order for a new contributor

1. [analysis/05-IMPLEMENTATION-PLAN.md](analysis/05-IMPLEMENTATION-PLAN.md) — what phases
   exist and where the project stands.
2. [analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md](analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md) — how the agent is wired.
3. [analysis/01-USE-CASE-ANALYSIS.md](analysis/01-USE-CASE-ANALYSIS.md) — who the
   system serves and what flows are supported.
4. [analysis/02-REQUIREMENTS-SPECIFICATION.md](analysis/02-REQUIREMENTS-SPECIFICATION.md) — concrete
   requirements and the env-var registry.
5. [analysis/03-PROJECT-SCAFFOLD.md](analysis/03-PROJECT-SCAFFOLD.md) — repository
   layout, dependencies, Docker Compose.
6. [analysis/06-OBSERVABILITY-DESIGN.md](analysis/06-OBSERVABILITY-DESIGN.md) — how
   the system is observed (after the core product is familiar).
