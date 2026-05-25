---
name: debugging
description: Debugging patterns and strategies - use when debugging issues, errors, or unexpected behavior
---

# Debugging Skill

Apply systematic debugging approaches to identify, explain, and fix issues.

Use this skill when:
- A feature is failing or behaving unexpectedly
- Tests are failing and the cause is not obvious
- An AI/LLM workflow produces low-quality, inconsistent, or unsafe outputs
- A multi-step agent, tool call, or RAG pipeline is hard to reason about

## Debugging Process

### 1. Reproduce the Issue
- Identify the exact steps to reproduce
- Minimize the reproduction case
- Document the expected vs actual behavior
- Capture the exact input, configuration, and environment used

### 2. Gather Information
- Read error messages carefully
- Check logs using structured logging, not ad-hoc prints
- For AI systems, inspect traces, tool calls, retrieved context, and model outputs
- Identify the error type:
  - SyntaxError: Code doesn't parse
  - TypeError: Wrong type operation
  - ValueError: Invalid value
  - RuntimeError: Runtime logic error
  - Exception: Unhandled error

### 3. Locate the Root Cause
- Use traceback to identify failure point
- Check the call stack
- Identify which component is failing
- Isolate the problematic code section
- For AI systems, classify the failure source before fixing it:
  - Prompt or instruction issue
  - Retrieval or ranking issue
  - Tool selection or tool argument issue
  - Application code bug
  - Model capability or parameter issue
  - External dependency or infrastructure issue

### 4. Fix and Verify
- Apply the fix
- Test the reproduction case
- Run related tests
- Check for similar issues elsewhere
- Confirm the fix addresses the root cause, not just the visible symptom

## AI System Debugging

### Trace First
- Find the exact request, trace, or session that failed
- Read the full sequence: user input -> prompt assembly -> retrieval -> tool calls -> model output -> post-processing
- Preserve correlation IDs so logs, traces, and metrics can be linked

### Inspect Boundaries
- Debug at boundaries where data changes form or responsibility changes owner
- Common boundaries:
  - User input validation
  - Prompt construction
  - Retrieval results
  - Tool invocation and tool output
  - Final response formatting

### Check Observability Signals
- Logs answer: what happened?
- Traces answer: how did it happen?
- Metrics answer: how often and how badly is it happening?
- If these signals are missing, add them before making large changes

### Reuse Eval Infrastructure
- Reuse failing tests, assertions, and eval cases as debugging assets
- Add a regression test for the failure before or alongside the fix when practical
- For LLM systems, keep examples of bad outputs to expand evaluation coverage

## Python Debugging Tools

### Print Debugging
```python
# Use structured logging instead of print
import logging
logger = logging.getLogger(__name__)
logger.debug(f"Variable state: {variable}")
```

### Using pdb
```python
import pdb; pdb.set_trace()  # Set breakpoint
# Commands: n (next), s (step), p (print), c (continue), q (quit)
```

### Using breakpoint()
```python
breakpoint()  # Python 3.7+ - uses pdb by default
```

### IDE Debuggers
- VS Code: F5 to start debugging
- PyCharm: Right-click > Debug

## Common Error Patterns

### Type Errors
- Check actual types with `type(variable)`
- Verify type hints are correct
- Check for None when expecting a value

### Import Errors
- Verify package is installed
- Check PYTHONPATH
- Verify relative vs absolute imports

### Logic Errors
- Add logging to trace execution
- Check boundary conditions
- Verify operator precedence

### AI Output Quality Errors
- Compare expected behavior against actual model output
- Check whether the failure is deterministic or intermittent
- Verify prompt version, model version, and retrieved context
- Confirm tool outputs and schema validation were correct before blaming the model

### RAG Failures
- Check whether relevant documents were retrieved at all
- Check ranking quality, chunking strategy, and context truncation
- Test generation separately with known-good context to isolate retrieval from generation

### Agent Failures
- Verify the agent chose the correct tool
- Verify the tool arguments are valid and complete
- Check whether the stop condition, retry logic, or iteration limit masked the real issue

## Best Practices

1. **Start simple**: Try the simplest fix first
2. **One change at a time**: Modify, test, repeat
3. **Use version control**: Can revert changes easily
4. **Write a test**: Add a test that fails, then passes after fix
5. **Document findings**: Note what caused the issue for future reference
6. **Prefer evidence over intuition**: Use traces, logs, and tests to justify the fix
7. **Preserve reproducibility**: Record the exact failing case, not just a paraphrase
