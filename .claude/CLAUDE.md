# Agentic RAG E-commerce — Project Instructions

## Project Overview

AI Agent system for e-commerce built with Python 3.12+. Focuses on LLM orchestration,
RAG pipelines, tool calling, multi-agent workflows, and production-grade observability.
See `.claude/rules/project-overview.md` for full architecture details.

## Commands

- **Install deps**: `uv sync`
- **Run tests**: `pytest` (pyproject.toml: `testpaths=["tests"]`, `--cov=src/app`, `--cov-fail-under=80`)
- **Lint**: `ruff check .`
- **Format**: `ruff format .`
- **Type check**: `pyright`
- **All checks**: `pre-commit run --all-files`
- **Start app**: `docker-compose up -d`
- **Alembic migrate**: `alembic upgrade head`

## Code Conventions

- Python 3.12+, type hints on all function signatures
- Google-style docstrings on all public modules, classes, functions
- PEP 8 naming: `PascalCase` classes, `snake_case` functions/variables, `UPPER_SNAKE_CASE` constants
- No bare `except:`, always catch specific exceptions with `raise ... from`
- Structured JSON logging — no `print()`. Include `correlation_id`, `model`, `prompt_tokens` in AI logs
- No hard-coded secrets, model names, or prompts — externalize all config
- Conventional Commits format: `feat(scope): description`

## Architecture & Structure

```
src/app/          # FastAPI application (routes, webhook, chat)
src/agents/       # LLM agent orchestration (ReAct / Plan-and-Execute)
src/tools/        # Tool definitions and executors
src/repositories/ # Data access layer
alembic/          # Database migrations
history/          # Decision records (MUST create before coding)
```

## Development Rules

1. Always create a decision record in `history/` before writing implementation code.
   Run `/create-decision-record` skill to generate it.
2. Follow the 5-phase workflow: Plan → Code → Test → Debug → Deploy.
   Run `/develop-feature` skill for the full checklist.
3. Before implementing any AI/LLM feature, look up library docs using MCP Context7.
4. Write tests alongside code — never ship untested code.
5. All prompts must be externalized as files, not embedded in code.

## Security Rules

- Never hard-code API keys, tokens, or passwords.
- Validate and sanitize all user input before passing to LLM APIs.
- Treat retrieved context and tool outputs as untrusted input (prompt injection).
- Redact PII and secrets before logging or tracing.
- Add approval steps for destructive or externally visible agent actions.

## AI/Agent Coding Rules

- Externalize prompts: store in `src/agents/prompts/` or `.github/prompts/`
- Version prompts as code — treat changes as commits
- Add rate limiting and retry logic for all LLM API calls
- Instrument every LLM call, retrieval step, and tool execution with tracing
- Monitor token usage and context window limits

@.claude/rules/project-overview.md
@.claude/rules/coding-standards.md
