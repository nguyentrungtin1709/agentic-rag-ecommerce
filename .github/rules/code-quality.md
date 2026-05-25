# Code Quality

## Logging Rules

### Log Levels

| Level | When to Use |
|-------|-------------|
| **DEBUG** | Development, troubleshooting |
| **INFO** | Startup, shutdown, key operations |
| **WARNING** | Deprecated usage, retry attempts |
| **ERROR** | Exception caught, operation failed |
| **CRITICAL** | System crash, unrecoverable error |

### Structured Logging
- Use **structured format** (JSON) instead of plain text.
- Include context: `timestamp`, `level`, `message`, `correlation_id`, `user_id`.
- For AI/LLM systems, include durable execution context when applicable: `trace_id`, `prompt_version`, `model`, `latency_ms`, `prompt_tokens`, `completion_tokens`, and `tool_name`.

### Best Practices
- Log **meaningful messages** with full context.
- Include **request/correlation ID** for tracing across services.
- Log at **entry/exit points** of important operations.
- Log at AI system boundaries: user input handling, prompt assembly, retrieval, tool execution, model calls, and final response formatting.
- AVOID: Logging sensitive data (passwords, tokens, PII).
- AVOID: Excessive logging in production.
- AVOID: Using `print()` instead of a logging framework.

```python
# Good
logger.error(
    "Failed to execute tool",
    extra={"tool_name": tool_name, "agent_run_id": run_id, "error": str(exception)},
    exc_info=True,
)

# Bad
logger.error("Error occurred")
```

## Observability For AI Systems

### Tracing
- Instrument multi-step AI flows with `session -> trace -> span` semantics.
- Every LLM call, retrieval step, and tool execution should be traceable within the same request flow.
- Preserve correlation and trace IDs across boundaries so logs, traces, and metrics can be linked during debugging.

### Metrics
- Track latency (`p50`, `p95`, `p99`) for model calls and end-to-end requests.
- Track prompt and completion tokens for cost and context-window management.
- Track quality-related metrics where possible: schema pass rate, tool success rate, hallucination rate, user feedback, and task completion rate.
- Track retrieval quality for RAG systems separately from generation quality.

### Runtime Quality Checks
- Reuse validation and evaluation assertions at runtime when feasible: schema validation, safety checks, content guards, and tool-output verification.
- Prefer failing fast on malformed or unsafe intermediate outputs rather than letting bad state propagate through the system.

---

## Error Handling

### General Principles
- Always use `try-except` for: **File I/O**, **Network**, **External Process**, **Database**.
- **Fail fast**: Validate input early, raise exceptions immediately if invalid.
- Don't swallow exceptions (empty `except`) -- at least log them.
- Catch **specific exceptions**, never bare `except:` or `except Exception:`.
- Use `finally` or context managers (`with`) for resource cleanup.

### Custom Exceptions
- Create custom exception classes inheriting from appropriate base exceptions.
- Exception messages must be clear and actionable.

```python
class ModelLoadError(RuntimeError):
    """Raised when a model fails to load from disk."""
    def __init__(self, model_path: str, reason: str) -> None:
        super().__init__(f"Failed to load model from '{model_path}': {reason}")
        self.model_path = model_path
        self.reason = reason
```

### Python-Specific
- Use `raise ... from original_exception` to preserve exception chain.
- Prefer **EAFP** over **LBYL** when appropriate.
- Use `contextlib.suppress()` only when intentionally ignoring specific exceptions.
- When wrapping external LLM or tool failures, preserve the original exception and attach enough context for debugging without leaking sensitive data.

### Error Response
- APIs must return error responses with a consistent structure.
- Include: error code, message, details (if needed).

---

## Code Formatting & Linting

### Formatter
- Use **`black`** or **`ruff format`** as the code formatter.
- Line length: **88 characters** (black default) or **120 characters**.
- All code must be formatted before committing.
- For this project, prefer a **ruff-first** toolchain when choosing new automation or examples.

### Linter
- Use **`ruff`** (recommended) or **`flake8`**.
- Enable rules for: code style, import order, unused variables, complexity.
- All linting errors must be resolved before committing.

### Import Sorting
- Use **`isort`** or **`ruff`** built-in import sorting.
- Import order: **stdlib** -> **third-party** -> **local**.
- Use absolute imports; avoid wildcard imports (`from module import *`).

```python
import json
import os
from pathlib import Path

import httpx
from openai import OpenAI
from pydantic import BaseModel

from src.agents.orchestrator import AgentOrchestrator
from src.tools.registry import ToolRegistry
```

### Type Checker
- Use **`mypy`** with strict mode for production code.
- Configure in `pyproject.toml`.
- Use `pyright` as a complementary checker when stricter or editor-integrated analysis is useful, but keep one canonical configuration path documented.

### Pre-commit Hooks
- Use **`pre-commit`** to run formatters & linters before each commit.
- Include in CI/CD pipeline as a quality gate.

### Configuration
All tool configurations should be centralized in **`pyproject.toml`**:

```toml
[tool.black]
line-length = 88
target-version = ["py310"]

[tool.ruff]
line-length = 88
select = ["E", "F", "W", "I", "N", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
```
