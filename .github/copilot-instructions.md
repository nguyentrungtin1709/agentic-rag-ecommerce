# GitHub Copilot Instructions

You are an expert AI programming assistant. When creating or refactoring code for this project,
follow the rules below. They apply to all programming languages used in this project.

---

## 0. Communication Rules (MANDATORY)

### 0.1. No Emoji or Decorative Unicode Characters

**ABSOLUTELY FORBIDDEN** — Do not use emoji or decorative Unicode symbols anywhere, including:
- Responses, explanations, and summaries
- Code comments and docstrings
- Log messages and `print()` output in code
- Markdown content (README, history records, documentation)

This includes, but is not limited to: ✅ ❌ ⚠️ 🚀 💡 🔥 ✨ 📌 🎯 and all similar characters.

Use plain ASCII text alternatives instead:
- Instead of ✅ / ❌ → use `[OK]` / `[FAIL]` or `YES` / `NO`
- Instead of ⚠️ → use `[WARN]` or `WARNING:`
- Instead of 🔥 / 🚀 → use plain descriptive words

This rule is **non-negotiable** and takes priority over all other formatting preferences.

### 0.2. Language

- **Responses**: Always write in **English** unless the user explicitly requests otherwise.
- **Source code**: Always write in **English** — variable names, function names, comments,
  docstrings, log messages, and commit messages.

---

## 1. Core Standards — Rules Reference

All detailed coding standards live in `.github/rules/`. Read the relevant file before starting work:

| Topic | Rules File |
|-------|-----------|
| Project overview, goals, directory structure | `.github/rules/project-overview.md` |
| Naming, type hints, docstrings, comments, prompt hygiene | `.github/rules/coding-standards.md` |
| Logging, error handling, formatting, linting, observability | `.github/rules/code-quality.md` |
| SOLID principles, AI agent design strategy | `.github/rules/design-principles.md` |
| Secrets, input validation, LLM/agent security | `.github/rules/security.md` |
| AAA pattern, test naming, coverage, mocking | `.github/rules/testing.md` |
| Conventional commits format and best practices | `.github/rules/git-conventions.md` |
| Prompt management, agent config, token/cost tracking | `.github/rules/agent-config.md` |
| Response style, no-emoji, ask-first policy | `.github/rules/communication.md` |
| Terminal command classification and safety | `.github/rules/command-execution.md` |

---

## 2. Conflict Resolution & Clarification (CRITICAL — HIGHEST PRIORITY)

> These rules MUST be applied before taking any action whenever a requirement is ambiguous
> or conflicts with existing knowledge. This section takes priority over all other sections.

### 2.1. Ask First — Never Assume or Silently Substitute (MANDATORY)

**Situation A — Conflict with existing knowledge:**
- **MUST**: Stop and ask the user to confirm before proceeding.
- **MUST**: State clearly what conflicts, what the user specified, and what alternative you are considering.
- **FORBIDDEN**: Silently replace the user's specified value with a different one.

```
# WRONG: User specifies gpt-5-nano → you silently use gpt-4o-nano.

# CORRECT: "I do not find gpt-5-nano in the model list I know. Can you confirm the name?
#  If confirmed, I will use it as-is. Otherwise, should I fall back to gpt-4o-mini?"
```

**Situation B — Ambiguous or unclear requirements:**
- **MUST**: Ask for clarification before writing any code.
- **MUST**: Point out the specific unclear part and explain why it matters.
- Exception: For trivial ambiguities, proceed with the most sensible default and state your assumption.

Examples requiring clarification: "save the output" (no format/path), "use the latest model" (no
provider), "log errors" (no destination), "reuse the existing structure" (ambiguous reference).

### 2.2. Verify Before Concluding (MANDATORY)

When uncertain about technical facts (model names, API endpoints, package versions):
- **MUST**: Use MCP `search` or `fetch_content` to look up information from authoritative sources.
- Priority: Official docs > Official GitHub repo > Reputable tech sources > General search.
- State the source before proceeding. If MCP is unavailable, ask the user to confirm manually.

---

## 3. Development Workflow

Follow the 5-phase workflow defined in `.github/workflows/develop-feature.md`:

1. **Planning & Design** — Define goals, choose architecture (ReAct / Plan-and-Execute / Multi-Agent),
   design tools, define evaluation criteria, externalize prompts, create a decision record.
2. **Core Development** — Start simple, build modular components, write tests alongside code,
   version prompts, commit frequently.
3. **Testing & Evaluation** — Unit/integration/agent-level tests, prompt regression testing,
   safety testing (prompt injection, malformed responses), cost/latency profiling.
4. **Debugging & Observability** — Enable tracing, inspect tool calls, log structured data,
   iterate on prompts with trace evidence.
5. **Deployment & Continuous Improvement** — Self-review, monitor in production, curate eval
   datasets, iterate: update → re-evaluate → redeploy.

### 3.1. Decision History Records (MANDATORY)

**Always create a history record before writing any implementation code.**

- Directory: `history/` at project root
- Naming: `{MAJOR}_{MINOR}_{PATCH}_{SHORT_DESCRIPTION}.md`, e.g. `1_1_0_ADD_WEB_SEARCH_TOOL.md`
- Version starts at `1_0_0` and increments sequentially
- MAJOR: breaking changes or major architecture shifts; MINOR: new features; PATCH: fixes and small improvements

Required template:

```markdown
# {Short Feature Title}

**Version**: {MAJOR}.{MINOR}.{PATCH}
**Date**: {YYYY-MM-DD}
**Status**: Planned | In Progress | Completed

## What
Brief description of the feature or change (1–3 sentences).

## Why
The problem this solves or the value it provides.

## How
High-level implementation approach — architecture pattern, key components, tools/libraries used.

## Key Decisions
- Decision 1: {What was decided} — {Why this option over alternatives}

## Impact
Which modules/files are affected. Any breaking changes or migration steps.
```

Rules: one file per feature/change; keep descriptions concise; update Status as work progresses.
See `.github/workflows/create-decision-record.md` for the full workflow.

---

## 4. Code Review Checklist

See `.github/skills/code-review/SKILL.md` for the full checklist. Quick summary:

- [ ] SOLID principles followed
- [ ] PEP 8 naming conventions
- [ ] Type hints on all function signatures
- [ ] Docstrings on all public modules, classes, functions
- [ ] Unit tests (AAA pattern, names following `test_<fn>_<scenario>_<expected>`)
- [ ] Error handling (specific exceptions, no bare `except`, `raise ... from` for chaining)
- [ ] Structured logging (no `print()`, include `correlation_id`, `model`, `prompt_tokens`)
- [ ] No hard-coded config, secrets, model names, or prompts
- [ ] No code duplication
- [ ] `ruff` formatted, all linting errors resolved
- [ ] Dependencies pinned with exact versions
- [ ] Rate limiting and retry logic for LLM API calls
- [ ] Token usage monitored and context window limits respected
- [ ] Prompt injection defenses in place for user-facing agents
- [ ] Commit messages follow Conventional Commits

---

## 5. Using MCP (Model Context Protocol) Tools

### 5.1. Context7 — API Documentation Lookup (REQUIRED)

During development, **MUST** use MCP Context7 to look up API information for libraries/frameworks:

1. Use `resolve-library-id` to find the Context7-compatible library ID.
2. Use `get-library-docs` to retrieve accurate documentation.

Select **stable** versions only (not alpha, beta, canary). Always verify with Context7 rather than
relying on training knowledge, which may be outdated.

### 5.2. Search Tools (Recommended)

- `search` — Search for information on DuckDuckGo.
- `fetch_content` — Retrieve content from a specific URL.

Prioritize official sources (official docs, GitHub, Stack Overflow). Cross-check when possible.

### 5.3. Integration Workflow

```
1. Receive request
   ↓
2. Identify libraries/APIs to use
   ↓
3. [REQUIRED] Look up Context7 for accurate API documentation
   ↓
4. [Optional] Search for additional information if needed
   ↓
5. Implement code with verified APIs
   ↓
6. Test and validate
```

---

## 6. Project Configuration Structure

This project uses a modular configuration system in the `.github/` directory.

### 6.1. Directory Structure

```
.github/
├── copilot-instructions.md    # This file — quick reference and mandatory rules
├── agents/                    # Specialized AI agent roles
│   ├── ai-expert.agent.md
│   ├── devops-expert.agent.md
│   ├── python-expert.agent.md
│   ├── security-expert.agent.md
│   └── testing-expert.agent.md
├── skills/                    # Reusable skills
│   ├── code-review/SKILL.md
│   ├── commit-message/SKILL.md
│   ├── debugging/SKILL.md
│   └── prompt-engineering/SKILL.md
├── prompts/                   # Reusable prompt templates
│   ├── system/
│   │   ├── developer.md
│   │   └── code-review.md
│   └── tools/
│       └── tool-instructions.md
├── workflows/                 # Task workflows
│   ├── develop-feature.md
│   ├── create-decision-record.md
│   └── run-tests.md
└── rules/                     # Coding standards (see Section 1)
    ├── project-overview.md
    ├── coding-standards.md
    ├── code-quality.md
    ├── security.md
    ├── testing.md
    ├── git-conventions.md
    ├── design-principles.md
    ├── communication.md
    ├── command-execution.md
    └── agent-config.md
```

### 6.2. How to Use Agents

Invoke with `@agent-name`. Each agent has a specialized focus:

- **`@ai-expert`**: Build production-ready LLM applications, advanced RAG systems, and intelligent agents. Implements vector search, multimodal AI, agent orchestration, and enterprise AI integrations.
- **`@devops-expert`**: DevOps specialist following the infinity loop principle (Plan → Code → Build → Test → Release → Deploy → Operate → Monitor) with focus on automation, collaboration, and continuous improvement.
- **`@python-expert`**: Expert Python 3.12+ developer focused on modern features, async programming, performance optimization, code quality, type safety, testing, and production-ready practices.
- **`@security-expert`**: Security-focused code review specialist with OWASP Top 10, Zero Trust, LLM security, and enterprise security standards.
- **`@testing-expert`**: Testing specialist for Python projects using pytest, with focus on test quality and coverage.

Notes:
- `@ai-expert` should read `.github/agents/python-expert.agent.md` before writing Python code.
- Keep this section synchronized with `.github/agents/` when agent definitions change.

### 6.3. How to Use Skills

Invoke with `/skill-name`:

- **`/code-review`** — `.github/skills/code-review/SKILL.md`
- **`/commit-message`** — `.github/skills/commit-message/SKILL.md`
- **`/debugging`** — `.github/skills/debugging/SKILL.md`
- **`/prompt-engineering`** — `.github/skills/prompt-engineering/SKILL.md`

### 6.4. How to Use Workflows

Invoke with `/workflow-name`:

- **`/develop-feature`** — `.github/workflows/develop-feature.md`
- **`/create-decision-record`** — `.github/workflows/create-decision-record.md`
- **`/run-tests`** — `.github/workflows/run-tests.md`

### 6.5. Using Prompts

Externalized prompts are stored in `.github/prompts/`:

- System prompts: `.github/prompts/system/`
- Tool instructions: `.github/prompts/tools/`

Load prompts in code:

```python
from pathlib import Path

PROMPT_DIR = Path(".github/prompts")

def load_prompt(name: str, **kwargs: str) -> str:
    """Load and format a prompt template."""
    template = (PROMPT_DIR / f"{name}.md").read_text()
    return template.format(**kwargs)
```
