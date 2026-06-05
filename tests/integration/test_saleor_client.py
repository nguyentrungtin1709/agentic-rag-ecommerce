"""Integration tests — Saleor GraphQL client.

Verifies that:
- The Saleor GraphQL endpoint is reachable.
- The ``products`` query returns a valid response structure.
- Pagination metadata fields are present.

NOTE: These tests require Saleor to be running and the
``default-channel`` to exist.  The number of products returned
may be zero in a fresh installation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio

from tests.integration.conftest import SALEOR_URL

_PRODUCTS_QUERY = """
query Products($first: Int!) {
  products(first: $first, channel: "default-channel") {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
      }
    }
  }
}
"""

_GRAPHQL_ENDPOINT = f"{SALEOR_URL}/graphql/"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client for Saleor requests."""
    async with httpx.AsyncClient(
        base_url=_GRAPHQL_ENDPOINT,
        timeout=15,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


async def test_saleor_graphql_endpoint_reachable(http_client: httpx.AsyncClient) -> None:
    """POST to the GraphQL endpoint must return HTTP 200."""
    response = await http_client.post(
        "",
        json={"query": "{ __typename }"},
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}. Is Saleor running on localhost:8000?"
    )


async def test_saleor_products_query_structure(http_client: httpx.AsyncClient) -> None:
    """The products query must return a well-formed response with pageInfo."""
    response = await http_client.post(
        "",
        json={"query": _PRODUCTS_QUERY, "variables": {"first": 5}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "data" in body, f"Unexpected GraphQL response: {body}"
    assert "errors" not in body or body.get("errors") is None, (
        f"GraphQL errors in response: {body.get('errors')}"
    )
    products = body["data"]["products"]
    assert "pageInfo" in products
    assert "hasNextPage" in products["pageInfo"]
    assert "edges" in products


async def test_saleor_products_query_returns_list(http_client: httpx.AsyncClient) -> None:
    """The edges list must be a list (empty is acceptable for fresh installs)."""
    response = await http_client.post(
        "",
        json={"query": _PRODUCTS_QUERY, "variables": {"first": 10}},
    )
    body = response.json()
    edges = body["data"]["products"]["edges"]
    assert isinstance(edges, list)
