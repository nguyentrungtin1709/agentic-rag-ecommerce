# Hybrid Search

Hybrid search combines semantic vector search with traditional keyword (BM25) search.
A fusion algorithm merges results from both retrieval paths into a single ranked list.

---

## Concept

Vector search excels at capturing semantic meaning and conceptual similarity but may
miss exact term matches. BM25 keyword search excels at exact term matching but has no
semantic understanding. Hybrid search leverages both.

---

## The Alpha Parameter

The alpha (α) parameter controls the balance between the two retrieval modes:

- α = 1.0: pure semantic (vector) search.
- α = 0.0: pure keyword (BM25) search.
- 0 < α < 1: weighted combination of both scores.

The optimal alpha value is use-case specific and should be tuned empirically using an
evaluation dataset.

---

## When to Use Hybrid Search

Use hybrid search when the query requires both:
- Conceptual or semantic understanding (handled by vector search).
- Exact term matching for specific entities, version numbers, product names, or technical
  terminology (handled by BM25).

Example where hybrid outperforms pure vector search:
- Query: "Excel formula not calculating correctly after update"
- Vector search: finds conceptually related content about spreadsheet issues.
- BM25: ensures "Excel" and "formula" are matched exactly.
- Hybrid: combines both for more complete and accurate results.

---

## Fusion Methods

Two common approaches for merging the result sets:

### Reciprocal Rank Fusion (RRF)

Assigns a score to each result based on its rank in each retrieval list. Does not require
score normalization. Robust to outliers in individual score distributions.

### Score Normalization + Weighted Sum

Normalize scores from both retrieval paths to the same scale, then compute a weighted
combination using alpha.

---

## LlamaIndex Support

Use `QueryFusionRetriever` combining a vector retriever and a BM25 retriever:

```python
from llama_index.core.retrievers import QueryFusionRetriever, BM25Retriever
from llama_index.retrievers.bm25 import BM25Retriever

vector_retriever = index.as_retriever(similarity_top_k=10)
bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=10)

retriever = QueryFusionRetriever(
    retrievers=[vector_retriever, bm25_retriever],
    similarity_top_k=5,
    mode="reciprocal_rerank",
)
```

The `LlamaCloudIndex` managed retrieval also exposes `dense_similarity_top_k`,
`sparse_similarity_top_k`, and `alpha` parameters for managed hybrid retrieval.
See `components/indexing.md`.

---

## Key Design Ideas

- Hybrid search is the recommended default for most production RAG applications.
- Pure vector search is sufficient only when queries are always semantically rich and never
  depend on exact term matching.
- BM25 requires the full node corpus to be available in memory at query time; plan storage
  accordingly.
- Always evaluate alpha tuning with your actual query distribution, not a synthetic one.
