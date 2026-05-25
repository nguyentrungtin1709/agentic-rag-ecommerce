---
name: create-decision-record
description: Create a decision history record before starting implementation of any feature
---

# Create Decision Record Workflow

This workflow is **MANDATORY** before starting implementation of any feature.

## Purpose

Maintain a traceable log of **what** was decided, **why**, and **how** - so the team can understand the reasoning behind every feature.

## Steps

1. **Check existing records**: Look at files in `history/` directory
2. **Determine version number**:
   - **MAJOR**: Breaking changes or major architectural shifts
   - **MINOR**: New features or significant enhancements
   - **PATCH**: Bug fixes, small improvements, config changes

3. **Create file**: `history/{MAJOR}_{MINOR}_{PATCH}_{SHORT_DESCRIPTION}.md`

4. **Use template**:

```markdown
# {Short Feature Title}

**Version**: {MAJOR}.{MINOR}.{PATCH}
**Date**: {YYYY-MM-DD}
**Status**: Planned | In Progress | Completed

## What
Brief description of the feature or change (1-3 sentences).

## Why
The problem this solves or the value it provides.

## How
High-level implementation approach - architecture pattern,
key components involved, tools/libraries used.

## Key Decisions
- Decision 1: {What} - {Why this over alternatives}
- Decision 2: ...

## Impact
Which modules/files are affected. Breaking changes?
```

5. **Update Status**: Change from Planned → In Progress → Completed as work progresses

## Rules

- **Always create** a history record before writing any code
- Keep descriptions **concise but complete**
- One file per feature/change
- Focus on **decisions**, not implementation details

## File Naming

- Use `UPPER_SNAKE_CASE` for SHORT_DESCRIPTION
- Keep it concise: 2-5 words
- Examples:
  - `1_0_0_INIT_PROJECT.md`
  - `1_1_0_ADD_WEB_SEARCH_TOOL.md`
  - `1_2_0_IMPLEMENT_RAG_PIPELINE.md`
