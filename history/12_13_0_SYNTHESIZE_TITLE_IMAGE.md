# 12.13.0 — Synthesize, Title Generation, Image Generation

## Status

Accepted — 2026-06-16

## Context

Phase 12 ships the two terminal main-pipeline nodes of the POD
Stylist agent graph:

- **`synthesize`** (`ResponseGeneratorNode`) — assembles the
  final user-facing response, streams tokens, and emits the
  terminal `products` / `done` SSE events.
- **`generate_title`** (`TitleGenerationNode`) — auto-names the
  thread after the first complete exchange, persists the title,
  invalidates the Valkey thread-list cache, and emits a
  `thread_title` SSE event.

Phase 13 ships the parallel image-generation branch:

- **`generate_image`** (`ImageGenerationNode`) — generates an
  image with DALL-E, uploads to S3, persists a `generated_images`
  row, increments the Valkey daily quota, and emits `image_ready`
  or `image_failed` SSE events.

All three nodes are real implementations of stubs that have been
in the codebase since Phase 1 (`graph.py` already wires them into
the topology). They share a single contract: an
`asyncio.Queue` injected via `config["configurable"]["sse_queue"]`
that they push typed events onto as the chat handler drains them
to the client.

Phase 12 + 13 ship together because they all sit on the same
SSE event-bus contract — splitting them would force a partial
deployment where `synthesize` can run but no one is listening
for its events.

This phase also fixes a latent bug discovered while implementing
F8.x: the existing `get_thread_history` handler in
`api/threads.py` was looking up `generated_images` by AI-message
IDs in `_format_message`, but the storage convention (FR-051)
stores them by the *human*-message ID. The fix (Commit 0, F8.1-F8.5)
preserves storage and corrects the response assembly.

## Phase 12 decisions (D12.1 - D12.11)

### D12.1 — Intent-to-prompt dispatch

`synthesize` selects one of four system prompts based on
`state["intent"]`:

- `sufficient` → `synthesize_sufficient_system.md`
- `clarification_needed` → `synthesize_clarification_system.md`
- `out_of_scope` → `synthesize_out_of_scope_system.md`
- `fallback` (or any unknown value) → `synthesize_fallback_system.md`

A defensive `dict.get(intent, fallback)` mapping guarantees
that an unknown or `None` intent always picks the safe fallback
prompt — the worst case is a polite, on-brand response, never
a stack trace.

### D12.2 — Dynamic context block

Each prompt is augmented with a `## Conversation Context` block
carrying the user profile (JSON-dumped), retrieved products
(name / category / price one-line-per-item), trend summary, and
prior conversation summary.  Sections are omitted when their
source is empty so the prompt never carries `"None"`
placeholders that could confuse the LLM.

### D12.3 — Streaming + SSE emission

`synthesize` uses `ChatOpenAI.astream(messages)` and emits one
`token` event per non-empty `AIMessageChunk`.  Empty chunks
(an OpenAI quirk on intermediate frames) are dropped silently.
Token usage is accumulated across chunks and forwarded in the
terminal `done` event for the dev tools cost panel.

### D12.4 — `products` event

When `retrieved_products` is non-empty, one `products` event
follows the stream, carrying the full `ProductItem` payload
(truncated description to 500 chars to stay under the typical
4 KB SSE frame limit).

### D12.5 — `done` event

The terminal `done` event carries `run_id` (uuid4), `thread_id`,
`intent`, and a `UsagePayload(prompt_tokens, completion_tokens,
cost_usd)`.  The `cost_usd` field is `0.0` for now — Phase 15
will fill in pricing.

### D12.6 — No try/except around the LLM stream

If the LLM call fails partway through, the exception bubbles to
the graph runtime.  The Phase 14 chat endpoint will translate it
to a single `error` SSE event and close the stream.  We chose
NOT to wrap the stream in a try/except because partial output
that wraps a synthetic apology is more confusing than a clean
error frame.

### D12.7 — No Pydantic output parser

The synthesize prompts ask for free-form prose, not structured
output.  A Pydantic parser would conflict with the brand-tone
instructions in the system prompts.  Validation happens at the
SSE layer (typed `ChatChunk` / `ProductsPayload` / `DonePayload`)
instead.

### D12.8 — `title_generated` short-circuit

`generate_title` reads `state["title_generated"]` first.  The
parallel branch runs on every turn, but this guard turns
subsequent invocations into a single SELECT-only no-op.

### D12.9 — `first_user_message` is the title seed

`state["first_user_message"]` is populated at the API boundary
on the first turn; the title node reads it but never falls back
to the *current* turn's user message.  This is intentional: the
title should reflect what the thread is *about*, not what the
user just asked.

### D12.10 — LLM timeout / API error returns `{}`

The LLM call has a 10-second `asyncio.wait_for` ceiling.  Any
exception (timeout, rate limit, 5xx) is logged and the node
returns `{}` — the attempt counter is already incremented, so
the next turn retries.  After
`settings.title_generation_max_attempts` (default 3) the
truncation path takes over.

### D12.11 — Sanitisation

The LLM output is sanitised: trim whitespace, drop wrapping
quotes (some models wrap titles in `"..."` literally), keep
only the first line, hard-cap at 100 chars.  An empty result
triggers the truncation fallback even on a "successful" LLM
call.

## Phase 13 decisions (D13.1 - D13.10)

### D13.1 — Guard 1: `generate_image` flag

Primary skip path.  Most chat turns are not image turns; this
guard short-circuits with `{}` and zero I/O.

### D13.2 — Guard 2: `image_prompt` presence

No fallback to `first_user_message` per user-confirmed decision.
The image-generation node only fires when the TrendScout
subagent produced a real `image_prompt` — not every chat
request deserves an image.

### D13.3 — Valkey daily quota pre-flight

Key: `image_quota:{user_id}:{date.today().isoformat()}`,
24h TTL.  When the count is `>= settings.image_daily_limit`
(default 10), emit `image_failed {reason: "rate_limit_exceeded"}`
and return `{}`.  No DALL-E call, no DB row.

### D13.4 — DALL-E with `response_format="b64_json"`

We use `b64_json` (not `url`) because decoding the base64
payload avoids a second HTTP call to download the image
bytes — the public URL is valid for ~1h and would force the
chat handler to complete the upload in that window.  Trade-off:
`+33%` payload size (the b64 string is in memory briefly) for a
2-call reduction per image; this is the right call at 10
images/user/day.

### D13.5 — S3 upload

`S3Service.aupload_image(user_id, thread_id, timestamp,
image_bytes, content_type="image/png")` returns the public HTTPS
URL.  Key pattern: `images/{user_id}/{thread_id}/{timestamp}.png`.

### D13.6 — DB row

`ImageRepository.create(thread_id, user_id, prompt, s3_key,
s3_url, model="dall-e-3", request_message_id=<last
HumanMessage.id>)`.  The `request_message_id` is the
HumanMessage ID (FR-051, F8.1) — at image-creation time the
synthesised AIMessage does not yet exist because `synthesize`
runs in parallel.

### D13.7 — Quota increment AFTER S3 + DB

The Valkey increment is the *last* step.  Incrementing first
and failing the upload would block a user from re-trying in the
same day even though the image was never generated — a strict
order matters.

### D13.8 — `image_ready` SSE

Terminal success event.  Carries `url` (the S3 public URL) and
`prompt` (the original `image_prompt`) so the frontend can
display both the image and a regenerate-from-prompt affordance.

### D13.9 — `image_failed` SSE

Any exception from `images.generate`, `aupload_image`, or
`ImageRepository.create` emits
`image_failed {reason: "generation_failed"}` and returns `{}`.
No state mutation, no quota increment.  S3 orphans are reclaimed
by the Phase 10 cleanup task at `thread_expiry_days`.

### D13.10 — Return shape

Success: `{"image_url": s3_url, "image_prompt": prompt}`.
Any failure: `{}`.  The LangGraph reducer persists the URL
into the checkpoint so the next turn can reference it
(e.g. for the `image_url` field on subsequent `state`
inspections).

## Cross-cutting (DI.X1 - DI.X3)

### DI.X1 — `sse_queue` is always `config["configurable"]["sse_queue"]`

The chat handler in Phase 14 builds a fresh `asyncio.Queue` per
request and threads it into the graph.  Nodes never construct
their own queue.  When the queue is `None` (unit tests that
exercise only the no-op paths), the `emit_sse` helper silently
no-ops.

### DI.X2 — Shared services injected via `config["configurable"]`

`openai_client`, `s3_service`, `valkey_service` are read from
`config["configurable"]` when present.  In production the chat
handler injects the `app.state` singletons; in tests the fixture
injects mocks or `None`.  When a service is `None`, the node
either skips that step (Valkey quota) or raises a clear error
(DALL-E, S3 — which the test fixture never exercises without
injection).

### DI.X3 — Synthesize and generate_title do NOT need service injection

They use `ChatOpenAI(model=...)` per-call (instantiated from
`settings`), so the `openai_client` is not consumed.  Only
`generate_image` requires the three services above.

## History image-attachment fix (F8.1 - F8.5)

The existing `get_thread_history` handler in
`api/threads.py` had a latent bug: it queried images by
HumanMessage IDs but attached them to AIMessages in
`_format_message` by looking up `images_by_msg[ai_message_id]`
— a key that never exists in the map.  No image ever reached
the client.

### F8.1 — Storage invariant (unchanged)

`generated_images.request_message_id` stores the **HumanMessage
ID** of the turn that triggered generation.  The DALL-E call
runs in parallel with `synthesize` (which has not yet appended
its AIMessage to `state["messages"]`), so the AIMessage ID does
not exist at image-creation time — this is the reason for the
HumanMessage-based convention.

### F8.2 — API mapping rule (NEW)

The handler now builds an `images_by_ai` map by walking the page
in order, tracking the most recently seen HumanMessage ID, and
attaching that human's images to the next AIMessage.

### F8.3 — Implementation

`images_by_human = await image_repo.list_by_message_ids(human_ids)`
remains unchanged.  The new code walks `page`, tracks
`last_human_id`, and on each AI message, attaches
`images_by_human.get(last_human_id, [])` to `images_by_ai[msg_id]`.
`_format_message` then uses `images_by_ai[msg_id]` instead of
`images_by_msg[msg_id]`.

### F8.4 — Edge case: image attached to multiple AI messages

If the same HumanMessage's image is referenced from a follow-up
turn (e.g. user says "regenerate that image"), the API walks
the page from the start, so only the first subsequent AIMessage
carries the image.  This matches the current frontend rendering
which shows the image once per turn.

### F8.5 — Test coverage

A new test `test_history_attaches_image_to_following_ai_message`
walks a synthetic page `h1 -> a1 -> a2 -> h2 -> a3` and asserts
that the image under `h1` surfaces on `a1`, not on `h1` or
`a2`.

## Phase 14 carry-overs

- **SSE event-bus contract** — the 7 event types (`token`,
  `products`, `image_ready`, `image_failed`, `thread_title`,
  `done`, `error`) are locked here.  Phase 14 must serialise
  them as `event: <type>\ndata: <json>\n\n` frames.
- **`sse_queue` location** — `config["configurable"]["sse_queue"]`
  is the single source.  Phase 14 will inject a fresh
  `asyncio.Queue(maxsize=...)` per request.
- **Resource injection pattern** — Phase 14 reads the
  `app.state` singletons and threads them through
  `config["configurable"]`.  The unit-test fixture pattern
  (DI.X2) carries over unchanged.

## Follow-ups

- **DALL-E model upgrade path** — the model name `"dall-e-3"`
  is hard-coded as a constant.  When a newer model becomes
  available, this is the single place to change.
- **Image regeneration UX** — the current contract allows the
  user to send a new turn with the same `image_prompt`; the
  image node runs again and overwrites the URL.  The frontend
  should consider a "regenerate" affordance that surfaces this.
- **Multi-image support** — currently `n=1` and the SSE event
  carries one image.  When the product spec calls for a gallery
  (3-5 images per turn), `n` will need to grow and the
  `ProductsPayload` shape will need to be re-discussed.
- **Cost pricing** — `done.cost_usd` is hard-coded to `0.0`.
  Phase 15 will wire in the OpenAI pricing table.
