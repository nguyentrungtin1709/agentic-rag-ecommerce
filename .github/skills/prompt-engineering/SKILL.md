---
name: prompt-engineering
description: Prompt writing best practices - use when creating or improving prompts for LLM agents
---

# Prompt Engineering Skill

Create effective prompts for AI Agent systems.

Use this skill when:
- Creating or revising system prompts, tool instructions, or evaluator prompts
- Improving agent reliability, safety, or output consistency through prompt changes
- Designing prompts for RAG, tool use, or multi-step workflows

## Core Principles

### 1. Clear and Specific
- State exactly what you want the model to do
- Avoid ambiguity
- Use precise language

### 2. Include Context
- Provide necessary background information
- Explain the domain or task
- Set expectations for output format

### 3. Define Output Format
- Specify the exact format needed (JSON, markdown, etc.)
- Provide examples when possible
- Be explicit about structure

### 4. Use Constraints
- Specify what NOT to do
- Set boundaries on responses
- Limit scope when needed

### 5. Design for Evaluation
- Write prompts so outputs can be checked with assertions or human review
- Prefer explicit formats, stable structure, and testable requirements
- Treat prompt iteration as an evidence-driven loop, not guesswork

### 6. Design for Safety
- Constrain tool use and privileged actions clearly
- State how to handle missing information, unsafe requests, or ambiguous input
- Avoid sending sensitive data to external models without explicit approval

### 7. Design for Maintainability
- Externalize prompts into files
- Keep prompts modular and versioned
- Avoid giant prompts that mix role, policy, formatting, and task logic without structure

---

## Prompting Techniques

### Zero-shot Prompting
Ask the model to perform a task without any examples.

```
Task: Classify the sentiment of this review as positive or negative.
Review: "This product exceeded my expectations!"
```

### One-shot Prompting
Provide a single example to show the expected format or behavior.

```
Task: Classify the sentiment as positive or negative.

Example:
Review: "This product is terrible."
Sentiment: negative

Now classify:
Review: "Love this product!"
Sentiment:
```

### Few-shot Prompting
Provide 2-5 examples to demonstrate the pattern.

```
Task: Extract the main entity and its sentiment.

Examples:
- "Apple released new iPhone" -> Entity: Apple, Sentiment: neutral
- "Microsoft stock surged 5%" -> Entity: Microsoft, Sentiment: positive
- "Tesla recalled vehicles" -> Entity: Tesla, Sentiment: negative

Now extract:
- "Google launched AI assistant"
```

### Multi-shot Prompting
Use 6+ examples for complex or nuanced tasks. Helps the model understand edge cases.

When to use:
- Complex output formats
- Multiple conditions or rules
- Edge cases that need clarification
- Domain-specific terminology

### Chain-of-Thought (CoT)
Ask the model to show its reasoning step by step.

Note: Use reasoning prompts carefully. Prefer concise reasoning or hidden reasoning patterns when exposing long internal reasoning is unnecessary or unsafe.

```
Task: If there are 5 birds on a fence and you shoot 1, how many are left?
Think step by step:
1. Starting count: 5 birds
2. Action: shoot 1 bird
3. The shot may cause all birds to fly away
4. Answer: 0 (or 1 if the bird died and others stayed)
```

### Tree-of-Thought (ToT)
Explore multiple reasoning paths for complex decisions.

```
Task: Find the most efficient route from A to D through B and C.
Explore different paths:
- Path 1: A -> B -> D (cost: 10)
- Path 2: A -> C -> D (cost: 8)
- Path 3: A -> B -> C -> D (cost: 12)
Best path: A -> C -> D
```

### Evaluator Prompts
Use a stronger or separate model to critique outputs against explicit criteria.

Good evaluator prompts include:
- The original input
- The model output to judge
- Clear grading criteria
- Required output schema for the judgment

Example:

```
Task: Evaluate whether the answer is grounded in the provided context.

Input Question: {question}
Retrieved Context: {context}
Answer: {answer}

Return JSON with:
- grounded: true or false
- reason: short explanation
```

---

## Task Process Description

When describing a task, include step-by-step instructions:

### Template with Steps

```
# Task: [Task Name]

## Objective
[Brief description of what to achieve]

## Process Steps
1. [First step - what to do]
2. [Second step - what to do]
3. [Third step - what to do]
...

## Output Requirements
- [Format requirement 1]
- [Format requirement 2]

## Examples
Example: [input] -> [expected output]
```

### Example: Code Review Task

```
# Task: Code Review

## Objective
Review Python code for quality, security, and best practices.

## Process Steps
1. Read the code file to understand the implementation
2. Check for security vulnerabilities (hard-coded secrets, injection risks)
3. Verify code quality (type hints, docstrings, error handling)
4. Identify test coverage gaps
5. Provide specific recommendations with line numbers

## Output Format
- Critical Issues: [list]
- High Priority: [list]
- Medium Priority: [list]
- Suggestions: [list]

## Example
Code: def connect_db(password="hardcoded"): ...
Output:
- Critical Issues: Hard-coded password on line 1
```

---

## Prompt Structure

```
# Role/Identity
You are [role] with expertise in [domain].

# Task
[Clear description of what to do]

# Context
[Background information needed]

# Constraints
- [Constraint 1]
- [Constraint 2]

# Output Format
[Exact format specification]

# Examples
Example input: ...
Example output: ...
```

## Tool And Agent Prompting

### Tool Instructions
- Treat tool descriptions like API docs for a junior engineer
- Be explicit about when to use the tool, when not to use it, required arguments, and edge cases
- Prefer formats that are natural for models to produce reliably

### Multi-step Agents
- State the planning policy clearly: when to plan, when to act, when to ask for clarification
- Define stop conditions and escalation conditions
- Specify how the agent should use external evidence before concluding
- In agent workflows, make intermediate outputs auditable where possible

### RAG Prompts
- Distinguish grounded facts from model inference
- Tell the model how to behave when retrieved context is weak, conflicting, or missing
- Prefer explicit instructions such as: "If the answer is not supported by the context, say so"

## Externalization

Always externalize prompts to separate files:

```
prompts/
├── system.md           # Main system prompt
├── tool_instructions/  # Tool-specific instructions
│   ├── web-search.md
│   └── code-execution.md
└── templates/          # Reusable templates
    ├── summary.md
    └── analysis.md
```

## Loading Prompts

```python
from pathlib import Path

PROMPT_DIR = Path("prompts")

def load_prompt(name: str, **kwargs: str) -> str:
    """Load and format a prompt template."""
    template = (PROMPT_DIR / f"{name}.md").read_text()
    return template.format(**kwargs)
```

## Version Control

- Treat prompt changes as code changes
- Commit prompts alongside implementation
- Track prompt versions in history records
- Test prompt changes with evaluation suite

## Iteration With Evidence

1. Write the initial prompt with clear success criteria
2. Test with representative and adversarial inputs
3. Inspect failures using logs, traces, and evaluations
4. Refine the prompt with a single clear hypothesis
5. Re-run tests and compare results against the previous version
6. Keep changes that improve the target metric without introducing regressions

## Anti-patterns

- Hard-coding prompts in source files
- Mixing multiple unrelated tasks into one prompt without routing
- Using vague requirements that cannot be evaluated
- Relying on prompt edits without reviewing traces or failures
- Growing prompts indefinitely instead of restructuring them
