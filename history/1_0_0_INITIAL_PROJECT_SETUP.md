# Initial Project Setup

**Version**: 1.0.0
**Date**: 2026-06-02
**Status**: Completed

## What

Full project scaffold for the `agentic-rag-ecommerce` service — an AI POD Stylist
and Recommendation System built on FastAPI, LangGraph, LlamaIndex, and Qdrant.
Establishes all module boundaries, dependency tree, Docker Compose stack,
configuration system, and integration test suite.

## Why

Phase 4 deliverable: create a runnable foundation that passes `/health` and `/ready`,
connects to all 11 infrastructure services, and has an empty-but-compilable LangGraph
graph.  No agent logic is implemented yet.

## How

- **FastAPI** app with lifespan manager wiring DB pools, LangGraph, Qdrant, Valkey.
- **`pydantic-settings`** `Settings` class loading all env vars from `.env`.
- **`AsyncPostgresSaver` + `AsyncPostgresStore`** for LangGraph short/long-term memory.
- **Docker Compose** stack with 11 services (see Section 4 of Phase 4 scaffold doc).
- **Alembic** migrations for custom tables (`threads`, `generated_images`).
- **Pre-commit hooks**: `ruff` (lint + format) + `pyright` (type check).

## Key Decisions

- **Two DB drivers**: `psycopg` (v3) required by LangGraph checkpointer;
  `asyncpg` used for custom repository queries where raw performance matters.
- **Schemas split**: `schemas/api.py` → `common.py`, `thread.py`, `chat.py`,
  `webhook.py`; `api.py` retained as backward-compat re-export shim.
- **Node naming**: Final file names follow actual implementation
  (`orchestrate.py`, `synthesize.py`, `generate_title.py`, `generate_image.py`)
  rather than the illustrative names in the Phase 4 scaffold doc.
- **Primary key column**: Custom tables use `id UUID` (not `thread_id`/`image_id`)
  for consistency with LangGraph conventions; scaffold doc is outdated.
- **11 services**: docker-compose.yml runs 11 services
  (app, celery-worker, celery-beat, postgres, qdrant, valkey, rabbitmq,
  prometheus, grafana, loki, promtail); README/PLAN docs updated accordingly.
- **Intentionally deferred**:
  - `agent/prompts/` — externalized prompt templates deferred to Phase 7.
  - `docker-compose.override.yml` — not created; dev team uses `.env` overrides.

## Impact

All source modules created as stubs; integration tests for Postgres, Qdrant,
Valkey, Saleor client, and health endpoints are in `tests/integration/`.
No breaking changes — first commit.
