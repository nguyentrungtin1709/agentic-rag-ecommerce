"""Domain model for a product as stored in Qdrant."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProductPayload(BaseModel):
    """Payload stored alongside a product vector in Qdrant.

    Field mapping from Saleor GraphQL ``Product`` type:

    Attributes:
        product_id: Saleor product ID (``Product.id``, base64 Node ID).
        name: Product display name (``Product.name``).
        slug: URL slug (``Product.slug``); used to build ``saleor_url``.
        description: Plain-text product description extracted from
            ``Product.description`` (EditorJS JSONString, stripped and
            optionally summarised during ingestion).
        category: Category name (``Product.category.name``).
        collections: List of collection names the product belongs to
            (``Product.collections[].name``). Used as tags — e.g. ``["Spring 2025", "Sale"]``.
        price_min: Lowest gross price from ``pricing.priceRange.start.gross.amount``.
        price_max: Highest gross price from ``pricing.priceRange.stop.gross.amount``.
        currency: ISO 4217 currency code from ``pricing.priceRange.start.gross.currency``.
        price_range: Human-readable formatted range, e.g. ``"100k \u2013 250k VND"``.
            Derived at ingestion time; stored for display only (not used for filtering).
        available: Whether the product is purchasable (``Product.isAvailable``).
        saleor_url: Storefront product URL, built as
            ``{SALEOR_STOREFRONT_URL}/products/{slug}/``.
        thumbnail_url: Product thumbnail URL
            (``Product.thumbnail(size: 512, format: WEBP).url``).
    """

    product_id: str
    name: str
    slug: str = ""
    description: str = ""
    category: str = ""
    collections: list[str] = Field(default_factory=list)
    price_min: float = 0.0
    price_max: float = 0.0
    currency: str = "USD"
    price_range: str = ""
    available: bool = True
    saleor_url: str = ""
    thumbnail_url: str = ""
