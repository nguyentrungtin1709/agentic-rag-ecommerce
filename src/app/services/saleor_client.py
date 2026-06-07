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
        collections(first: 10) {
          edges {
            node {
              name
            }
          }
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
        collections: list[str] = [
            edge["node"]["name"]
            for edge in ((node.get("collections") or {}).get("edges") or [])
            if edge.get("node", {}).get("name")
        ]

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

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
