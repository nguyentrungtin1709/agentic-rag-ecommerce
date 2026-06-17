/**
 * Develop Feature Workflow
 *
 * Orchestrates the full 5-phase development lifecycle for AI Agent features.
 * Usage: /develop-feature <feature description>
 *
 * Phases: Planning → Development → Testing → Debugging → Deployment
 */
const featureDescription = args?.description || args || "the requested feature";

// Phase 1: Planning & Design
const planResult = await claude.runAgent({
  agent: "Explore",
  prompt: `
    Analyze the codebase to plan implementation of: ${featureDescription}

    Tasks:
    1. Read .claude/rules/project-overview.md to understand architecture
    2. Read src/ directory structure to find relevant modules
    3. Check history/ directory to understand existing decisions and current version
    4. Identify which files need to be created or modified
    5. Determine the architecture pattern: ReAct, Plan-and-Execute, or Multi-Agent
    6. List all external libraries and APIs needed (to look up with Context7)

    Output:
    - List of files to create/modify
    - Recommended architecture pattern with justification
    - List of libraries requiring Context7 documentation lookup
    - Proposed decision record version number (increment from latest in history/)
  `,
});

// Phase 2: Create Decision Record
const decisionRecord = await claude.runAgent({
  prompt: `
    Based on this analysis:
    ${planResult}

    Create a decision record in history/ for: ${featureDescription}
    Follow the naming convention: {MAJOR}_{MINOR}_{PATCH}_{SHORT_DESCRIPTION}.md
    Use the template from .claude/skills/create-decision-record/SKILL.md
    (template is also documented in .claude/rules/project-overview.md)
    Set Status to "In Progress"
  `,
});

// Phase 3: Core Development
const devResult = await claude.runAgent({
  prompt: `
    Implement: ${featureDescription}

    Planning context:
    ${planResult}

    Requirements:
    - Read .claude/rules/coding-standards.md before writing any code
    - Read .claude/rules/code-quality.md for logging and error handling patterns
    - Read .claude/rules/security.md for security requirements
    - Use MCP Context7 to look up documentation for any library before using it
    - Python 3.12+ with type hints on ALL function signatures
    - Google-style docstrings on all public modules, classes, functions
    - No hard-coded secrets, model names, or prompts
    - Externalize all prompts to src/agents/prompts/ files
    - Structured JSON logging — no print()
    - Write pytest unit tests alongside implementation
    - Follow Conventional Commits for any commit messages
  `,
});

// Phase 4: Testing & Validation
const testResult = await claude.runAgent({
  agent: "testing-expert",
  prompt: `
    Verify the implementation quality for: ${featureDescription}

    Context:
    ${devResult}

    Tasks:
    1. Run: pytest
    2. Run: ruff check .
    3. Run: pyright
    4. Report any failures and fix them
    5. Ensure test coverage is adequate for new code
    6. Verify AAA pattern in all new tests
  `,
});

// Phase 5: Final Summary
await claude.runAgent({
  prompt: `
    Provide a deployment-ready summary for: ${featureDescription}

    Development context: ${devResult}
    Test results: ${testResult}

    Include:
    1. Files created/modified
    2. Test results summary
    3. Any remaining TODOs or known limitations
    4. Suggested commit message (Conventional Commits format)
    5. Update the decision record Status to "Completed"
  `,
});
