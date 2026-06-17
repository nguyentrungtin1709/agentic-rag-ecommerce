# Switch image generation from DALL-E 3 to gpt-image-2

**Version**: 16.1.0
**Date**: 2026-06-17
**Status**: Planned

## What
Replace the DALL-E 3 URL-download path shipped in 16.0.0 with the
OpenAI gpt-image family (default `gpt-image-2`).  The gpt-image
endpoints return base64 payloads inline, so the per-request
`httpx.AsyncClient` injection, the URL download, and the
`response_format` workaround all go away.  The image model is now
read from a new `settings.image_generation_model` field
(env var `IMAGE_GENERATION_MODEL`).

## Why
After deploying 16.0.0 in production, every image request still
fails with:

```
BadRequestError: Error code: 400 - {'error': {'message':
"The model 'dall-e-3' does not exist.", 'type':
'invalid_request_error', 'param': None, 'code': 'invalid_value'}}
```

The OpenAI API key shipped with this project does not have access
to the DALL-E 3 endpoint (it returns 400 `invalid_value` instead
of 401/403).  A real-API probe (`/tmp/test_openai_models.py`)
confirms the available models on this account:

| Model | Result |
|---|---|
| `gpt-image-2` | OK (1.83 MB b64) |
| `gpt-image-1.5` | OK (684 KB b64) |
| `gpt-image-1` | OK (1.78 MB b64) |
| `gpt-image-1-mini` | OK (1.78 MB b64) |
| `dall-e-3` | ERR 400 `invalid_value` |
| `dall-e-2` | ERR 400 `invalid_value` |

The gpt-image family has a different response contract: the API
**always** returns base64 in `b64_json` (no signed URL, no
`response_format` parameter at all — the parameter is silently
ignored if sent).  This makes the 16.0.0 URL-download path
unnecessary: we just decode the base64 payload inline.

## How

### Config
- Add `image_generation_model: str = Field(default="gpt-image-2")`
  to `src/app/config.py` under `Agent Behavior`.
- Add `IMAGE_GENERATION_MODEL=gpt-image-2` to `.env` and
  `.env.example` next to `IMAGE_DAILY_LIMIT`.

### `src/app/agent/nodes/generate_image.py`
- Remove the `_DALLE_MODEL` constant; read the model from
  `settings.image_generation_model`.
- Remove `_download_dalle_image`, the `http_client` configurable
  read, and the related log events / tests.
- Replace the `response.data[0].url` path with a base64 path:
  `b64 = response.data[0].b64_json` then
  `image_bytes = base64.b64decode(b64, validate=True)`.
- Add a new `_decode_b64_payload` helper with a `validate=True`
  guard so a corrupted payload raises `binascii.Error` instead
  of silently truncating.
- Rename log events that said `dalle_*` to `model_*` and add the
  `model=...` field to each so production logs can attribute
  failures to the active model.
- Restore `_IMAGE_*` constants (`_IMAGE_SIZE = "1024x1024"`,
  `_IMAGE_N = 1`, `_IMAGE_CONTENT_TYPE = "image/png"`) — the
  previous DALL-E-prefixed names are gone with the URL path.

### `src/app/api/chat.py`
- Remove the `import httpx` and the per-request
  `httpx.AsyncClient` construction / injection / close.
- Drop `"http_client"` from the `config["configurable"]` dict
  and the `finally`-block close call.
- Update the docstring's "Path A injection" bullet and the
  background-task `finally` comment to match.

### Tests
- `tests/unit/agent/nodes/test_generate_image.py` —
  - Drop the `_make_http_client_stub` helper and the
    `http_client` key from `_make_config`.
  - Add `_FAKE_B64` (base64 of `_FAKE_PNG`) and switch every
    `MagicMock(url=..., b64_json=None)` to
    `MagicMock(b64_json=_FAKE_B64)`.
  - Replace `dalle_no_url` / `dalle_download_failure` tests
    with `model_no_payload` and `b64_decode_failure`.
  - Update model asserts from `"dall-e-3"` to `"gpt-image-2"`.
  - Add a new test pinning `settings.image_generation_model` →
    `openai.images.generate` so future env-var swaps are caught.
- `tests/unit/api/test_threads.py` — `model="dall-e-3"` →
  `"gpt-image-2"` in the `GeneratedImage` fixture.
- `tests/integration/test_cleanup.py` — SQL fixture
  `'dall-e-3'` → `'gpt-image-2'`.
- `tests/unit/repositories/test_image_repo.py` — every
  `"model": "dall-e-3"` fixture → `"gpt-image-2"`.

### Documentation
- `README.md` — replace the two "OpenAI DALL-E" mentions in
  the main feature section and the graph-flow comment with
  "OpenAI gpt-image" and note that the model is configurable
  via `IMAGE_GENERATION_MODEL`.
- `src/app/repositories/image_repo.py` and
  `src/app/models/image.py` — update the example model name in
  the docstring from `"dall-e-3"` to `"gpt-image-2"`.

## Key Decisions
- Decision 1: Default to `gpt-image-2` — highest quality in the
  family that this account can reach.  Operators who want a
  cheaper model flip `IMAGE_GENERATION_MODEL=gpt-image-1-mini`
  in the env without a code change.
- Decision 2: Revert the URL-download path entirely (16.0.0's
  `http_client` injection, `_download_dalle_image`,
  `httpx` import) — the gpt-image family always returns
  base64, so the URL path is dead code that only adds a
  per-request dependency.
- Decision 3: `base64.b64decode(..., validate=True)` — the
  default `validate=False` would silently skip / truncate
  invalid characters, masking API regressions with a vague
  "S3 upload failed" log.
- Decision 4: Rename log events `dalle_call_failed` /
  `dalle_no_url` / `dalle_download_failed` →
  `model_call_failed` / `model_no_payload` /
  `b64_decode_failed` — the old names lied about which model
  was actually used.

## Impact
- `src/app/config.py` — new `image_generation_model` field.
- `src/app/agent/nodes/generate_image.py` — full rewrite of
  the model-call and bytes-acquisition path.
- `src/app/api/chat.py` — `httpx` removed; configurable
  injection no longer carries `http_client`.
- `.env`, `.env.example` — `IMAGE_GENERATION_MODEL=gpt-image-2`
  added.
- `README.md` — two DALL-E references replaced with gpt-image.
- 4 test files updated (one full rewrite, three single-line
  fixtures).
- 2 docstrings updated (`image_repo.py`, `image.py`).
- No DB migration needed — the `model` column is free-form
  text, so legacy `dall-e-3` rows are harmless and the new
  `gpt-image-2` rows coexist.
- No Dockerfile change — dependency layer untouched.

## Supersedes
- The 16.0.0 "DALL-E 3 URL + httpx download" fix is superseded
  by this version.  The other 16.0.0 fixes (`ddgs` swap,
  `HF_TOKEN` opt-in) are unaffected and remain in effect.
