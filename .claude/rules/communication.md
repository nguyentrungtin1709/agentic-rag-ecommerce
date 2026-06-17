# Communication & Conflict Resolution

## No Emoji or Decorative Unicode (MANDATORY)

**ABSOLUTELY FORBIDDEN** — Do not use emoji or decorative Unicode symbols anywhere:
- Responses, explanations, summaries, code comments, docstrings
- Log messages, `print()` output, Markdown content

Includes: ✅ ❌ ⚠️ 🚀 💡 🔥 ✨ 📌 🎯 and all similar characters.

**Use ASCII alternatives:**
- ✅ / ❌ → `[OK]` / `[FAIL]` or `YES` / `NO`
- ⚠️ → `[WARN]` or `WARNING:`
- 🔥 / 🚀 → plain descriptive words

---

## Language Rules (MANDATORY)

- **Responses**: Always write in **English** unless the user explicitly requests a different language.
- **Source code**: Always write in **English** -- variable names, function names, class names, comments, docstrings, log messages, and commit messages.

---

## Ask First — Never Assume or Silently Substitute (MANDATORY)

### Situation A — Conflict with existing knowledge

When a user's requirement conflicts with what you know (model name, API endpoint, library version):

- **MUST**: Stop and ask the user to confirm before proceeding.
- **MUST**: State clearly what conflicts and what alternative you are considering.
- **FORBIDDEN**: Silently replace the user's specified value with a different one.

### Situation B — Ambiguous or unclear requirements

When a requirement is vague, missing, or open to multiple interpretations:

- **MUST**: Ask for clarification immediately, before writing any code.
- **MUST**: Point out the specific unclear part and explain why it matters.
- **Exception**: For trivial ambiguities, proceed with the most sensible default and state your assumption.

Examples requiring clarification:
- "save the output" without file format/path
- "use the latest model" without provider
- "log errors" without log destination
- "reuse the existing structure" when multiple structures apply

---

## Verify Before Concluding

When uncertain about technical facts (model names, API endpoints, package versions):

- **MUST**: Use browser search or documentation lookup before making a decision.
- **Priority**: Official docs > Official GitHub repo > Reputable tech sources > General search.
- After retrieving information, state the source before proceeding.
