"""Saleor GraphQL client for product catalogue ingestion.

Used by the Celery reindex task to fetch product data from Saleor and
upsert it into the Qdrant collection.  All communication uses the
Saleor Storefront API (no authentication required for public products).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import Settings

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
        description
        category {
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
          }
        }
        thumbnail {
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
        self._http = httpx.AsyncClient(
            base_url=graphql_url,
            timeout=30,
            headers={"Content-Type": "application/json"},
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

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
