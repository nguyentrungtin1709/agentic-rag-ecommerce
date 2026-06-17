# Testing Rules

## AAA Pattern (Arrange-Act-Assert)

All unit tests must follow this structure:
```python
# Arrange: Prepare data and dependencies
# Act: Execute the action to test
# Assert: Verify the result
```

## Testing Principles

- **Isolation**: Each test must be independent, not dependent on other tests.
- **Deterministic**: Same input always produces same output.
- **Fast**: Unit tests must run quickly (< 100ms/test).
- **Readable**: Test names must clearly describe the scenario and expected result.

## Test Naming Convention

Format: `test_method_name_scenario_expected_behavior`

```python
def test_execute_tool_with_valid_input_returns_tool_result():
def test_execute_tool_with_unknown_name_raises_tool_not_found_error():
def test_trim_messages_with_empty_list_returns_empty_list():
```

## Test Coverage Guidelines

- **Unit Tests**: Business logic, utilities, algorithms.
- **Integration Tests**: Database, API, external services.
- **E2E Tests**: Critical user flows.

## Mocking & Dependencies

- Mock external dependencies (APIs, databases, file system).
- Use **Dependency Injection** to easily mock.
- Avoid excessive mocking -- it may indicate high code coupling.
