# Project Overview

This project focuses on developing **AI Agent systems** (LLM orchestration, tool calling, RAG, multi-agent workflows, prompt engineering). It also emphasizes production readiness: evaluation, observability, safety, cost control, and maintainable agent customization. All code must adhere to good design principles, be maintainable, extensible, and testable.

## Core Principles

- **Clean Code**: Code must be readable, understandable, and self-documenting.
- **DRY (Don't Repeat Yourself)**: Avoid code duplication, extract into reusable functions/classes.
- **KISS (Keep It Simple, Stupid)**: Prefer simple solutions, avoid over-engineering.
- **YAGNI (You Aren't Gonna Need It)**: Don't write code for features not yet needed.
- **Evaluate What You Build**: Quality must be measured with tests, evals, and reviewable evidence.
- **Observe What You Run**: Logging, tracing, and metrics are part of the system design.
- **Secure By Default**: Protect data, tools, prompts, and model interactions from avoidable risk.

---

## Project Structure

```
.
├── .github/               # Agent customizations, skills, workflows, and rules
├── prompts/              # Externalized prompt templates
├── config/                # Configuration files (YAML, JSON)
├── src/                   # Source code
│   ├── agents/           # Agent orchestration logic
│   ├── tools/            # Tool definitions and executors
│   ├── prompts/          # Prompt templates (optional)
│   └── utils/            # Utility functions
├── tests/                 # Unit and integration tests
├── history/               # Decision records (MANDATORY)
└── notebooks/            # Jupyter notebooks for experiments
```

## Key Technologies

- **LLM Providers**: OpenAI, Anthropic, or other providers
- **Agent Patterns**: ReAct, Plan-and-Execute, Multi-Agent Orchestration
- **RAG**: Retrieval-augmented generation with vector stores
- **Tools**: Function calling, web search, code execution
- **Observability**: Structured logging, tracing, metrics, feedback collection
- **Evaluation**: Unit tests, model evals, human review, regression checks
- **Safety**: Prompt injection defenses, PII handling, moderation, approval boundaries

## Development Workflow

1. **Define goals**: What should the agent accomplish?
2. **Choose architecture**: ReAct, Plan-and-Execute, or Multi-Agent?
3. **Design tools**: What capabilities does the agent need?
4. **Externalize prompts**: Store as separate files
5. **Design for safety and observability**: Define logging, tracing, evaluation, and control points
6. **Implement modularly**: Build components in isolation
7. **Test thoroughly**: Unit, integration, and evaluation tests
8. **Document decisions**: Create history records
