# Fix generation pipeline (DALL-E 3, ddgs, HF_TOKEN)

**Version**: 16.0.0
**Date**: 2026-06-17
**Status**: Superseded by 16.1.0 (DALL-E 3 portion only)

> The DALL-E 3 portion of this fix was rolled back in 16.1.0:
> the production OpenAI account does not have DALL-E 3 access
> (HTTP 400 `invalid_value`).  The `ddgs` and `HF_TOKEN` fixes
> from this version remain in effect.

## What
Three independent fixes shipped in one version bump:

1. **DALL-E 3 `response_format` rejection** — drop
   `response_format="b64_json"` and switch to the URL-returning
   default. Download the image bytes via an injected `httpx` client
   so the S3 upload path is unchanged.
2. **`ddgs` package missing** — replace
   `duckduckgo-search==8.1.1` with `ddgs` in `pyproject.toml` so
   `langchain_community==0.4.2` finds the runtime it needs and the
   TrendScout DuckDuckGo fallback stops emitting
   `ddgs package may be missing`.
3. **HuggingFace Hub unauthenticated warning** — document and wire
   the `HF_TOKEN` env var (docker-compose, `.env.example`) so
   FastEmbed's BM25 download no longer logs the
   "Warning: You are sending unauthenticated requests to the HF Hub"
   noise on every container start.

## Why
`app.log` shows three production issues:

### Issue 1 — DALL-E 3 image generation
```
BadRequestError: Error code: 400 - {'error': {'message':
'Unknown parameter: \'response_format\'.', 'type':
'invalid_request_error', 'param': 'response_format', 'code':
'unknown_parameter'}}
```
The OpenAI API rejects `response_format` even though the OpenAI
docs list it as supported.  Whether the cause is account-level
moderation, regional restrictions, or a recent API drift, the
behaviour is the same — the call fails.  The current code's
`except Exception` handler swallows the error and emits
`image_failed {reason: "generation_failed"}`, so the user sees
nothing.

### Issue 2 — ddgs missing
```
duckduckgo_client_init_failed: ddgs package may be missing —
see pyproject.toml follow-up.
```
`langchain_community==0.4.2` requires `ddgs`, but the project
pins `duckduckgo-search==8.1.1`.  `_ddg` is set to `None` at
module load time and the DuckDuckGo fallback silently degrades
to Tavily-only.

### Issue 3 — HF unauthenticated warning
```
Warning: You are sending unauthenticated requests to the
Hugging Face Hub.
```
FastEmbed's `Qdrant/bm25` sparse model is downloaded from the
Hub.  Unauthenticated downloads work, but they emit a noisy
warning and may be rate-limited; the recommended fix is to
provide `HF_TOKEN`.

## How

### Fix 1 — DALL-E 3 URL + httpx download
- Remove `response_format="b64_json"` from the
  `openai_client.images.generate(...)` call.  DALL-E 3 returns
  the image as a signed URL by default; the URL is valid for
  ~1 hour, which is plenty of time for the in-process download.
- Add `_download_dalle_image(url, httpx_client) -> bytes` that
  GETs the URL with a 30s timeout and returns the raw bytes.
- Inject an `httpx.AsyncClient` via `config["configurable"]` —
  same DI pattern as `openai_client` and `s3_service`.  The
  chat handler builds the client in `chat.py` and the unit
  tests pass a `MagicMock(spec=httpx.AsyncClient)`.  The client
  is closed by the handler's request lifecycle, not the node.
- Update the test helpers in
  `tests/unit/agent/nodes/test_generate_image.py` to inject a
  mock `httpx_client` and assert on the URL → bytes → S3 path.

### Fix 2 — ddgs
- Remove `duckduckgo-search==8.1.1` from `pyproject.toml`.
- Add `ddgs>=9.0.0,<10.0.0` (latest 9.14.4 per Context7).  The
  lower bound matches the `ddgs` major version
  `langchain_community==0.4.2` was updated against.
- Run `uv lock` to refresh the lockfile.  The transitive
  `ddgs` entry that was already present in `uv.lock` will be
  promoted to a direct dependency.
- Update `tests/unit/agent/subagents/trend_scout/test_tools.py`
  to assert that `_ddg` is not `None` after module import.
  The latent-bug comment in `tools.py:63-73` can stay (it
  documents the resolved contract) or be removed; defer that
  cleanup to a follow-up commit.

### Fix 3 — HF_TOKEN
- Add `HF_TOKEN=` to `.env.example` under a new
  `Hugging Face` section.
- No change to `docker-compose.yml` is required because it
  uses `env_file: .env`; the existing `OPENAI_API_KEY` row
  proves the pattern.
- No code change.  The FastEmbed / HuggingFace Hub client
  reads `HF_TOKEN` from `os.environ` directly.
- Add a one-line note in `README.md` under section 3 (Tech
  Stack) — HF auth is opt-in for higher rate limits.

## Key Decisions
- Decision 1: URL + httpx over switching to `gpt-image-1` —
  the user explicitly requested Option A (URL download, keep
  DALL-E 3 quality).  `gpt-image-1` would change the model
  identity and break the per-row `model` field in
  `generated_images`, which is contract-stability.
- Decision 2: `httpx.AsyncClient` injection over creating a
  client inside the node — matches the existing DI pattern
  (DI.X2) and keeps the request lifecycle owned by the chat
  handler.  Internal clients would also require a `finally`
  close that currently is not in the node's contract.
- Decision 3: `ddgs>=9.0.0,<10.0.0` over pinning an exact
  version — `ddgs` is the successor of `duckduckgo-search` and
  is rapidly iterated; a major-version cap is the safe
  middle ground between reproducibility and bug-fix flow.
- Decision 4: `HF_TOKEN` is opt-in (empty by default) — the
  FastEmbed BM25 model is public, so authentication is not
  required for the system to work.  Operators with rate-limit
  issues can fill in the token without a code change.

## Impact
- `src/app/agent/nodes/generate_image.py` — drop
  `response_format="b64_json"`, add `_download_dalle_image`,
  read URL + bytes from the injected `httpx_client`.
- `src/app/api/chat.py` — create `httpx.AsyncClient()` per
  request and inject it into the configurable dict; close it
  in the request lifecycle.
- `pyproject.toml` — swap `duckduckgo-search==8.1.1` for
  `ddgs>=9.0.0,<10.0.0`.
- `uv.lock` — regenerated by `uv lock`.
- `.env.example` — new `HF_TOKEN=` row under a new
  `Hugging Face` section.
- `README.md` — short note in section 3 about `HF_TOKEN`
  being optional.
- `tests/unit/agent/nodes/test_generate_image.py` — update
  fixtures to inject an `httpx_client`, add URL-download path
  tests, add DALL-E-call-args regression guard
  (no `response_format`).
- `tests/unit/agent/subagents/trend_scout/test_tools.py` —
  new assertion that `_ddg` is not `None`.
- `docker` — no Dockerfile change needed; the rebuild only
  refreshes the dependency layer via `uv lock`.
