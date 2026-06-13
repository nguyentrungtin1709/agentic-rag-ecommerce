"""Saleor GraphQL client for product catalogue ingestion.

Used by the Celery reindex task to fetch product data from Saleor and
upsert it into the Qdrant collection.  Authentication uses the Saleor
app token (``SALEOR_APP_TOKEN``) passed as a Bearer token in the
``Authorization`` header (FR-073).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import Settings
from app.models.product import ProductPayload

logger = structlog.get_logger(__name__)

_PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after, channel: "default-channel") {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        slug
        description
        isAvailable
        category {
          name
        }
        collections {
          name
        }
        pricing {
          priceRange {
            start {
              gross {
                amount
                currency
              }
            }
            stop {
              gross {
                amount
                currency
              }
            }
          }
        }
        thumbnail(size: 512, format: WEBP) {
          url
        }
      }
    }
  }
}
"""

_PRODUCT_BY_ID_QUERY = """
query ProductById($id: ID!) {
  product(id: $id, channel: "default-channel") {
    id
    name
    slug
    description
    isAvailable
    category {
      name
    }
    collections {
      name
    }
    pricing {
      priceRange {
        start {
          gross {
            amount
            currency
          }
        }
        stop {
          gross {
            amount
            currency
          }
        }
      }
    }
    thumbnail(size: 512, format: WEBP) {
      url
    }
  }
}
"""


class SaleorClient:
    """Async GraphQL client for Saleor product data.

    Args:
        settings: Application settings providing ``saleor_url``.
    """

    def __init__(self, settings: Settings) -> None:
        graphql_url = f"{settings.saleor_url}/graphql/"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.saleor_app_token:
            headers["Authorization"] = f"Bearer {settings.saleor_app_token}"
        self._http = httpx.AsyncClient(
            base_url=graphql_url,
            timeout=30,
            headers=headers,
        )

    async def fetch_all_products(self, page_size: int = 100) -> list[dict[str, Any]]:
        """Paginate through all public products and return a flat list.

        Args:
            page_size: Number of products per GraphQL page (max 100).

        Returns:
            List of raw product dicts from the GraphQL ``node`` objects.
        """
        products: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            variables: dict[str, Any] = {"first": page_size}
            if cursor:
                variables["after"] = cursor

            response = await self._http.post(
                "",
                json={"query": _PRODUCTS_QUERY, "variables": variables},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            page = data["data"]["products"]
            for edge in page["edges"]:
                products.append(edge["node"])

            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]

        logger.info("Saleor products fetched", count=len(products))
        return products

    @staticmethod
    def node_to_product_payload(node: dict[str, Any], storefront_url: str) -> ProductPayload:
        """Convert a raw Saleor GraphQL product node to a ``ProductPayload``.

        Args:
            node: A single ``node`` dict from the ``products.edges`` GraphQL response.
            storefront_url: Base storefront URL from ``SALEOR_STOREFRONT_URL`` setting;
                used to build the ``saleor_url`` field.

        Returns:
            ``ProductPayload`` populated from the Saleor GraphQL fields.
        """
        pricing = (node.get("pricing") or {}).get("priceRange") or {}
        start_gross = (pricing.get("start") or {}).get("gross") or {}
        stop_gross = (pricing.get("stop") or {}).get("gross") or {}

        price_min: float = float(start_gross.get("amount") or 0.0)
        price_max: float = float(stop_gross.get("amount") or price_min)
        currency: str = str(start_gross.get("currency") or "USD")

        slug: str = node.get("slug") or ""
        # Saleor returns collections as a flat list of ``Collection`` objects
        # (no ``edges`` wrapper).  The fallback handles older or future
        # schemas that may still wrap in a connection.
        raw_collections = node.get("collections") or []
        if (
            raw_collections
            and isinstance(raw_collections[0], dict)
            and "node" in raw_collections[0]
        ):
            names = [edge["node"].get("name") for edge in raw_collections]
        else:
            names = [c.get("name") for c in raw_collections]
        collections: list[str] = [n for n in names if n]

        return ProductPayload(
            product_id=node["id"],
            name=node.get("name") or "",
            slug=slug,
            description=node.get("description") or "",
            category=(node.get("category") or {}).get("name") or "",
            collections=collections,
            price_min=price_min,
            price_max=price_max,
            currency=currency,
            price_range="",  # derived and formatted by ProductIndexer at ingestion time
            available=bool(node.get("isAvailable", True)),
            saleor_url=f"{storefront_url}/products/{slug}/" if slug else "",
            thumbnail_url=(node.get("thumbnail") or {}).get("url") or "",
        )

    async def fetch_products_by_ids(self, product_ids: list[str]) -> list[dict[str, Any]]:
        """Re-fetch a list of products by their Saleor IDs.

        Used by the Celery ``process_batch`` worker to fetch the
        specific products assigned to its batch (avoids a wasteful
        full-catalogue re-fetch per batch).

        Saleor's GraphQL ``Product`` type exposes a single ``id`` filter
        argument.  This implementation sends one query per ID — this
        is a deliberate trade-off:

        - 100 IDs / 1 batch = 100 round-trips per worker.  Acceptable
          for batches of 100; the per-request payload is small and
          the requests are independent so they can be made in
          parallel if needed in a future optimisation.
        - A single ``filter: { ids: [...] }`` query is not part of
          Saleor's stable API surface and varies between versions;
          per-ID queries are stable and predictable.

        If any individual query fails, the whole call raises — the
        worker treats that as a transient error and retries.

        Args:
            product_ids: Saleor product IDs to fetch.

        Returns:
            List of raw product ``node`` dicts in the same order as
            ``product_ids`` (any ID with no matching product is
            silently dropped from the result).
        """
        if not product_ids:
            return []

        results: list[dict[str, Any]] = []
        for pid in product_ids:
            response = await self._http.post(
                "",
                json={
                    "query": _PRODUCT_BY_ID_QUERY,
                    "variables": {"id": pid},
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            node = (data.get("data") or {}).get("product")
            if node is not None:
                results.append(node)

        logger.info(
            "Saleor products fetched by ids",
            requested=len(product_ids),
            returned=len(results),
        )
        return results

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
