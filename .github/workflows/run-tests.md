---
name: run-tests
description: Test execution workflow - run unit tests, linting, and type checking
---

# Run Tests Workflow

Follow this workflow to run tests and verify code quality.

## Prerequisites

Ensure development environment is set up:
```bash
# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

## Test Commands

### 1. Run Linter
```bash
# Ruff - linting
ruff check .

# Or flake8
flake8 .
```

### 2. Run Formatter Check
```bash
# Ruff format
ruff format --check .

# Or black
black --check .
```

### 3. Run Type Checker
```bash
mypy .
```

### 4. Run Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=. --cov-report=term-missing

# Specific file
pytest tests/test_filename.py

# With verbose output
pytest -v

# Stop on first failure
pytest -x
```

### 5. Run Pre-commit Hooks
```bash
# Run all pre-commit hooks
pre-commit run --all-files

# Or on staged files only
pre-commit run
```

## CI Pipeline Order

Run in this order for efficiency:

1. **Lint** (fastest) - catch style issues
2. **Format** - auto-fix style issues
3. **Type check** - catch type errors
4. **Tests** - verify functionality

## Common Issues

### Tests fail
- Read test output to identify failing assertions
- Use `-v` flag for verbose output
- Use `-x` to stop on first failure
- Check test isolation - tests should be independent

### Lint errors
- Most can be auto-fixed: `ruff check --fix .`
- Read error messages carefully
- Check specific rules in configuration

### Type errors
- Fix in source code, not in type hints (usually)
- Use `# type: ignore` sparingly and document why
- Check mypy configuration in pyproject.toml

## Success Criteria

All of these should pass:
- [ ] `ruff check .` passes (no errors)
- [ ] `ruff format --check .` passes
- [ ] `mypy .` passes (or configured warnings only)
- [ ] `pytest` passes (all tests green)
