"""One-shot Saleor app + webhook bootstrap.

Automates Steps 1, 2, 3 of ``docs/SALEOR-APP-WEBHOOK-SETUP.md``:

1. ``tokenCreate`` with the staff email/password -- get a staff JWT.
2. ``appCreate`` -- create a Saleor app with ``MANAGE_PRODUCTS`` permission
   and capture the one-time ``authToken``.
3. ``webhookCreate`` -- register the POD Stylist product-lifecycle
   webhook with the canonical subscription query (channel-listings
   pricing fallback included).

Prints a ready-to-paste ``.env`` block at the end (app token, webhook
secret, Saleor URL).

Usage::

    uv run python scripts/setup_saleor_webhook.py \\
        --email admin@example.com \\
        --password 'Admin##123' \\
        --target-url http://host.docker.internal:8080/webhooks/saleor

The subscription query lives in this file as a module constant; override
with ``--query-file <path>`` to point at a customised version.

Notes:
- The staff password may be passed via ``--password``, the
  ``SALEOR_STAFF_PASSWORD`` environment variable, or stdin (one of
  those is required; CLI flag wins).  Reading from stdin is the
  recommended approach on shared hosts.
- Re-running is safe: the script does not check for an existing app
  or webhook.  Use the Saleor dashboard to delete duplicates.
- ``--dry-run`` prints every GraphQL payload it would send but does
  not call Saleor.  Useful for inspecting the auto-generated query
  before execution.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SALEOR_URL = "http://localhost:8000"
DEFAULT_APP_NAME = "POD Stylist"
DEFAULT_WEBHOOK_NAME = "POD Stylist Product Events"
APP_PERMISSIONS = ["MANAGE_PRODUCTS"]
WEBHOOK_EVENTS = ["PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"]
WEBHOOK_SECRET_BYTES = 32  # 64 hex chars

# Canonical subscription query -- copy of
# docs/SALEOR-APP-WEBHOOK-SETUP.md Step 3 (with the channel-listings
# pricing fallback that keeps the indexer resilient if a product is
# not yet assigned to the storefront channel).
DEFAULT_SUBSCRIPTION_QUERY = """
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
"""

# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------


class SaleorAPIError(RuntimeError):
    """Raised when Saleor returns a non-empty ``errors`` array or HTTP error."""

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def _gql(
    url: str, query: str, variables: dict[str, Any] | None = None, *, token: str | None = None
) -> dict[str, Any]:
    """POST a GraphQL request to Saleor and return ``data``.

    Accepts either ``http://host:8000`` (the dashboard URL) or
    ``http://host:8000/graphql/`` (the API URL); the ``/graphql/``
    suffix is appended automatically when missing.

    Raises:
        SaleorAPIError: When the HTTP response is non-2xx, or the body
            contains an ``errors`` key, or the requested field is null.
    """
    graph_url = url if url.endswith("/graphql/") else f"{url.rstrip('/')}/graphql/"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body: dict[str, Any] = {"query": query}
    if variables is not None:
        body["variables"] = variables

    response = httpx.post(graph_url, json=body, headers=headers, timeout=30.0)

    if response.status_code >= 400:
        raise SaleorAPIError(
            f"HTTP {response.status_code} from Saleor: {response.text[:300]}",
            payload=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {"text": response.text},
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise SaleorAPIError(
            f"Non-JSON response from Saleor: {response.text[:300]}",
            payload={"text": response.text},
        ) from exc

    if "errors" in payload and payload["errors"]:
        raise SaleorAPIError(
            f"GraphQL error: {payload['errors'][0].get('message', 'unknown')}",
            payload=payload,
        )

    if "data" not in payload or payload["data"] is None:
        raise SaleorAPIError("Response has no data", payload=payload)

    return payload["data"]


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------


def step_token_create(url: str, email: str, password: str) -> str:
    """Authenticate as staff and return a JWT."""
    print(f"[1/3] tokenCreate  (email={email})")
    data = _gql(
        url,
        (
            "mutation($email: String!, $password: String!) {"
            "  tokenCreate(email: $email, password: $password) {"
            "    token"
            "    user { id email isStaff }"
            "  }"
            "}"
        ),
        {"email": email, "password": password},
    )
    result = data["tokenCreate"]
    if not result or not result.get("token"):
        raise SaleorAPIError("tokenCreate returned no token", payload=data)
    if not result["user"]["isStaff"]:
        raise SaleorAPIError(
            f"User {email} is not staff -- MANAGE_APPS permission required",
            payload=data,
        )
    print(f"      ok  user={result['user']['email']}")
    return result["token"]


def step_app_create(url: str, staff_token: str, app_name: str) -> tuple[str, str]:
    """Create a Saleor app and return ``(app_id, auth_token)``."""
    print(f"[2/3] appCreate    (name={app_name!r})")
    data = _gql(
        url,
        (
            "mutation($input: AppInput!) {"
            "  appCreate(input: $input) {"
            "    authToken"
            "    app { id name }"
            "    errors { field message code permissions }"
            "  }"
            "}"
        ),
        {"input": {"name": app_name, "permissions": APP_PERMISSIONS}},
        token=staff_token,
    )
    result = data["appCreate"]
    if result.get("errors"):
        raise SaleorAPIError(
            f"appCreate errors: {[e.get('code') for e in result['errors']]}",
            payload=data,
        )
    if not result or not result.get("authToken") or not result.get("app"):
        raise SaleorAPIError("appCreate returned no authToken or app", payload=data)
    print(f"      ok  app_id={result['app']['id']}  (authToken captured)")
    return result["app"]["id"], result["authToken"]


def step_webhook_create(
    url: str,
    staff_token: str,
    *,
    app_id: str,
    webhook_name: str,
    target_url: str,
    subscription_query: str,
    webhook_secret: str,
) -> str:
    """Create the product-lifecycle webhook and return its id.

    Uses staff auth (the staff is the one creating the webhook) and
    passes the app id explicitly to bind the webhook to the app from
    Step 2.  This is the only auth + field combination that Saleor's
    ``webhookCreate`` accepts.

    ``webhook_secret`` is passed as the ``secretKey`` field of the
    input.  Saleor uses this exact value as the HMAC-SHA256 key when
    signing outbound webhook payloads (see
    ``saleor/webhook/transport/utils.py``).  If the field is omitted
    Saleor auto-generates an opaque secret we cannot read, which
    breaks signature verification on the receiver.
    """
    print(f"[3/3] webhookCreate (name={webhook_name!r}, app={app_id})")
    data = _gql(
        url,
        (
            "mutation($input: WebhookCreateInput!) {"
            "  webhookCreate(input: $input) {"
            "    webhook { id name targetUrl secretKey isActive asyncEvents { eventType } }"
            "    errors { field message code }"
            "  }"
            "}"
        ),
        {
            "input": {
                "name": webhook_name,
                "app": app_id,
                "targetUrl": target_url,
                "asyncEvents": WEBHOOK_EVENTS,
                "isActive": True,
                "query": subscription_query,
                "secretKey": webhook_secret,
            }
        },
        token=staff_token,
    )
    result = data["webhookCreate"]
    if result.get("errors"):
        raise SaleorAPIError(
            f"webhookCreate errors: {[e.get('code') for e in result['errors']]}",
            payload=data,
        )
    if not result or not result.get("webhook"):
        raise SaleorAPIError("webhookCreate returned no webhook", payload=data)
    webhook = result["webhook"]
    print(f"      ok  webhook_id={webhook['id']}  target={webhook['targetUrl']}")
    print(f"      events={[e['eventType'] for e in webhook['asyncEvents']]}")
    return webhook["id"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_password(args: argparse.Namespace) -> str:
    """Resolve the staff password from CLI, env, or stdin (in that order)."""
    if args.password:
        return args.password
    env_value = os.environ.get("SALEOR_STAFF_PASSWORD")
    if env_value:
        return env_value
    print("Staff password (input is hidden):", file=sys.stderr)
    return getpass.getpass("> ")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap a Saleor app and the POD Stylist product-lifecycle webhook in one shot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run python scripts/setup_saleor_webhook.py \\\n"
            "      --email admin@example.com --password 'Admin##123' \\\n"
            "      --target-url http://host.docker.internal:8080/webhooks/saleor\n"
            "\n"
            "  echo '$PASSWORD' | uv run python scripts/setup_saleor_webhook.py \\\n"
            "      --email admin@example.com --target-url http://host.docker.internal:8080/webhooks/saleor\n"
        ),
    )
    parser.add_argument(
        "--email", required=True, help="Staff email (must have MANAGE_APPS permission)"
    )
    parser.add_argument(
        "--password",
        help=(
            "Staff password. If omitted, the script reads "
            "$SALEOR_STAFF_PASSWORD, then prompts on stdin. "
            "Stdin is recommended on shared hosts."
        ),
    )
    parser.add_argument(
        "--saleor-url",
        default=DEFAULT_SALEOR_URL,
        help=f"Saleor GraphQL endpoint (default: {DEFAULT_SALEOR_URL})",
    )
    parser.add_argument(
        "--app-name", default=DEFAULT_APP_NAME, help=f"App name (default: {DEFAULT_APP_NAME!r})"
    )
    parser.add_argument(
        "--webhook-name",
        default=DEFAULT_WEBHOOK_NAME,
        help=f"Webhook name (default: {DEFAULT_WEBHOOK_NAME!r})",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="Where Saleor should POST product events (e.g. http://host.docker.internal:8080/webhooks/saleor)",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        help="Override the default subscription query with a file (GraphQL document)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every payload that would be sent, but do not call Saleor",
    )
    return parser


def _print_env_block(*, saleor_url: str, app_token: str, webhook_secret: str) -> None:
    print()
    print("=" * 72)
    print("  Append the following to your .env (replace existing values if any):")
    print("=" * 72)
    print(f"SALEOR_URL={saleor_url}")
    print(f"SALEOR_APP_TOKEN={app_token}")
    print(f"SALEOR_WEBHOOK_SECRET={webhook_secret}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    subscription_query = (
        args.query_file.read_text() if args.query_file else DEFAULT_SUBSCRIPTION_QUERY
    )

    if args.dry_run:
        print("DRY RUN -- no requests will be sent to Saleor.")
        print()
        print("Would call:")
        saleor_base = args.saleor_url.rstrip("/")
        graph_url = saleor_base if saleor_base.endswith("/graphql") else f"{saleor_base}/graphql/"
        print(f"  POST {graph_url}")
        print("  1) tokenCreate(email=<email>, password=<hidden>)")
        print(f"  2) appCreate(name={args.app_name!r}, permissions={APP_PERMISSIONS})")
        print(f"  3) webhookCreate(name={args.webhook_name!r}, targetUrl={args.target_url!r}, ...)")
        print()
        print("Subscription query that would be sent:")
        print("-" * 72)
        print(subscription_query)
        print("-" * 72)
        return 0

    try:
        password = _resolve_password(args)
        webhook_secret = secrets.token_hex(WEBHOOK_SECRET_BYTES)

        staff_token = step_token_create(args.saleor_url, args.email, password)
        app_id, app_token = step_app_create(args.saleor_url, staff_token, args.app_name)
        webhook_id = step_webhook_create(
            args.saleor_url,
            staff_token,
            app_id=app_id,
            webhook_name=args.webhook_name,
            target_url=args.target_url,
            subscription_query=subscription_query,
            webhook_secret=webhook_secret,
        )
    except SaleorAPIError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        if exc.payload:
            print("--- Saleor response (truncated) ---", file=sys.stderr)
            print(json.dumps(exc.payload, indent=2)[:1500], file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"\nHTTP error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print()
    print("Setup complete.")
    print(f"  app_id     = {app_id}")
    print(f"  webhook_id = {webhook_id}")
    print(f"  webhook_secret (generated, 64 hex chars) = {webhook_secret}")
    _print_env_block(saleor_url=args.saleor_url, app_token=app_token, webhook_secret=webhook_secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
