---
name: create-decision-record
description: Create a decision history record in the history/ directory. Use before writing any implementation code for a new feature, architecture change, or significant technical decision.
disable-model-invocation: true
---

# Create Decision Record Skill

Create a history record before writing any implementation code.

## Steps

1. Determine the next version number by reading existing files in `history/`
   - Format: `{MAJOR}_{MINOR}_{PATCH}_{SHORT_DESCRIPTION}.md`
   - MAJOR: breaking changes or major architecture shifts
   - MINOR: new features
   - PATCH: fixes and small improvements
   - Start at `1_0_0` if no records exist

2. Create the file in `history/` using this template:

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
High-level implementation approach — architecture pattern, key components, tools/libraries used.

## Key Decisions
- Decision 1: {What was decided} — {Why this option over alternatives}

## Impact
Which modules/files are affected. Any breaking changes or migration steps.
```

3. Keep descriptions concise — this is a decision record, not documentation.
4. Update Status as work progresses: Planned → In Progress → Completed.
