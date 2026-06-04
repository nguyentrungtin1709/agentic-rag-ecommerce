# Saleor App and Webhook Setup

This guide covers how to create a Saleor app token and register product webhooks
for the POD Stylist integration.

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

Call `webhookCreate`:

```graphql
mutation {
  webhookCreate(
    input: {
      name: "POD Stylist Product Events"
      app: "<app-id>"
      targetUrl: "http://host.docker.internal:<app-port>/webhooks/saleor"
      asyncEvents: [PRODUCT_CREATED, PRODUCT_UPDATED, PRODUCT_DELETED]
      isActive: true
    }
  ) {
    webhook {
      id
      name
      targetUrl
      asyncEvents {
        eventType
        name
      }
      isActive
    }
    errors {
      field
      message
      code
    }
  }
}
```

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
