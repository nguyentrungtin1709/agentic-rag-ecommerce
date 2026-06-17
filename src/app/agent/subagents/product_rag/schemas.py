"""Pydantic schemas for the ProductRAG subgraph.

Structured output types used with ``ChatOpenAI.with_structured_output``.

The prepare-query LLM is called with ``PrepareQueryOutput`` as the
response schema.  Rerank uses ``RerankOutput`` — a Pydantic
``BaseModel`` with a ``ranked_ids`` field — because:

1. LangChain's ``with_structured_output`` requires a real class
   (Pydantic model, TypedDict, or callable) for the schema argument.
   A bare ``list[str]`` type annotation is treated as the string
   ``"list[str]"`` at runtime and raises
   ``TypeError: list[str] is not a module, class, method, or function.``
2. OpenAI's structured-outputs endpoint (the default method for
   ``langchain-openai>=0.3``) only accepts JSON Schemas of
   ``type: "object"``.  A ``RootModel[list[str]]`` would generate
   a ``type: "array"`` schema and be rejected with HTTP 400.
   Wrapping the list in a named ``BaseModel`` field keeps the
   JSON Schema of ``type: "object"`` while preserving the
   list-of-IDs contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrepareQueryOutput(BaseModel):
    """Structured output of the prepare-query LLM call.

    Produces an optimised English search query plus optional hard
    metadata filters for Qdrant.  Mirrors the DRAFT 0.6 Option B
    contract: only scalar fields with reliable exact-match semantics
    are used as metadata filters (``available``, ``price_max``); all
    category/style/occasion/recipient intent is embedded in the query
    text for the hybrid (BM25 + dense) search to handle semantically.

    Attributes:
        query: English search query string with category, style,
            occasion, and recipient intent embedded.  Always in
            English regardless of the input language (FR-040).
        available: When ``True``, restrict the Qdrant search to
            in-stock products.  When ``False`` or ``None``, do not
            apply an availability filter.
        price_max: Numeric maximum price ceiling.  When ``None``, do
            not apply a price filter.  Free-text budgets
            (e.g. "under 200k VND", "cheap") are normalised to a
            numeric value by the LLM.
    """

    query: str = Field(
        description=(
            "Concise English search query. Embed category, style, "
            "occasion, and recipient intent directly in the text. "
            "Do NOT include filter values (e.g. 'available: true') "
            "in the query."
        ),
    )
    available: bool | None = Field(
        default=None,
        description=(
            "Set to true only if the user explicitly requires in-stock "
            "items. Use null (not false) when no availability "
            "constraint is stated."
        ),
    )
    price_max: float | None = Field(
        default=None,
        description=(
            "Numeric budget ceiling. Set only when the user mentions a "
            "clear budget cap. Interpret free-text budgets to a "
            "number. Use null when the budget is ambiguous or absent."
        ),
    )


class RerankOutput(BaseModel):
    """Structured output of the rerank LLM call.

    Carries a ``ranked_ids`` list of product IDs in descending
    relevance order.  The class is a ``BaseModel`` (not a
    ``RootModel``) so its JSON Schema is ``type: "object"`` —
    a requirement of OpenAI's structured-outputs endpoint used
    by default in ``langchain-openai>=0.3``.

    The rerank LLM emits ``{"ranked_ids": [...]}``; the node
    reads ``.ranked_ids`` and maps each ID back to its candidate
    payload, capping the result at ``qdrant_rerank_top_k``.
    """

    ranked_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Product IDs in descending relevance order. Length is "
            "bounded by the LLM; the node caps the final result "
            "at qdrant_rerank_top_k."
        ),
    )
