"""Domain model for a product as stored in Qdrant."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProductPayload(BaseModel):
    """Payload stored alongside a product vector in Qdrant.

    Attributes:
        product_id: Saleor product ID (e.g. ``"UHJvZHVjdDoxMjM="``)
        name: Product display name.
        description: Plain-text product description.
        category: Category name (e.g. ``"T-Shirts"``).
        price: Price in the store's default currency.
        currency: ISO 4217 currency code (e.g. ``"USD"``).
        thumbnail_url: URL of the product thumbnail image.
        tags: Optional list of freeform tags.
    """

    product_id: str
    name: str
    description: str = ""
    category: str = ""
    price: float = 0.0
    currency: str = "USD"
    thumbnail_url: str = ""
    tags: list[str] = Field(default_factory=list)
