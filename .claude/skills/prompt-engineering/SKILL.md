---
name: prompt-engineering
description: Prompt writing best practices - use when creating or improving prompts for LLM agents. Use when creating or revising system prompts, tool instructions, or evaluator prompts, or when improving agent reliability, safety, or output consistency.
---

# Prompt Engineering Skill

Create effective prompts for AI Agent systems.

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
- Externalize prompts into files: `src/agents/prompts/` or `.github/prompts/`
- Keep prompts modular and versioned
- Avoid giant prompts that mix role, policy, formatting, and task logic

## Prompting Techniques

### Zero-shot Prompting
Ask the model to perform a task without any examples.

### Few-shot Prompting
Provide 2-5 examples to demonstrate the pattern.

### Chain-of-Thought
Ask the model to reason step by step before giving a final answer.

```
Think step by step:
1. First, identify...
2. Then, determine...
3. Finally, produce...
```

### ReAct Pattern (for agents)
```
You have access to the following tools: {tools}

Use this format:
Thought: Think about what to do
Action: tool_name
Action Input: tool_input
Observation: result of the action
...
Thought: I now know the final answer
Final Answer: the final answer
```

## Prompt File Structure

Store prompts as markdown files with clear sections:

```markdown
# System Prompt: [Agent Name]

## Role
You are a [role description].

## Capabilities
- Capability 1
- Capability 2

## Instructions
1. When given a task...
2. Always...
3. Never...

## Output Format
Respond in the following format:
[format specification]

## Examples
[optional examples]
```

## Quality Checks Before Finalizing

- [ ] Is the role clearly defined?
- [ ] Are instructions specific enough to be verifiable?
- [ ] Are edge cases handled (missing data, ambiguous input)?
- [ ] Is the output format specified?
- [ ] Are safety boundaries stated?
- [ ] Is this prompt testable with assertions?
