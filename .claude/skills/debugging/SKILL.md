---
name: debugging
description: Debugging patterns and strategies - use when debugging issues, errors, or unexpected behavior. Use when a feature is failing, tests are failing, an AI/LLM workflow produces low-quality outputs, or a multi-step agent pipeline is hard to reason about.
---

# Debugging Skill

Apply systematic debugging approaches to identify, explain, and fix issues.

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
  - SyntaxError: Code does not parse
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
- Run related tests: `pytest tests/test_<module>.py -v`
- Check for similar issues elsewhere
- Confirm the fix addresses the root cause, not just the visible symptom

## AI System Debugging

### Trace First
- Find the exact request, trace, or session that failed
- Read the full sequence: user input → prompt assembly → retrieval → tool calls → model output → post-processing
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
