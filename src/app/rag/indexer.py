"""Product indexer — Saleor → LlamaIndex → Qdrant pipeline.

Implements the full Saleor → Qdrant ingestion pipeline (Phase 6):

1. Fetch all products from Saleor (cursor-paginated) via
   :class:`app.services.saleor_client.SaleorClient`.
2. For each product, clean the description (HTML/EditorJS stripping)
   and optionally summarise it via an LLM when it exceeds
   ``settings.description_max_chars``.
3. Build a LlamaIndex :class:`TextNode` per product with the
   two-track description rule:
   - ``node.text`` = ``"{name}\\n\\n{summary_or_short}"`` — embedded
   - ``metadata['description']`` = full cleaned description (no cap)
4. Upsert into the Qdrant ``products`` collection with both dense
   (OpenAI) and sparse (BM25 via FastEmbed) vectors.

The indexer is invoked by:

- :func:`app.tasks.run_ingestion_job.run_ingestion_job` (orchestrator
  path — fetches the catalogue, splits into batches, dispatches one
  :func:`app.tasks.process_batch.process_batch` worker per batch).
- :func:`app.tasks.process_batch.process_batch` (per-batch worker
  — calls :meth:`ProductIndexer.index_batch`).
- :func:`app.tasks.process_webhook.process_webhook` (single-product
  path — calls :meth:`ProductIndexer.upsert_product` for created /
  updated events, :meth:`ProductIndexer.delete_product` for deleted).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from itertools import islice
from typing import Any, cast

import structlog
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.schema import TextNode
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.config import Settings
from app.models.product import ProductPayload
from app.rag.text_cleaning import (
    clean_product_description,
    description_for_embedding,
)
from app.services.qdrant_service import (
    _DENSE_VECTOR_NAME,
    _SPARSE_VECTOR_NAME,
    QdrantService,
)
from app.services.saleor_client import SaleorClient

logger = structlog.get_logger(__name__)


class PermanentProductError(Exception):
    """Raised when a product's data is malformed and cannot be indexed.

    The worker treats this as a per-product failure (skips the
    product, continues the batch) or, when every product in a batch
    fails, as a batch-level permanent failure.
    """


def _batched[T](iterable: Iterable[T], n: int) -> Iterable[list[T]]:
    """Yield successive ``n``-sized chunks from ``iterable``."""
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


def to_qdrant_point_id(saleor_product_id: str) -> str:
    """Convert a Saleor product ID to a UUID string for Qdrant point IDs.

    Qdrant 1.18 (strict mode) only accepts unsigned integers or UUIDs as
    point IDs.  Saleor product IDs are base64-encoded GraphQL Global IDs
    (e.g. ``"UHJvZHVjdDoxMjM="``) which are neither, so we deterministically
    hash the Saleor ID into a UUID v5 namespace.

    The same Saleor ID always produces the same UUID, so re-indexing the
    same product is idempotent (Qdrant upsert overwrites by point ID).
    The original Saleor ID is preserved in ``metadata['product_id']`` and
    is what the ``delete_product`` filter matches on, so this transform
    is invisible to retrieval and deletion.

    Args:
        saleor_product_id: The raw Saleor product ID (e.g. GraphQL Global ID).

    Returns:
        A stable UUID string derived from the Saleor ID.

    Example:
        >>> to_qdrant_point_id("UHJvZHVjdDoxMjM=")
        '...'
        >>> to_qdrant_point_id("UHJvZHVjdDoxMjM=") == to_qdrant_point_id("UHJvZHVjdDoxMjM=")
        True
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, saleor_product_id))


class ProductIndexer:
    """Orchestrates the Saleor → Qdrant product indexing pipeline.

    Args:
        settings: Application settings providing Qdrant, OpenAI, and
            Saleor configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Bounded semaphore to keep concurrent OpenAI summarization calls
        # within the configured ceiling (default 10).  Respects the
        # SUMMARIZE_MODEL RPM/TPM limits.
        self._summarize_semaphore = asyncio.Semaphore(
            settings.description_summarize_concurrency,
        )

    # ── Node construction helpers ──────────────────────────────────────

    async def _clean_for_payload(self, payload: ProductPayload) -> str:
        """Clean the description for a single payload.

        Args:
            payload: Source product.

        Returns:
            The cleaned plain-text description.

        Raises:
            PermanentProductError: If the description is malformed
                (e.g. invalid EditorJS JSON, type error).
        """
        try:
            return clean_product_description(payload.description)
        except (ValueError, TypeError) as exc:
            raise PermanentProductError(f"description cleaning failed: {exc}") from exc

    async def _summarize_with_limit(self, cleaned_description: str) -> str:
        """Wrap ``description_for_embedding`` with the rate-limit semaphore.

        Args:
            cleaned_description: Plain-text description that may be too
                long for the embedding model.

        Returns:
            The text to embed — either the cleaned text (when short)
            or an LLM-produced summary (when long).
        """
        async with self._summarize_semaphore:
            return await description_for_embedding(
                cleaned_description,
                max_chars=self._settings.description_max_chars,
            )

    def _build_node(
        self,
        payload: ProductPayload,
        summary: str,
        full_description: str,
    ) -> TextNode:
        """Build a ``TextNode`` from a payload + (possibly summarised) text.

        Implements the two-track description rule (FR-035 + FR-035a):

        - ``node.text`` = ``"{name}\\n\\n{summary}"`` — embedded
        - ``metadata['description']`` = full cleaned text (no cap)

        Args:
            payload: Source product.
            summary: Text used for embedding — either the cleaned
                description (short) or the LLM summary (long).
            full_description: The full cleaned description, stored
                verbatim in metadata for retrieval and LLM context.
        """
        price_range = self._format_price_range(
            payload.price_min,
            payload.price_max,
            payload.currency,
        )
        return TextNode(
            text=f"{payload.name}\n\n{summary}",
            id_=to_qdrant_point_id(payload.product_id),  # idempotent upsert (FR-080)
            metadata={
                "product_id": payload.product_id,
                "name": payload.name,
                "description": full_description,
                "slug": payload.slug,
                "category": payload.category,
                "collections": payload.collections,
                "price_min": payload.price_min,
                "price_max": payload.price_max,
                "currency": payload.currency,
                "price_range": price_range,
                "available": payload.available,
                "saleor_url": payload.saleor_url,
                "thumbnail_url": payload.thumbnail_url,
            },
        )

    @staticmethod
    def _format_price_range(price_min: float, price_max: float, currency: str) -> str:
        """Format a human-readable price range, e.g. ``"100.00 - 250.00 USD"``."""
        if price_min == price_max:
            return f"{price_min:.2f} {currency}".strip()
        return f"{price_min:.2f} - {price_max:.2f} {currency}".strip()

    # ── Single product (used by webhooks) ──────────────────────────────

    async def _payload_to_textnode(self, payload: ProductPayload) -> TextNode:
        """Convert a single ``ProductPayload`` to a ``TextNode``.

        Used by :meth:`upsert_product` and :meth:`delete_product`.
        The batched path (:meth:`index_batch`) uses the parallel
        helpers directly.

        Args:
            payload: Source product.

        Returns:
            The constructed :class:`TextNode`.

        Raises:
            PermanentProductError: If the description cannot be
                parsed.
        """
        cleaned = await self._clean_for_payload(payload)
        embedding_text = await self._summarize_with_limit(cleaned)
        return self._build_node(
            payload,
            summary=embedding_text,
            full_description=cleaned,
        )

    # ── Batched indexing (used by worker) ──────────────────────────────

    async def index_batch(
        self,
        payloads: list[ProductPayload],
    ) -> tuple[int, list[dict]]:
        """Build nodes (skipping permanent errors) and upsert to Qdrant.

        Flow:

        1. Clean every description in parallel (pure-Python regex /
           HTML strip — safe to run without an LLM rate-limit).
        2. Split products into two groups: short descriptions (no LLM
           needed) and long descriptions (need an LLM summary).
        3. For the long group, run bounded-parallel LLM summarization
           via ``asyncio.gather`` + ``asyncio.Semaphore``.
        4. Build a ``TextNode`` per surviving product, then upsert
           all nodes in a single ``IngestionPipeline.arun`` call.

        Args:
            payloads: Products to index in this batch.

        Returns:
            Tuple of ``(succeeded_count, skipped_products)`` where
            ``skipped_products`` is a list of
            ``{product_id, stage, error}`` dicts.

        Raises:
            PermanentProductError: If every product in the batch
                failed cleaning or summarization.  Workers use this
                to mark the batch permanently failed.
        """
        cleaning_results = await asyncio.gather(
            *(self._clean_for_payload(p) for p in payloads),
            return_exceptions=True,
        )

        short_nodes: list[TextNode] = []
        summaries_needed: list[tuple[ProductPayload, str]] = []
        skipped: list[dict] = []

        for payload, result in zip(payloads, cleaning_results, strict=True):
            if isinstance(result, PermanentProductError):
                skipped.append(
                    {
                        "product_id": payload.product_id,
                        "stage": "cleaning",
                        "error": str(result),
                    }
                )
                logger.warning(
                    "product_skipped_permanent",
                    product_id=payload.product_id,
                    error=str(result),
                )
                continue
            cleaned = cast(str, result)
            if len(cleaned) <= self._settings.description_max_chars:
                short_nodes.append(
                    self._build_node(
                        payload,
                        summary=cleaned,
                        full_description=cleaned,
                    )
                )
            else:
                summaries_needed.append((payload, cleaned))

        if summaries_needed:
            summary_results = await asyncio.gather(
                *(self._summarize_with_limit(c) for _, c in summaries_needed),
                return_exceptions=True,
            )
            for (payload, cleaned), summary in zip(summaries_needed, summary_results, strict=True):
                if isinstance(summary, BaseException):
                    skipped.append(
                        {
                            "product_id": payload.product_id,
                            "stage": "summarization",
                            "error": str(summary),
                        }
                    )
                    logger.warning(
                        "product_summarization_failed",
                        product_id=payload.product_id,
                        error=str(summary),
                    )
                    continue
                short_nodes.append(
                    self._build_node(
                        payload,
                        summary=cast(str, summary),
                        full_description=cleaned,
                    )
                )

        if not short_nodes:
            raise PermanentProductError(
                f"All {len(payloads)} products in batch failed cleaning/summarization"
            )

        await self._run_pipeline(short_nodes)

        return len(short_nodes), skipped

    async def _run_pipeline(self, nodes: list[TextNode]) -> None:
        """Upsert a list of ``TextNode`` objects to Qdrant.

        Extracted as a method so unit tests can patch it and avoid
        the real ``QdrantVectorStore`` + ``OpenAIEmbedding`` /
        ``fastembed`` import chain.

        Args:
            nodes: Nodes to embed and upsert.
        """
        # Construct the Qdrant + embedding pipeline per call so the
        # clients are not reused across Celery worker processes.
        qdrant = QdrantService(self._settings)
        try:
            await qdrant.ensure_collection()
            vector_store = QdrantVectorStore(
                collection_name=self._settings.qdrant_collection_name,
                aclient=qdrant.client,
                enable_hybrid=True,
                fastembed_sparse_model="Qdrant/bm25",
                dense_vector_name=_DENSE_VECTOR_NAME,
                sparse_vector_name=_SPARSE_VECTOR_NAME,
            )
            embed_model = OpenAIEmbedding(
                model=self._settings.embedding_model,
                api_key=self._settings.openai_api_key,
            )
            pipeline = IngestionPipeline(
                transformations=[embed_model],
                vector_store=vector_store,
            )
            await pipeline.arun(nodes=nodes)
        finally:
            await qdrant.close()

    # ── Full catalogue reindex (used by orchestrator) ──────────────────

    async def reindex_all(
        self,
        payloads: list[ProductPayload],
    ) -> tuple[int, list[dict]]:
        """Index every product in ``payloads`` (batched internally).

        The orchestrator is responsible for fetching the catalogue
        and passing the products in.  This method splits the list
        into groups of ``settings.reindex_batch_size`` and calls
        :meth:`index_batch` per group, accumulating skipped
        products across groups.

        Args:
            payloads: Full Saleor catalogue (one element per product).

        Returns:
            Tuple of ``(succeeded_count, skipped_products)``.

        Raises:
            Exception: Any exception from the underlying pipeline is
                propagated to the orchestrator (which marks the job
                ``failed`` and re-raises for Celery).
        """
        succeeded = 0
        all_skipped: list[dict] = []
        for chunk in _batched(payloads, self._settings.reindex_batch_size):
            n, skipped = await self.index_batch(chunk)
            succeeded += n
            all_skipped.extend(skipped)
        return succeeded, all_skipped

    # ── Webhook paths (used by ``process_webhook``) ────────────────────

    async def upsert_product(self, product_data: dict[str, Any]) -> str:
        """Embed and upsert a single product (FR-078, FR-080).

        Args:
            product_data: Either a Saleor GraphQL ``node`` dict or a
                ``ProductPayload``-shaped dict.

        Returns:
            The product_id that was upserted.
        """
        if "id" in product_data and hasattr(SaleorClient, "node_to_product_payload"):
            payload = SaleorClient.node_to_product_payload(
                product_data, self._settings.saleor_storefront_url
            )
        else:
            payload = ProductPayload(**product_data)
        await self.index_batch([payload])
        logger.info("product_upserted", product_id=payload.product_id)
        return payload.product_id

    async def delete_product(self, product_id: str) -> None:
        """Remove a product point by ``product_id`` (FR-079).

        Filter key is the flat ``product_id`` field — LlamaIndex
        serialises node metadata as a flat dict (no ``_node_content``
        prefix on user-defined fields), so a top-level key match is
        the right filter shape.

        Args:
            product_id: Saleor product ID to delete.
        """
        qdrant = QdrantService(self._settings)
        try:
            await qdrant.client.delete(
                collection_name=self._settings.qdrant_collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="product_id",
                            match=MatchValue(value=product_id),
                        ),
                    ],
                ),
            )
        finally:
            await qdrant.close()
        logger.info("product_deleted", product_id=product_id)
