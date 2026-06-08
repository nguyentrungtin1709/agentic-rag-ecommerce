# Prompt & Agent Configuration Management

## Prompt Management

- **Externalize all prompts** -- never embed system prompts or instruction templates in code.
- Store prompts as separate files (`prompts/system.md`, `prompts/tool_instructions.txt`) or in config.
- Use **template variables** with clear naming: `{user_query}`, `{context}`, `{tool_results}`.
- Version control prompts alongside code -- prompt changes are code changes.

```python
# Good: Externalized prompt template
from pathlib import Path
PROMPT_DIR = Path("prompts")

def load_prompt(name: str, **kwargs: str) -> str:
    """Load and format a prompt template from file."""
    template = (PROMPT_DIR / f"{name}.md").read_text()
    return template.format(**kwargs)

# Bad: Hard-coded prompt in code
system_prompt = "You are a helpful assistant that..."
```

## Agent Configuration

- **NEVER** hard-code LLM parameters in agent code.
- Use configuration files (`YAML`, `JSON`) or environment variables.
- Save the exact configuration used for each agent run for debugging.
- Keep `.github/agents/*.agent.md` synchronized with `.github/copilot-instructions.md` whenever agent files are added, renamed, or substantially revised.
- If one agent depends on another agent's standards or workflow, document that dependency explicitly in the agent file and keep any higher-level instructions consistent with it.

```yaml
# config/agent_config.yaml
llm:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.7
  max_tokens: 4096
  timeout: 30

agent:
  max_iterations: 10
  system_prompt_file: "prompts/system.md"
  verbose: false

tools:
  - name: "web_search"
    enabled: true
    max_results: 5

retry:
  max_retries: 3
  backoff_factor: 2.0
  retry_on_status: [429, 500, 502, 503]
```

## Conversation & Memory Management

- Define a clear **memory strategy**: full history, sliding window, or summarization.
- Set explicit limits on conversation history to respect context window limits.
- Implement token counting to prevent exceeding model's context length.
- Persist conversation state when needed (database, file, cache).

## Cost & Token Tracking

- Track token usage (prompt tokens, completion tokens) for every LLM call.
- Log cumulative costs per agent run, per user, or per session.
- Set **budget alerts** or **hard limits** to prevent runaway costs.
- Use cheaper models for simple tasks and reserve expensive models for complex reasoning.

## Agent Evaluation & Testing

- Define evaluation criteria: **correctness**, **relevance**, **tool usage accuracy**, **latency**.
- Create reproducible test scenarios with fixed inputs and expected outputs.
- Use **LLM-as-judge** or human evaluation for subjective quality assessment.
- Track evaluation metrics over time to detect regressions.
