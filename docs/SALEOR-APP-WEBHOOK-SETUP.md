# Saleor App and Webhook Setup

This guide covers how to create a Saleor app token and register product webhooks
for the POD Stylist integration.

> **Fast path (recommended)**: skip Steps 1-3 and run
> `uv run python scripts/setup_saleor_webhook.py --help` for a one-shot
> CLI that automates all three steps.  Example:
>
> ```bash
> uv run python scripts/setup_saleor_webhook.py \
>     --email admin@example.com \
>     --password '<admin-password>' \
>     --target-url http://host.docker.internal:8080/webhooks/saleor
> ```
>
> The script prints a ready-to-paste `.env` block at the end.  Read
> Steps 1-3 below only if you need to do the setup manually (e.g. to
> understand the GraphQL contract or to integrate the steps into
> another tool).

---

## Prerequisites

- Saleor instance running and accessible (default: `http://localhost:8000`)
- A staff account with `MANAGE_APPS` and `MANAGE_PRODUCTS` permissions
- Access to the Saleor GraphQL Playground at `http://localhost:8000/graphql/`

---

## Step 1 — Obtain a Staff Token

Call `tokenCreate` with no `Authorization` header (public endpoint):

```graphql
mutation {
  tokenCreate(email: "<staff-email>", password: "<staff-password>") {
    token
    refreshToken
    user {
      id
      email
      isStaff
      userPermissions {
        code
        name
      }
    }
  }
}
```

Copy the `token` value from the response. Set it as the `Authorization` header for
all subsequent requests:

```json
{
  "Authorization": "Bearer <staff-token>"
}
```

---

## Step 2 — Create the App

Call `appCreate` with the staff token header from Step 1:

```graphql
mutation {
  appCreate(
    input: {
      name: "POD Stylist"
      permissions: [MANAGE_PRODUCTS]
    }
  ) {
    authToken
    errors {
      field
      code
      message
      permissions
    }
    app {
      id
      name
    }
  }
}
```

The response contains:

```json
{
  "data": {
    "appCreate": {
      "authToken": "<app-auth-token>",
      "errors": [],
      "app": {
        "id": "<app-id>",
        "name": "POD Stylist"
      }
    }
  }
}
```

**Important**: `authToken` is shown only once. Copy it immediately and set it in `.env`:

```env
SALEOR_APP_TOKEN=<app-auth-token>
```

---

## Step 3 — Register the Webhook

Switch the `Authorization` header to the **app token** from Step 2:

```json
{
  "Authorization": "Bearer <app-auth-token>"
}
```

Call `webhookCreate`. The `query` field is a **GraphQL subscription query** that defines exactly which fields the webhook payload will contain — Saleor only includes the fields you explicitly ask for in the subscription. The query below covers every field the POD Stylist indexer reads (name, description, category, collections, pricing, channel listings, thumbnail); if a field is missing from the query, Saleor will send `null` and the indexer will treat it as missing.

> **Why a variable?** The multi-line subscription query is sent as a string value for the `query` field. GraphQL's `"""..."""` block-string syntax is fragile across clients (some GraphQL Playgrounds/SDKs escape the inner `"""` as a closing delimiter) and may not survive prettifiers. Passing the query through a `$query: String!` variable is the canonical GraphQL pattern and works in every client and against every server, including Saleor.

**The subscription query** (paste this as the value of the `query` variable):

```graphql
fragment ProductWebhookFields on Product {
  id
  name
  slug
  description
  category { name }
  collections { name }
  pricing {
    priceRange {
      start { gross { amount currency } }
      stop  { gross { amount currency } }
    }
  }
  thumbnail(size: 512, format: WEBP) { url }
  channelListings {
    channel { slug }
    pricing {
      priceRange {
        start { gross { amount currency } }
        stop  { gross { amount currency } }
      }
    }
  }
}

subscription {
  event {
    ... on ProductCreated { product { ...ProductWebhookFields } }
    ... on ProductUpdated { product { ...ProductWebhookFields } }
    ... on ProductDeleted { product { id } }
  }
}
```

> **Where is the event type?** The `event` field is **not** in the
> body. With a `subscription { event { ... } }` root, Saleor's
> `generate_payload_from_subscription` (see
> `saleor/graphql/webhook/subscription_payload.py`,
> `_process_payload_instance`) emits the inner selection set
> directly — no `data` wrapper, no `event` key. The event name is
> sent in the `Saleor-Event` HTTP header (the deprecated
> `X-Saleor-Event` is the v3 → v4 fallback). The POD Stylist
> endpoint reads it from the request headers, not from the body.
> See https://docs.saleor.io/developer/extending/webhooks/overview
> for the full header list.
>
> **Wire value of the header is lowercase.** Saleor's
> `WebhookEventAsyncType` enum stores event names as
> `product_created` / `product_updated` / `product_deleted` (see
> `saleor/webhook/event_types.py` in the running 3.23 image). The
> endpoint upper-cases the value at the boundary so the rest of
> the app works in the conventional `PRODUCT_UPDATED` form.

**The mutation** (paste this in the GraphQL Playground, then fill in the `query` variable in the "Variables" panel on the left):

```graphql
mutation CreateWebhook($input: WebhookCreateInput!) {
  webhookCreate(input: $input) {
    webhook {
      id
      name
      targetUrl
      asyncEvents { eventType name }
      isActive
    }
    errors { field message code }
  }
}
```

**Variables** (replace `<app-id>` and `<app-port>` with the values from Steps 1 and 2; the `query` field is the subscription query above):

```json
{
  "input": {
    "name": "POD Stylist Product Events",
    "app": "<app-id>",
    "targetUrl": "http://host.docker.internal:<app-port>/webhooks/saleor",
    "asyncEvents": ["PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"],
    "isActive": true,
    "query": "<paste the subscription query above, with literal newlines>"
  }
}
```

**Equivalent cURL (no Playground needed)** — the multi-line query is sent as a JSON string (this is what worked when tested against Saleor 3.x):

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <app-auth-token>" \
  -d @- <<'JSON'
{
  "query": "mutation CreateWebhook($input: WebhookCreateInput!) { webhookCreate(input: $input) { webhook { id name targetUrl isActive asyncEvents { eventType } } errors { field message code } } }",
  "variables": {
    "input": {
      "name": "POD Stylist Product Events",
      "app": "<app-id>",
      "targetUrl": "http://host.docker.internal:<app-port>/webhooks/saleor",
      "asyncEvents": ["PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"],
      "isActive": true,
      "query": "fragment ProductWebhookFields on Product { id name slug description category { name } collections { name } pricing { priceRange { start { gross { amount currency } } stop { gross { amount currency } } } } thumbnail(size: 512, format: WEBP) { url } channelListings { channel { slug } pricing { priceRange { start { gross { amount currency } } stop { gross { amount currency } } } } } } subscription { event { ... on ProductCreated { product { ...ProductWebhookFields } } ... on ProductUpdated { product { ...ProductWebhookFields } } ... on ProductDeleted { product { id } } } }"
    }
  }
}
JSON
```

**Why the query matters**: per the [Saleor Subscription Webhook docs](https://docs.saleor.io/developer/extending/webhooks/subscription-webhook-payloads), a subscription webhook sends **only** the fields explicitly listed in the subscription query. If `pricing` is omitted, the POD Stylist indexer will receive a product with no price data and the Qdrant point will be upserted with `price_min=0, price_max=0, currency="USD"`. The query above is the contract that keeps the indexer accurate.

**Field coverage in the query above**:

| Field | Why we need it |
|---|---|
| `id` | Deterministic Qdrant point id (UUID v5) — required for idempotency (NFR-013) |
| `name`, `slug`, `description`, `category.name`, `collections[].name` | Core metadata for `ProductPayload` and the embedding input |
| `pricing.priceRange.{start,stop}.gross.{amount,currency}` | Price range, currency — primary pricing path |
| `channelListings[].pricing.priceRange.{...}` | Per-channel pricing — used as a fallback if the top-level `pricing` is null |
| `thumbnail(size: 512, format: WEBP).url` | WebP thumbnail URL for storefront display |

**Defensive fallback**: if a future change accidentally drops a field from the query (or a null is sent for a specific product), the `process_webhook` task in the POD Stylist app will detect the missing pricing and refetch the canonical product from the Saleor GraphQL API before upserting. This is automatic — no manual intervention needed for transient schema mismatches.

A successful response looks like:

```json
{
  "data": {
    "webhookCreate": {
      "webhook": {
        "id": "<webhook-id>",
        "name": "POD Stylist Product Events",
        "targetUrl": "http://host.docker.internal:<app-port>/webhooks/saleor",
        "asyncEvents": [
          { "eventType": "PRODUCT_UPDATED", "name": "Product updated" },
          { "eventType": "PRODUCT_CREATED", "name": "Product created" },
          { "eventType": "PRODUCT_DELETED", "name": "Product deleted" }
        ],
        "isActive": true
      },
      "errors": []
    }
  }
}
```

---

## Step 4 — Generate a Webhook Secret

The webhook secret is used by this application to verify HMAC-SHA256 signatures
on incoming payloads. Generate a secure random value:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Set the output in `.env`:

```env
SALEOR_WEBHOOK_SECRET=<generated-64-char-hex>
```

---

## Step 5 — Final .env Values

```env
SALEOR_URL=http://localhost:8000
SALEOR_APP_TOKEN=<app-auth-token>
SALEOR_WEBHOOK_SECRET=<generated-64-char-hex>
```

---

## Notes on `targetUrl` for Local Development

Saleor runs inside Docker and cannot reach `localhost` on the host machine directly.

| Scenario | targetUrl |
|---|---|
| Saleor in Docker, app on host (Linux) | `http://host.docker.internal:<app-port>/webhooks/saleor` |
| Saleor in Docker, app on host (Linux, no `host.docker.internal`) | Use bridge gateway IP from `docker network inspect` |
| Both in same Docker Compose network | `http://<app-service-name>:<app-port>/webhooks/saleor` |
| Testing without a running app | Use https://webhook.site as a temporary target |

To enable `host.docker.internal` on Linux, add to the Saleor `api` service in its
`docker-compose.yml`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

---

## Step 6 — Test Users (Integration Tests)

The integration test suite needs **two authenticated users** so the
`/api/v1/threads` endpoint can be exercised end-to-end with real
Saleor JWTs:

1. A regular **customer** account (`is_staff: false`) for the
   thread-owner path.
2. A **staff** account (`is_staff: true`) for the admin-only paths
   (`GET /admin/threads` once Phase 8 wires admin filters).

The credentials are loaded from environment variables so they are
never hard-coded in the test source.  Put the real values in
`.env.local` (gitignored) and reference them in `tests/integration/conftest.py`.

### 6.1 — Prerequisites

A Saleor instance is running and an admin token is available.
Sign in once with the admin credentials you configured when
bootstrapping Saleor (the example below uses
`admin@gmail.com` / `Admin##123`):

```bash
ADMIN_JWT=$(curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { tokenCreate(email: \"admin@gmail.com\", password: \"Admin##123\") { token } }"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['tokenCreate']['token'])")
```

Also list the available channels — `accountRegister` requires a
`channel` slug from this query:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"query": "{ channels { slug } }"}'
# => { "data": { "channels": [ { "slug": "default-channel" }, ... ] } }
```

Mailpit runs alongside Saleor and captures every email Saleor
sends — the set-password and confirmation links land there at
`http://localhost:8025`.

### 6.2 — Create the Customer Account

Saleor's `accountRegister` mutation creates a non-staff user and
fires an account-confirmation email.  The mutation **does not
return a usable token until the email is confirmed**.

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"query": "mutation Reg($input: AccountRegisterInput!) { accountRegister(input: $input) { user { id email isStaff } errors { field message code } } }", "variables": {"input": {"email": "testuser.podstylist@gmail.com", "password": "TestUser##123", "redirectUrl": "http://localhost:3000", "channel": "default-channel"}}}'
```

Open Mailpit at <http://localhost:8025> and copy the
`token=...` query parameter from the
`Account confirmation e-mail`.  Confirm the account:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { confirmAccount(email: \"testuser.podstylist@gmail.com\", token: \"<token-from-mailpit>\") { user { id email isConfirmed } errors { field message code } } }"}'
```

Verify the customer can mint a JWT:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { tokenCreate(email: \"testuser.podstylist@gmail.com\", password: \"TestUser##123\") { token user { isStaff } } }"}'
```

### 6.3 — Create the Staff Account

`staffCreate` is admin-only and **does not accept a password** —
Saleor emails the new staff member a set-password link.  The
mutation requires a permission group ID.  Use the `Full Access`
group (`R3JvdXA6MQ==`) for a superuser, or any narrower group
to scope permissions:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"query": "mutation { staffCreate(input: {email: \"teststaff.podstylist@gmail.com\", firstName: \"Test\", lastName: \"Staff\", isActive: true, redirectUrl: \"http://localhost:3000\", addGroups: [\"R3JvdXA6MQ==\"]}) { user { id email isStaff } errors { field message code } } }"}'
```

Open Mailpit and copy the `token=...` from the
`You're invited to join Saleor` email.  Set the password:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { setPassword(email: \"teststaff.podstylist@gmail.com\", password: \"TestStaff##123\", token: \"<token-from-mailpit>\") { user { isStaff } errors { field message code } } }"}'
```

Verify the staff user mints a JWT with `is_staff: true`:

```bash
curl -sS -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { tokenCreate(email: \"teststaff.podstylist@gmail.com\", password: \"TestStaff##123\") { token user { isStaff } } }"}'
```

### 6.4 — Persist the Credentials in `.env.local`

The integration tests read these env vars (see
`tests/integration/conftest.py`):

```env
# Admin (Step 6.1) — used by fixtures to mint test users on the fly
SALEOR_ADMIN_EMAIL=admin@gmail.com
SALEOR_ADMIN_PASSWORD=Admin##123

# Test customer (Step 6.2)
SALEOR_TEST_USER_EMAIL=testuser.podstylist@gmail.com
SALEOR_TEST_USER_PASSWORD=TestUser##123

# Test staff (Step 6.3)
SALEOR_TEST_STAFF_EMAIL=teststaff.podstylist@gmail.com
SALEOR_TEST_STAFF_PASSWORD=TestStaff##123
```

> `.env.local` is git-ignored (see `.gitignore`: `.env` / `.env.*` are
> excluded except `.env.example` and `.env.template`).  The corresponding
> placeholders live in `.env.example` so other contributors know which
> variables to set.
