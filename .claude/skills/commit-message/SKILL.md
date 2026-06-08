---
name: commit-message
description: Generate conventional commit messages - use when creating commits, writing commit messages, or asking for git commit help.
disable-model-invocation: true
---

# Commit Message Skill

Generate commit messages following the Conventional Commits specification.

## Format

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

## Types

| Type | Description | When to Use |
|------|-------------|-------------|
| `feat` | New feature | Adding new functionality |
| `fix` | Bug fix | Fixing existing bugs |
| `docs` | Documentation | README, docs, comments only |
| `style` | Formatting | Code style (no logic change) |
| `refactor` | Refactoring | Code change without new features/fixes |
| `perf` | Performance | Performance improvements |
| `test` | Testing | Adding or updating tests |
| `build` | Build system | Dependencies, build tools |
| `ci` | CI/CD | GitHub Actions, pipeline changes |
| `chore` | Maintenance | Maintenance tasks |

## Rules

1. Subject line maximum 72 characters
2. Use imperative mood ("add" not "added" or "adds")
3. No period at end of subject line
4. Separate subject from body with blank line
5. Body explains **what** and **why**, not how
6. Footer for breaking changes: `BREAKING CHANGE: description`

## Examples

### Simple
```
fix(auth): prevent redirect loop on expired sessions
```

### With scope and body
```
feat(api): add rate limiting to public endpoints

- Limits requests to 100/minute per IP
- Returns 429 status with retry-after header
- Configurable via RATE_LIMIT_MAX env variable
```

### Breaking change
```
feat(api)!: change response format to JSON

BREAKING CHANGE: API now returns JSON instead of XML
```

## Process

1. Analyze the changes made (read git diff if needed)
2. Determine the type (feat, fix, refactor, etc.)
3. Identify scope if applicable (auth, api, db, agents, tools, etc.)
4. Write a concise description in imperative mood
5. Add body if more context is needed
6. Check character limits
