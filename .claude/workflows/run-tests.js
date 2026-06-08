/**
 * Run Tests Workflow
 *
 * Runs the full quality check pipeline: lint, format, type check, tests.
 * Collects all failures and produces a consolidated report.
 * Usage: /run-tests [target path or test filter]
 */
const target = args?.target || args || ".";

// Step 1: Lint
const lintResult = await claude.runAgent({
  agent: "Explore",
  prompt: `
    Run the following commands and capture ALL output including errors:

    1. ruff check ${target}
    2. ruff format --check ${target}

    Report:
    - Number of linting errors found
    - Files with errors
    - Full error messages
    - Whether auto-fix is possible: ruff check --fix
  `,
});

// Step 2: Type Check
const typeResult = await claude.runAgent({
  agent: "Explore",
  prompt: `
    Run: pyright ${target === "." ? "" : target}

    Report:
    - Number of type errors
    - Files with type errors
    - Full error messages with line numbers
  `,
});

// Step 3: Tests with Coverage
const testResult = await claude.runAgent({
  prompt: `
    Run: pytest ${target === "." ? "" : target} -v
    (pyproject.toml configures: testpaths=["tests"], --cov=src/app, --cov-fail-under=80)

    Capture and report:
    - Total tests: passed, failed, skipped, errors
    - Coverage percentage per module
    - Full output for any FAILED or ERROR tests including:
      - Test name
      - Error message
      - Stack trace
    - Any warnings that should be addressed
  `,
});

// Consolidated Report
await claude.runAgent({
  agent: "Explore",
  prompt: `
    Produce a consolidated quality report from these results:

    Lint results:
    ${lintResult}

    Type check results:
    ${typeResult}

    Test results:
    ${testResult}

    Report format:
    ## Quality Check Results

    ### Summary
    - Lint: [PASS/FAIL] (N errors)
    - Type check: [PASS/FAIL] (N errors)
    - Tests: [PASS/FAIL] (N passed, N failed, N% coverage)

    ### Lint Issues
    [list issues or "No issues found"]

    ### Type Errors
    [list errors or "No type errors"]

    ### Test Failures
    [list failures with details or "All tests passed"]

    ### Recommended Actions
    [prioritized list of fixes needed]
  `,
});
