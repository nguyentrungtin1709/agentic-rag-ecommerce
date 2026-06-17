# Command Execution Strategy

Rules for running terminal commands without unnecessarily blocking the conversation.

---

## Command Classification

### Fast (under 5 seconds)

Examples: `rm`, `echo`, `ls`, `cat`, `python -c "..."`, `grep`, `mkdir`

- Set `WaitMsBeforeAsync=3000` to run synchronously.
- Read results directly from output. No additional polling needed.

---

### Medium (5 to 30 seconds)

Examples: `uv sync`, installing a small package, `git clone` a small repo, running a few tests

- Set `WaitMsBeforeAsync=5000` and send to background.
- Poll at most 2 times, each with `WaitDurationSeconds=15`.
- If still RUNNING after 2 polls: stop polling. Notify the user and let them check the result themselves.

---

### Slow (over 30 seconds)

Examples: `uv add <large-package>`, downloading ML models, `docker build`, first-time `npm install`

DO NOT run these commands automatically.

Instead, provide instructions for the user to run manually:

```
This command is estimated to take over 30 seconds.
Please run it yourself in your terminal:

  <exact command>

Let me know when it is done so I can continue.
```

---

## Additional Rules

- Always quote arguments containing special shell characters (`>=`, `<=`, `*`, `?`, `&`).
  - Correct: `uv add 'pinecone>=8.1.0'`
  - Incorrect: `uv add pinecone>=8.1.0`  (shell creates a file named `=8.1.0`)

- Prefer editing `pyproject.toml` manually then running `uv sync` over using `uv add` for large packages. This is faster because uv reuses its cache.

- Do not call `python` directly. Use `uv run python` or activate the venv first.

- Maximum total polling time per command: 30 seconds (2 polls x 15 seconds). Return control to the user after that.
