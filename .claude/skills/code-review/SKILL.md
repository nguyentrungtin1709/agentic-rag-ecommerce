---
name: code-review
description: Code review checklist - use for checking Python code quality, bugs, security issues, and best practices. Use when a user asks for a code review, needs to assess whether a change is safe to merge, or needs to review AI-agent code for production risk.
---

# Code Review Skill

Apply this checklist when reviewing code, with emphasis on bugs, risks, regressions, security, and missing tests.

## Code Quality Checklist

- [ ] All functions have type hints (parameters and return types)
- [ ] No bare except clauses - catch specific exceptions
- [ ] No mutable default arguments
- [ ] Context managers used for file I/O and resources
- [ ] Functions are under 50 lines when possible
- [ ] Variable and function names follow PEP 8 (snake_case)
- [ ] Classes follow PascalCase convention
- [ ] Public APIs have docstrings (Google style)
- [ ] Configuration is externalized instead of hard-coded
- [ ] Prompts are externalized instead of embedded inline
- [ ] Logging is meaningful and structured (JSON format)

## Input Validation Checklist

- [ ] User input validated before processing
- [ ] Edge cases handled (empty strings, None, out-of-range values)
- [ ] Error messages are clear and helpful
- [ ] Type hints match actual usage

## Security Checklist

- [ ] No hard-coded secrets or API keys
- [ ] Input sanitized to prevent injection
- [ ] File operations validated (no path traversal)
- [ ] Sensitive data not logged
- [ ] Prompt injection and unsafe tool use considered for AI features

## Testing Checklist

- [ ] New code has corresponding pytest tests
- [ ] Edge cases are covered
- [ ] Tests use descriptive names: `test_<fn>_<scenario>_<expected>`
- [ ] AAA pattern followed (Arrange-Act-Assert)
- [ ] Regressions are covered when fixing a bug

## AI/Agent-Specific Checklist

- [ ] Prompts, models, and runtime parameters are versionable or configurable
- [ ] Tool schemas and tool descriptions are clear enough to prevent misuse
- [ ] Token usage, latency, and error behavior are considered
- [ ] Tracing or equivalent observability exists for multi-step flows
- [ ] Evaluation strategy exists for quality-sensitive behavior

## Output Format

Present findings in severity order:

```
## Code Review: [filename]

### Critical Issues
- [ISSUE] Description with file/line reference and impact

### High Priority
- [ISSUE] Description with file/line reference and impact

### Medium Priority
- [ISSUE] Description with file/line reference and impact

### Open Questions
- [QUESTION] Assumption, ambiguity, or missing context
```
