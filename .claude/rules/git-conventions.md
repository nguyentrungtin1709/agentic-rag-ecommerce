# Git Commit Rules (Conventional Commits)

## Commit Message Format

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

## Commit Types

| Type | Description | SemVer |
|------|-------------|--------|
| `feat` | Add new feature | MINOR |
| `fix` | Fix bug | PATCH |
| `docs` | Documentation changes only | - |
| `style` | Code formatting, no logic changes | - |
| `refactor` | Refactor code, no new features or bug fixes | - |
| `perf` | Performance improvements | - |
| `test` | Add or fix tests | - |
| `build` | Build system or dependency changes | - |
| `ci` | CI configuration changes | - |
| `chore` | Other changes (not affecting src/test) | - |

## Breaking Changes

- Use `!` after type: `feat!: remove deprecated API`
- Or add footer: `BREAKING CHANGE: description`

## Examples

```
feat(auth): add OAuth2 authentication support
fix(image-processor): handle null pointer in crop function
docs: update API documentation for v2.0
refactor(pipeline): extract preprocessing into separate module
feat(api)!: change response format to JSON

BREAKING CHANGE: API now returns JSON instead of XML
```

## Best Practices

- Each commit is a complete **logical unit**.
- Commit message is concise (< 72 characters for title).
- Use **imperative mood** ("add feature" not "added feature").
- AVOID: Committing code that doesn't compile/run.
- AVOID: Combining unrelated changes into one commit.
