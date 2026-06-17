# Webhook Handling (Per-Product Event Pipeline)

**Version**: 7.0.0
**Date**: 2026-06-13
**Status**: Approved

## What
Wire `POST /webhooks/saleor` to the existing `ProductIndexer` so that Saleor
product lifecycle events (`PRODUCT_CREATED`, `PRODUCT_UPDATED`,
`PRODUCT_DELETED`) reach Qdrant in real time without any operator action.
Replace the two stubs (`receive_saleor_webhook` returns `{"status": "accepted"}`
without dispatching; `process_webhook` is a `{"status": "stub"}` placeholder)
with a real endpoint and a real Celery task. Extract the transient-error
classifier that was duplicated in `process_batch` into a shared
`app/utils/transient.py` module.

## Why
- Phases 1-6 left `ProductIndexer.upsert_product` and
  `ProductIndexer.delete_product` ready to use, but nothing calls them on
  the webhook path. The only way to populate Qdrant today is a manual
  `POST /admin/reindex`, which is operator-driven and not real time.
- Saleor already sends the events — HMAC verification passes — but the
  payload is discarded. Closing this loop is required for the system to
  stay synchronized with Saleor in production (NFR-013 idempotency also
  requires the webhook path, not just reindex).
- `process_batch` and the soon-to-exist `process_webhook` both need the
  same transient-error whitelist. Duplicating it across two files is a
  known maintenance hazard (Phase 6's `_is_transient` is a 7-class
  whitelist; future tweaks in one place silently desync from the other).
  Two callers is the inflection point where extraction stops being YAGNI.

## How
- **Schema (`schemas/webhook.py`)** — replace the generic
  `SaleorWebhookPayload(event, payload)` with the real Saleor shape:
  `class SaleorWebhookPayload(event: Literal["PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"], data: ProductDataWrapper)`.
  Nested Pydantic models (`Thumbnail`, `Category`, `Collection`,
  `ChannelListing`) cover the fields the indexer needs;
  `ProductObject` uses `model_config = ConfigDict(extra="allow")` so
  future Saleor fields pass through to `ProductIndexer` without
  validation failure.
- **Endpoint (`api/webhooks.py`)** — after HMAC verify, validate the raw
  body with `SaleorWebhookPayload.model_validate_json(...)`. On
  `ValidationError` return 400 (Saleor does not consume the error body,
  but the 4xx class signals a real failure to the upstream retry logic).
  Extract `product_id = payload.data.object.id`, dispatch
  `process_webhook.delay(event_type, product_id, product_data)`,
  return `{"status": "accepted", "event_type": ...}` in < 200 ms
  (NFR-003). Keep `@_limiter.exempt` (FR-094).
- **Task (`tasks/process_webhook.py`)** — decorator is
  `bind=True, max_retries=3, default_retry_delay=5, retry_backoff=True,
  retry_backoff_max=60, retry_jitter=True, time_limit=60, queue="webhook",
  acks_late=True`. Sync wrapper uses `asyncio.run(_run(...))` to bridge
  to the async indexer. Dispatch on `event_type`:
  - `PRODUCT_CREATED` / `PRODUCT_UPDATED` →
    `ProductIndexer.upsert_product(product_data)` (FR-078, idempotent
    via UUID v5).
  - `PRODUCT_DELETED` → `ProductIndexer.delete_product(product_id)`
    (FR-079).
  - Other → `logger.warning("unknown_event_type", event_type=...)`,
    return `{"status": "ignored"}` (no enqueue, no retry).
  - Transient errors → `raise self.retry(exc=exc)` so Celery handles
    the retry with backoff and jitter.
  - Permanent errors → log error, return `{"status": "failed", "error_type", "error"}`
    (no re-raise so retries are not consumed by a bug).
  - Log INFO entry and exit with `product_id`, `event_type`,
    `duration_ms`, `qdrant_point_id` (NFR-024).
- **Shared util (`app/utils/transient.py`)** — new module. Single
  `is_transient(exc) -> bool` function with the 7-class whitelist
  (`openai.RateLimitError`, `openai.APITimeoutError`,
  `openai.InternalServerError`, `httpx.ConnectError`,
  `httpx.ReadTimeout`, `httpx.ConnectTimeout`,
  `qdrant_client.http.exceptions.UnexpectedResponse`). `process_batch`
  is refactored to import this; `process_webhook` uses it directly.
  Module-level docstring records the contract and points at the
  Phase 6 PR that introduced the original.

## Key Decisions
- **Decision 1**: Match Saleor's real format `{event, data.object}` —
  not a hybrid alias. Reasoning: the HMAC signature is computed over the
  raw bytes Saleor sends; an alias adds a translation step with no real
  win and creates a footgun for anyone debugging the wire format.
- **Decision 2**: `extra="allow"` on `ProductObject` — Saleor adds
  fields in minor versions (e.g. the `attributes` block). Strict
  validation would cause a webhook to fail after every Saleor upgrade.
  We capture what we need through the typed fields and forward the
  rest via `model_dump()`.
- **Decision 3**: 400 (not 422) for Pydantic `ValidationError` — the
  4xx class is what Saleor's retry logic consumes; the response body
  shape is irrelevant. 400 is the most common catch-all for malformed
  requests.
- **Decision 4**: `time_limit=60` (not 120) on `process_webhook` —
  OpenAI embeddings are typically < 2 s, the BM25 model is already
  cached after first use (Phase 6 shipped it), and Qdrant is warm. A
  60 s cap is a tight budget that still allows for a single transient
  cold-start; anything longer means we are retrying something we
  should not be.
- **Decision 5**: Extract `_is_transient` to `app/utils/transient.py`
  now — two callers is the inflection point. The whitelist grew to 7
  classes during Phase 6 testing; further additions (e.g.
  `openai.APIConnectionError`) will land in one place.
- **Decision 6**: Unknown event types return `{"status": "ignored"}`
  with HTTP 200, not 4xx — Saleor may add new event types in minor
  versions (e.g. `PRODUCT_TRANSLATED`). Rejecting them with 4xx would
  trigger Saleor's retry queue; ignoring them is the right operational
  default. The log line carries the diagnostic.
- **Decision 7**: `acks_late=True` on `process_webhook` — if the
  worker is killed mid-task, RabbitMQ redelivers the message. This
  matches the reindex worker pattern (Phase 6).

## Impact
- **New files**:
  - `src/app/utils/__init__.py` — package marker.
  - `src/app/utils/transient.py` — shared `is_transient`.
  - `tests/unit/tasks/test_process_webhook.py` — 12+ test cases.
  - `tests/integration/test_webhook_dispatch.py` — 9 test cases
    (endpoint behaviour + HMAC + latency + rate-limit exempt).
  - `tests/integration/test_webhook_e2e.py` — 5 E2E cases
    (Celery eager + Qdrant real, separate test collection).
  - `temp/phase-7-webhook-handling.md` — phase log.
- **Modified files**:
  - `src/app/schemas/webhook.py` — new Pydantic models.
  - `src/app/api/webhooks.py` — real endpoint body.
  - `src/app/tasks/process_webhook.py` — real task body.
  - `src/app/tasks/process_batch.py` — import `is_transient` from
    `app.utils.transient`; remove local definition.
  - `tests/unit/tasks/test_process_batch.py` — update the 8
    `_is_transient_*` test cases to import from the new location.
  - `docs/analysis/05-IMPLEMENTATION-PLAN.md` — Phase 7 row from `[PENDING]`
    to `[DONE]`.
- **No breaking API changes** to the webhook contract — Saleor
  continues to send `{event, data.object}` and continues to receive a
  2xx response on success.
- **No new external dependencies**.
- **No database migrations** — webhook processing does not need new
  tables; the `ingestion_jobs` / `ingestion_batches` tables remain the
  audit trail for the operator-driven reindex path.

## Validation
End-to-end smoke test (planned, see `temp/phase-7-webhook-handling.md`):

- `docker compose up -d` brings up the full stack.
- Unit tests: 12+ in `tests/unit/tasks/test_process_webhook.py`,
  8 refactored in `tests/unit/tasks/test_process_batch.py`.
- Integration tests: 9 endpoint cases in
  `tests/integration/test_webhook_dispatch.py`, 5 E2E cases in
  `tests/integration/test_webhook_e2e.py`.
- Coverage: webhook module 100%, transient util 100%, task module 95%+.
- `ruff check .` and `pyright` clean.
- `pre-commit run --all-files` clean.
- Existing `tests/integration/test_rate_limit.py` exempt assertion
  still passes (the `app.api.webhooks.receive_saleor_webhook` symbol
  is preserved, so the limiter's exempt set is unchanged).
- Manual webhook via `curl`: a `PRODUCT_CREATED` event with valid
  HMAC returns 200 in < 200 ms; the Qdrant `products` collection
  contains the new point within seconds; re-firing the same event
  leaves the count at 1 (FR-080).

## References
- FR-076, FR-077, FR-078, FR-079, FR-080, FR-086, FR-094, FR-100.
- NFR-003 (webhook latency < 200 ms), NFR-013 (idempotency),
  NFR-024 (log with `product_id`, `event_type`, duration).
- `docs/analysis/05-IMPLEMENTATION-PLAN.md` lines 410-443 (Phase 7 description).
- `docs/analysis/01-USE-CASE-ANALYSIS.md` UC-009 / UC-010 / UC-011
  (CREATE / UPDATE / DELETE flows), UC-S04 (HMAC).
- `docs/analysis/03-PROJECT-SCAFFOLD.md` lines 171, 180, 213
  (file-path expectations).
- Saleor docs:
  https://docs.saleor.io/developer/extending/webhooks/overview
  (payload top-level `{event, data.object}`, header `Saleor-Signature`,
  event name `UPPERCASE_WITH_UNDERSCORES`).
  https://docs.saleor.io/developer/extending/webhooks/subscription-webhook-payloads
  (subscription query mechanism; default async payload is
  `{"data": {"object": {...}}}`).
- `history/6_0_0_RAG_INGESTION.md` (Phase 6 ADR; the source of
  `_is_transient` and the async-bridge pattern).

---

## Live-wire fix — read event type from `Saleor-Event` header

**Date**: 2026-06-13
**Status**: Approved (post-merge correction)
**Supersedes**: Decision 1 above (the `SaleorWebhookPayload` shape;
the Celery task signature and the HMAC verifier are unchanged)

### Symptom
End-to-end testing against a live Saleor 3.23 instance showed the
endpoint returning HTTP 400 with `webhook_payload_validation_failed`
on every real event. The validation error reported `event` and `data`
fields missing; the actual `input_value` was
`{'product': {'id': '...', 'name': '...', 'pricing': {...}}}`.

### Root cause
The registered subscription query uses
`subscription { event { ... on ProductCreated { product { ... } } } }`.
Saleor's `generate_payload_from_subscription` (see
`saleor/graphql/webhook/subscription_payload.py`,
`_process_payload_instance`) branches on the subscription root:

```python
def process_single_payload(data):
    # When a subscription is defined with "event" as its root field,
    # the data is returned directly. This issue has been resolved for
    # subscriptions whose root field is not "event".
    if "event" == key:
        return data or {}
    return {"data": {key: data}}
```

With the `event { ... }` root, the function returns the inner
selection set **directly** — no `data` wrapper, no `event` key in
the body. Saleor separately sends the event type in the
`Saleor-Event` HTTP header (verified in
`saleor/webhook/transport/utils.py` and the *Webhook Overview* doc:
https://docs.saleor.io/developer/extending/webhooks/overview).

The Phase 7 schema (`SaleorWebhookPayload(event, data.object)`) was
written against the Saleor overview doc, which is correct for a
`data { ... }` root or a filterable subscription, but **not** for
the `event { ... }` root. We explored switching to `data { ... }`,
but introspection of the running 3.23 instance shows the `Subscription`
type has no `data` field at all — only `event` and the filterable
top-level fields. So that path is not available.

### Decision
Keep the `event { ... }` root. Read the event type from the
`Saleor-Event` header (with `X-Saleor-Event` fallback for the v3 →
v4 transition window). Drop the `event` and `data` fields from the
schema; the body is just the unwrapped product selection set.

The schema now mirrors what the wire actually carries:

```python
class SaleorWebhookPayload(BaseModel):
    product: ProductObject | None = None
```

The endpoint reads `event_type` from the request headers and
dispatches on it. A missing or unknown event header returns 400;
the unknown-event case in the Celery task is now a defensive
secondary check (the endpoint already filters).

### Code changes
- `src/app/schemas/webhook.py` — `SaleorWebhookPayload` is now
  `{product: ProductObject | None}` only. `ProductDataWrapper` is
  removed. `SaleorProductEvent` (the `Literal`) is kept for use at
  the endpoint boundary.
- `src/app/api/webhooks.py` — reads `event_type` from
  `request.headers.get("Saleor-Event")` (fallback
  `X-Saleor-Event`); rejects missing/unknown values with 400.
  Validates the body against the simplified `SaleorWebhookPayload`.
- `src/app/services/saleor_client.py` — docstring on
  `webhook_object_to_product_payload` updated to reflect that the
  helper receives the unwrapped product dict (the wrapper is
  stripped by the schema before dispatch).
- `tests/integration/test_webhook_dispatch.py` — `_payload()`
  helper now emits the unwrapped body. New `_post()` helper adds
  the `Saleor-Event` header. New tests:
  `test_webhook_missing_event_header_returns_400`,
  `test_webhook_unknown_event_header_returns_400`,
  `test_webhook_accepts_deprecated_x_saleor_event_header`.
- `tests/integration/test_webhook_e2e.py` — `_build_payload()`
  helper emits the unwrapped body. New `_post_webhook()` helper
  adds the `Saleor-Event` header.
- `docs/analysis/00-SALEOR-APP-WEBHOOK-SETUP.md` — Step 3 subscription query
  example retains the `event { ... }` root; a new note explains
  where the event type lives (header, not body).
- `scripts/setup_saleor_webhook.py` — the default subscription
  query string is unchanged (it was always `event { ... }`).

---

## Live-wire fix #2 — canonicalise lowercase wire event to uppercase, pass `secretKey` on create

**Date**: 2026-06-13
**Status**: Approved (post-merge correction)
**Builds on**: "Live-wire fix" above; the body shape and HMAC
verifier are unchanged.

### Symptom
After the first live-wire fix, the endpoint stopped returning
`webhook_payload_validation_failed` but every real Saleor event
returned 400 with `webhook_event_header_unknown`. The log line
showed `event_type: "product_updated"` (lowercase) being rejected
by the endpoint's `_KNOWN_EVENT_TYPES` set, which was hard-coded
to the uppercase `PRODUCT_UPDATED`. A second issue surfaced in the
same pass: the `setup_saleor_webhook.py` script's
`webhookCreate` mutation did not include a `secretKey` field, so
Saleor auto-generated a secret the script could not capture and
the printed `SALEOR_WEBHOOK_SECRET` did not match the value Saleor
used to sign — every request returned
`webhook_hmac_verification_failed` once the header issue was
fixed.

### Root cause
The literal value of the `WebhookEventAsyncType` enum in Saleor
3.23 (`saleor/webhook/event_types.py`) is lowercase:

```python
PRODUCT_CREATED = "product_created"
PRODUCT_UPDATED = "product_updated"
PRODUCT_DELETED = "product_deleted"
```

This value is forwarded verbatim into the `Saleor-Event` HTTP
header by `saleor/webhook/transport/utils.py`. The Saleor docs
write the event name in uppercase for readability, which is what
the endpoint was matching against. The docs are misleading on
this point.

The `secretKey` issue is a separate oversight in the
`webhookCreate` call: the script generated a random 64-char hex
secret, printed it, and never told Saleor about it. Saleor's
`webhookCreate` therefore used an auto-generated opaque secret we
could never read.

### Decision
1. Canonicalise the header value at the boundary — upper-case
   the wire value before checking the whitelist and before
   dispatching to the Celery task. The `SaleorProductEvent`
   `Literal` stays in the conventional uppercase form so the
   task, log keys, and tests keep working in `PRODUCT_UPDATED`.
2. Pass the generated `webhook_secret` to `webhookCreate` as
   `secretKey`. The mutation is `staff`-authored (the staff is
   the one with permission to create webhooks); binding the
   secret is the only way to make `SALEOR_WEBHOOK_SECRET`
   in `.env` match the value Saleor uses to sign.

### Code changes
- `src/app/api/webhooks.py` — read the header, upper-case it,
   then check the whitelist and dispatch with the canonical
   form. The `process_webhook.delay(...)` call, the structured
   log line, and the response body all carry the canonical
   uppercase event type.
- `src/app/schemas/webhook.py` — module docstring updated to
   document the wire case (lowercase) and the canonicalisation
   at the boundary. `SaleorProductEvent` stays uppercase.
- `tests/integration/test_webhook_dispatch.py` — new test
   `test_webhook_canonicalises_lowercase_wire_event_to_uppercase`
   sends the wire-case `product_updated` in the header and
   asserts both the response and the Celery task call receive
   the canonical `PRODUCT_UPDATED`.
- `tests/integration/test_webhook_e2e.py` — the three E2E
   tests now send the wire-case lowercase event types
   (`product_created`, `product_updated`, `product_deleted`)
   in the `Saleor-Event` header. Assertions still expect the
   canonical uppercase form in the response, mirroring the
   production path.
- `scripts/setup_saleor_webhook.py` —
   `step_webhook_create` now takes a `webhook_secret` kwarg
   and includes `secretKey: webhook_secret` in the
   `webhookCreate` input. The `webhook { secretKey }` field is
   also requested in the response so a future caller can
   verify Saleor stored the value we asked for. The main
   function passes the generated secret to the call.
- `docs/analysis/00-SALEOR-APP-WEBHOOK-SETUP.md` — note added in Step 3
   explaining the wire case is lowercase and the endpoint
   upper-cases at the boundary.

---

## Live-wire fix #3 — `ProductObject.name` must be optional for `PRODUCT_DELETED`

**Date**: 2026-06-13
**Status**: Approved (post-merge correction)
**Builds on**: the two fixes above; only the schema shape changes.

### Symptom
After the first two fixes, `PRODUCT_CREATED` and `PRODUCT_UPDATED`
flowed through to Qdrant correctly, but every real
`PRODUCT_DELETED` event returned 400 with
`webhook_payload_validation_failed`.  Pydantic reported
`product.name` `Field required [type=missing, input_value=
{'id': 'UHJvZHVjdDoxNjU='}]`.

### Root cause
The production subscription in
[`docs/analysis/00-SALEOR-APP-WEBHOOK-SETUP.md` Step 3] selects only
`product { id }` for the delete event:

```graphql
... on ProductDeleted { product { id } }
```

The body Saleor actually sends for a delete is
`{"product": {"id": "..."}}` — no `name`, no pricing, no
thumbnail.  `ProductObject` in `src/app/schemas/webhook.py`
required `name: str` (the only required field besides `id`),
so the schema rejected every real delete before dispatch.

The unit / integration tests missed this because the shared
`_payload()` test helper built the **upsert** body shape (with
`name`, `description`, `thumbnail`, …) for all three event types.
The "delete" tests were effectively testing what a delete would
look like with full data, not what Saleor actually sends.

### Decision
Make `ProductObject.name` optional.  Every other upsert-only
field (`description`, `slug`, `thumbnail`, `category`,
`collections`, `channelListings`, `media`) was already optional
because `ProductObject` uses `model_config = ConfigDict(extra=
"allow")` with explicit `None` defaults.  Only `id` and `name`
were required; making `name` optional brings the schema in line
with the actual wire shape.  A missing `name` on an upsert
event is not a problem for the indexer — the Celery task's
A2 strategy falls back to a canonical `SaleorClient.
fetch_product_by_id()` call when the payload is incomplete
(see `src/app/tasks/process_webhook.py`).

### Code changes
- `src/app/schemas/webhook.py` — `name: str` -> `name: str |
  None = None` on `ProductObject`.  Class docstring updated
  to record that only `id` is required and to point at the
  `ProductDeleted` selection in the setup doc as the reason.
- `tests/integration/test_webhook_dispatch.py` — new helper
  `_delete_payload(product_id)` that builds the wire-shape
  delete body (`{"product": {"id": "..."}}`).  New test
  `test_webhook_product_deleted_accepts_id_only_wire_shape`
  sends that body with the wire-case `product_deleted` header
  and asserts the endpoint accepts it.
- `tests/integration/test_webhook_e2e.py` — new helper
  `_build_delete_payload(product_id)` with the same shape.
  `test_e2e_product_deleted_removes_from_qdrant` uses it for
  the delete step so the E2E flow now exercises the
  production wire format end-to-end.
