# Security & Dependency Management

## Secrets Management

- **NEVER** hard-code passwords, API keys, tokens, or credentials in source code.
- Use **environment variables** or **`.env` files** (with `python-dotenv`).
- Always add `.env` to **`.gitignore`**.
- For production, use dedicated secrets managers (AWS Secrets Manager, HashiCorp Vault).

```python
# Good
import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("API_KEY")

# Bad
api_key = "sk-1234567890abcdef"
```

## Input Validation

- **Validate all external input** (user input, API payloads, file contents, environment variables).
- Use type checking and schema validation (`pydantic`, `marshmallow`).
- Never trust data from untrusted sources -- sanitize before use.

## LLM And Agent Security

### Prompt Injection And Tool Safety
- Treat user input, retrieved documents, web content, and tool outputs as untrusted input.
- Defend against prompt injection by separating instructions from data and by validating whether external content should influence tool use or control flow.
- Do not allow model outputs to invoke sensitive tools or side effects without schema validation and, when appropriate, explicit approval boundaries.
- Prefer allowlists and explicit tool routing rules over implicit trust in model reasoning.

### Data Egress And Model Boundaries
- Avoid sending sensitive data, internal documents, credentials, or regulated content to external model providers without explicit approval.
- Minimize the amount of data sent to models: send only what is required for the task.
- Redact or mask secrets, tokens, PII, and confidential identifiers before logging, tracing, or forwarding content.

### Retrieval And Context Security
- Validate document sources before adding them to retrieval pipelines.
- Treat retrieved context as evidence, not authority; retrieved content can be malicious, stale, or irrelevant.
- In grounded-answer systems, require the model to distinguish supported facts from unsupported inference.

### Agent Action Boundaries
- Introduce approval steps for destructive, high-impact, or externally visible actions.
- Enforce stopping conditions, iteration limits, and retry limits to reduce runaway behavior and cascading failures.
- Log sensitive actions with secure, minimal context for auditability.

## Dependency Security

- Audit dependencies for vulnerabilities using **`pip-audit`** or **`safety`**.
- Pin dependencies to exact versions to avoid supply chain attacks.
- Review changelogs before upgrading major versions.
- Minimize dependencies -- prefer stdlib when possible.

## Data Protection

- **Never** commit sensitive data (PII, model weights) to Git.
- Use `.gitignore` for: data files, model checkpoints, credentials, environment files.
- Never log PII, credentials, or sensitive model outputs.
- Apply the same protection rules to observability systems: traces, eval datasets, feedback exports, and prompt logs.

## Notebook Security

- Clear notebook outputs before committing.
- Never store credentials in notebook cells.
- Use `nbstripout` to automatically strip outputs on commit.

---

## Dependency & Environment Management

### Virtual Environment
- **Always** use a virtual environment (`venv`, `conda`, `uv`).
- Never install packages globally.
- Document Python version in `pyproject.toml` or `README.md`.

### Dependency Pinning
- **Pin exact versions** in `requirements.txt` or `pyproject.toml`.
- Use `pip freeze` or `uv pip compile` for lockfiles.
- Separate **production** and **development** dependencies.

### Best Practices
- Document **why** a dependency is needed if not obvious.
- Review and update dependencies periodically.
- Use **`uv`** or **`pip-tools`** for deterministic resolution.
- AVOID: Using `>=` or `~=` version specifiers in production lockfiles.
- AVOID: Adding unnecessary dependencies for trivial functionality.

### Additional Best Practices For AI Systems
- Review dependencies that execute code, call browsers, access files, or proxy model/tool traffic with extra care.
- Document why high-risk dependencies are needed, especially those involved in tool execution, browser automation, or external integrations.
- Prefer deterministic and reviewable configuration for model providers, tool access, and networked integrations.
