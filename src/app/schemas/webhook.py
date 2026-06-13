"""Saleor webhook request schemas (HTTP boundary only).

Saleor sends HMAC-SHA256-signed POST requests to ``/webhooks/saleor`` for
product lifecycle events.  This module defines the Pydantic models used
to parse the inbound body and to feed the ``process_webhook`` Celery
task with structured arguments.

The shape mirrors Saleor's real wire format (verified against
https://docs.saleor.io/developer/extending/webhooks/overview and
https://docs.saleor.io/developer/extending/webhooks/subscription-webhook-payloads).
The registered subscription query uses the ``event { ... }`` root, so
Saleor emits the inner selection set directly (no ``data`` wrapper) and
sends the event type in a separate HTTP header:

.. code-block:: json

    {
        "product": {
            "id": "UHJvZHVjdDoxMjM=",
            "name": "Apple Juice",
            "description": "...",
            "thumbnail": { "url": "..." },
            "category": { "name": "Drinks", "slug": "drinks" },
            "collections": [ { "name": "Spring 2026", "slug": "spring-2026" } ],
            "channelListings": [ { ... } ]
        }
    }

The body key (``product``) mirrors the GraphQL selection in the
subscription query.  If the subscription is later changed to expose a
different entity, the schema field name must move to match.

The event type is **not** in the body — it arrives in the
``Saleor-Event`` HTTP header (with the deprecated ``X-Saleor-Event``
fallback).  The endpoint reads it from the request headers, not from
this schema.

The wire value of the header is **lowercase** (``product_created``,
``product_updated``, ``product_deleted``) — this is the literal value
of the ``WebhookEventAsyncType`` enum in Saleor's
``webhook/event_types.py`` (verified against
``ghcr.io/saleor/saleor:3.23``).  We canonicalise the header value to
uppercase at the endpoint boundary so the rest of the codebase can
work in the conventional ``PRODUCT_UPDATED`` form.

Notes
-----
- ``ProductObject`` uses ``ConfigDict(extra="allow")`` so future Saleor
  fields pass through to the indexer without validation failure.  We
  type only the fields the indexer actually reads.
- This module is an input-only boundary type — it is never returned to
  API callers and never imported outside the API / task layers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Canonical (uppercase) event names used internally by the app — the
# task dispatch, log keys, and tests all work in this form.  The wire
# value from Saleor is lowercase (see module docstring); the endpoint
# upper-cases the header before dispatch.
SaleorProductEvent = Literal[
    "PRODUCT_CREATED",
    "PRODUCT_UPDATED",
    "PRODUCT_DELETED",
]


class Thumbnail(BaseModel):
    """``Product.thumbnail`` field as sent by Saleor.

    Saleor 3.x returns ``{"url": "...", "alt": "..."}``; older
    versions may omit ``alt``.  Both are optional so the model still
    validates when one or the other is missing.
    """

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    alt: str | None = None


class Category(BaseModel):
    """``Product.category`` field."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    slug: str | None = None


class Collection(BaseModel):
    """``Product.collections[i]`` field.

    Saleor returns a flat list (no ``edges`` wrapper); older versions
    used a connection-style wrapper.  The mapper in
    :func:`app.services.saleor_client.SaleorClient.node_to_product_payload`
    handles both shapes — this model only validates the unwrapped form.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    slug: str | None = None


class Money(BaseModel):
    """A ``Money`` value as Saleor returns it in channel listings."""

    model_config = ConfigDict(extra="allow")

    amount: float | None = None
    currency: str | None = None


class ChannelPricing(BaseModel):
    """Pricing block inside a channel listing."""

    model_config = ConfigDict(extra="allow")

    price: Money | None = None


class Channel(BaseModel):
    """``ChannelListing.channel`` reference."""

    model_config = ConfigDict(extra="allow")

    slug: str | None = None
    name: str | None = None


class ChannelListing(BaseModel):
    """``Product.channelListings[i]`` field.

    We only model the fields the indexer reads (``channel`` and
    ``price``); the rest are passed through via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    channel: Channel | None = None
    price: Money | None = None
    # Field name mirrors Saleor's GraphQL schema exactly; renaming would
    # desync the wire format and break model_dump() round-trips.
    channelPricing: ChannelPricing | None = None  # Saleor 3.21+  # noqa: N815


class Media(BaseModel):
    """``Product.media[i]`` field.  We capture the URL and alt only."""

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    alt: str | None = None
    type: str | None = None


class ProductObject(BaseModel):
    """The Saleor product payload (top-level body key ``product``).

    The webhook body is the inner selection set of the
    ``subscription { event { ... on ProductCreated { product { ... } } } }`
    query, so this is the only object in the body and its key is
    the GraphQL field name ``product``.

    Only ``id`` is required; everything else is optional.  This is
    driven by Saleor's ``ProductDeleted`` selection: the production
    subscription in ``docs/SALEOR-APP-WEBHOOK-SETUP.md`` (Step 3)
    selects just ``product { id }`` for the delete event, so the
    inbound body for a delete is ``{"product": {"id": "..."}}`` with
    no ``name`` or pricing.  Treating the upsert-only fields as
    optional lets a single schema accept all three lifecycle events.

    ``ConfigDict(extra="allow")`` is intentional: Saleor ships new
    optional fields in minor versions (``attributes``, ``rating``,
    ``productType``, …) and we do not want a webhook to fail just
    because Saleor added one.  The indexer pulls only the fields it
    needs (``name``, ``description``, ``thumbnail.url``, …); the rest
    are forwarded via ``model_dump()`` and ignored downstream.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    description: str | None = None
    slug: str | None = None
    # Saleor GraphQL field names — must mirror the wire format for
    # the indexer's existing mapper to read them via model_dump().
    isAvailable: bool | None = None  # noqa: N815
    thumbnail: Thumbnail | None = None
    category: Category | None = None
    collections: list[Collection] = Field(default_factory=list)
    channelListings: list[ChannelListing] = Field(default_factory=list)  # noqa: N815
    media: list[Media] = Field(default_factory=list)


class SaleorWebhookPayload(BaseModel):
    """Top-level webhook payload (FR-076, FR-077).

    The body contains only the inner selection set of the
    subscription query.  With ``subscription { event { ... on
    ProductCreated { product { ... } } } }`, Saleor emits the
    selection set directly — there is no ``event`` field in the
    body and no ``data`` wrapper.  The event type arrives in the
    ``Saleor-Event`` HTTP header and is validated by the endpoint
    (see :mod:`app.api.webhooks`).

    ``product`` is typed as ``ProductObject | None`` so a missing
    selection (e.g. ``PRODUCT_DELETED`` with an empty body, or a
    malformed payload) still validates and the endpoint can return
    400 with a useful error rather than crashing.  The key name
    mirrors the GraphQL field selected by the subscription query.
    """

    product: ProductObject | None = None
