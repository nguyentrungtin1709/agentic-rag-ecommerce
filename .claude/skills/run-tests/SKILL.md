---
name: run-tests
description: Test execution workflow - run unit tests, linting, and type checking. Use when verifying code quality, running the full test suite, or checking before a commit.
<!-- disable-model-invocation: true -->
allowed-tools: Bash(pytest *) Bash(ruff *) Bash(pyright *) Bash(pre-commit *) Bash(uv run *)
---

# Run Tests Workflow

## Prerequisites

```bash
uv sync
uv run pre-commit install
```

## Test Commands

### 1. Lint
```bash
ruff check .
```

### 2. Format Check
```bash
ruff format --check .
```

### 3. Type Check
```bash
pyright
```

### 4. Run Tests
```bash
# All tests (pyproject.toml configures: testpaths=["tests"], --cov=src/app, --cov-fail-under=80)
pytest

# Specific file with extra coverage info
pytest tests/test_filename.py --cov=src/app

# Specific file
pytest tests/test_filename.py

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

### 5. All Checks at Once
```bash
pre-commit run --all-files
```

## CI Pipeline Order

Run in this order for efficiency:
1. Lint (fastest) - catch style issues
2. Format - auto-fix style issues
3. Type check - catch type errors
4. Tests - verify functionality
