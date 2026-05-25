# Coding Standards

## Naming Conventions (PEP 8)

| Component | Rule | Example |
|-----------|------|---------|
| **Class** | `PascalCase`, noun | `AgentOrchestrator`, `ToolRegistry` |
| **Abstract Class** | `PascalCase`, prefix `Base`/`Abstract` | `BaseAgent`, `AbstractTool` |
| **Method/Function** | `snake_case`, verb | `parse_llm_response()`, `execute_tool()` |
| **Variable/Parameter** | `snake_case`, noun | `max_tokens`, `system_prompt` |
| **Constant** | `UPPER_SNAKE_CASE` | `MAX_CONTEXT_LENGTH`, `DEFAULT_TEMPERATURE` |
| **Private attribute** | `_snake_case` | `_llm_client`, `_config_path` |
| **Module/Package** | `snake_case`, short | `agent_utils`, `prompt_builder` |
| **Boolean** | Prefix `is_`, `has_`, `can_`, `should_` | `is_valid`, `has_tool_access` |
| **Type Variable** | `PascalCase`, short | `T`, `KT`, `VT`, `ReturnType` |

**General:**
- Names must be **self-documenting**. Avoid uncommon abbreviations.
- Function names must describe the action (`get_user_by_id`, not `user`).
- Use `_` prefix for internal/private, avoid `__` unless necessary.

## AI/Agent Naming Conventions

- Prefer names that reflect the system role clearly: `orchestrator`, `evaluator`, `retriever`, `reranker`, `tool_registry`, `prompt_loader`.
- Name prompt files by purpose, not by vague chronology: `system.md`, `retrieval_grader.md`, `tool_selection.md`, not `prompt_v2_final.md`.
- Name configuration keys explicitly: `model`, `temperature`, `max_tokens`, `prompt_version`, `trace_id`, `session_id`.
- Use version-bearing names only when the version is operationally meaningful and tracked consistently.
- Tool names should describe the action and boundary clearly, e.g. `search_documents`, `run_sql_query`, `write_file`, not `do_search` or `helper_tool`.

---

## Type Hints (PEP 484)

- **All** function/method signatures must include type hints for parameters and return types.
- For Python 3.10+, prefer built-in generics (`list[str]` instead of `List[str]`).
- Use `TypeAlias` for complex type definitions.
- Run `mypy` for static type checking.

```python
# Good
def call_llm(
    messages: list[dict[str, str]],
    model: str = "gpt-4o",
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> str: ...

# Bad
def call_llm(messages, model="gpt-4o"): ...
```

---

## Docstrings (PEP 257)

- Every **module**, **class**, and **public function/method** must have a docstring.
- Use **Google style** docstring format.
- Include: description, `Args`, `Returns`, `Raises` as applicable.
- For orchestration code, document the decision boundary and side effects, not just the happy-path behavior.

```python
def run_agent(
    user_message: str,
    tools: list[ToolDefinition],
    model: str = "gpt-4o",
) -> AgentResponse:
    """Run an AI agent with tool-calling capabilities.

    Args:
        user_message: The user's input message to process.
        tools: List of tool definitions available to the agent.
        model: LLM model identifier. Defaults to "gpt-4o".

    Returns:
        An AgentResponse containing the final answer and tool call history.

    Raises:
        LLMAPIError: If the LLM API call fails after retries.
    """
```

---

## Comment Rules

- Comment **why**, not **what** -- code already shows what it does.
- Only comment non-obvious logic, formulas, design decisions, or warnings.
- Use **English** exclusively for all comments.
- Block comments: separate line above code, full sentences, capitalized.
- Inline comments: at least 2 spaces from code, under ~50 characters.
- Comments around prompts, evaluators, and tool orchestration should explain constraints or safety decisions, not paraphrase the prompt text.

**FORBIDDEN:**
- Separator comments (`# ---`, `# ===`, `# ***`)
- Commented-out code -- use version control instead
- TODO comments without a linked issue/ticket

## Prompt And Configuration Hygiene

- Externalize prompts, tool instructions, and evaluator criteria into files or configuration.
- Do not hard-code model parameters, prompt templates, API endpoints, or feature flags inside orchestration logic.
- Prefer typed configuration objects or validated dictionaries at system boundaries.
- Keep prompt variables explicit and stable: `{user_query}`, `{context}`, `{tool_results}`, `{evaluation_criteria}`.
