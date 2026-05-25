# ColBERT — Late Interaction Retrieval

ColBERT is a retrieval model that achieves cross-encoder accuracy at bi-encoder speed
by deferring the query-document interaction to after encoding.

---

## Context: Retrieval Model Trade-offs

Standard retrieval models fall into two categories:

| Model type | Encoding | Speed | Accuracy | Typical use |
|---|---|---|---|---|
| Bi-encoder | Query and document encoded independently into single vectors | Fast | Moderate | First-stage retrieval at scale |
| Cross-encoder | Query and document jointly encoded; interaction computed inside the model | Slow at scale | High | Re-ranking small candidate sets |

Bi-encoders are fast because documents can be pre-encoded and indexed. At query time only
the query needs to be encoded. But the single-vector representation loses intra-token
relationships.

Cross-encoders are accurate because they process the query and document together, allowing
the model to capture fine-grained relevance. But they cannot be used for first-stage
retrieval: every document in the corpus must be processed at query time.

---

## ColBERT Mechanism

ColBERT (Contextualized Late Interaction over BERT) resolves this trade-off:

1. **Encoding**: Both query and document are encoded independently, but at the token
   level — producing a sequence of token embeddings rather than a single vector.
2. **Indexing**: All document token embeddings are pre-computed and stored.
3. **Scoring (late interaction)**: At query time, a similarity matrix is computed between
   every query token embedding and every document token embedding.
4. **MaxSim**: For each query token, take the maximum similarity score across all document
   token embeddings. This identifies the document token most relevant to each query token.
5. **Aggregation**: Sum the per-query-token MaxSim scores to produce the final relevance score.

```
Score(q, d) = SUM over all query tokens t:
                MAX over all document tokens d_i:
                  cosine_similarity(embed(t), embed(d_i))
```

This is called "late interaction" because the cross-query-document comparison happens after
encoding, not inside a joint model.

---

## Why ColBERT Is Practical

- Documents are pre-encoded and indexed. Storage cost is higher than bi-encoders (many
  token embeddings per document instead of one) but comparable in practice.
- Retrieval requires computing token-level MaxSim only on the small candidate set, not
  the full corpus. With an ANN index over token embeddings, first-stage retrieval is fast.
- Scoring is more accurate than single-vector dot product because it captures fine-grained
  token-level relevance.

---

## Practical Implementation Patterns

### BM25 + ColBERT
1. Use BM25 for fast keyword-based first-stage retrieval (retrieve top-100).
2. Rerank the candidates with ColBERT.

Benefit: BM25 handles exact keyword matching; ColBERT provides semantic re-scoring.

### Clustering + ColBERT
1. Cluster the document corpus into semantic clusters.
2. At query time, identify the best-matching cluster(s).
3. Apply ColBERT only within the selected cluster(s).

Benefit: reduces the candidate set before applying the more expensive ColBERT scoring.

---

## RAGatouille Library

RAGatouille is a Python library that wraps ColBERT and simplifies its use in retrieval
pipelines.

Key capabilities:
- Index a document corpus with ColBERT token embeddings.
- Search using ColBERT late interaction scoring.
- Retrieve top-k results for a given query.

```bash
pip install ragatouille
```

RAGatouille manages the ColBERT model weights, index construction, and search logic,
making ColBERT accessible without implementing the MaxSim pipeline manually.

---

## When to Use ColBERT

- When bi-encoder retrieval accuracy is insufficient and cross-encoder re-ranking latency
  is too high.
- When retrieval precision is critical and storage cost is acceptable.
- When working with documents where token-level matching is important (technical documentation,
  legal text, scientific papers).

---

## Comparison: ColBERT vs. Cross-Encoder Re-ranking

| Aspect | ColBERT | Cross-encoder re-ranking |
|---|---|---|
| Encoding | Pre-computed token embeddings | Computed at query time (query + doc together) |
| Storage | High (many token vectors per doc) | None (no indexed embeddings) |
| Scoring overhead | Token-level MaxSim on candidates | Full transformer forward pass per candidate |
| Accuracy | High | Highest |
| Integration | RAGatouille | CohereRerank, SentenceTransformerRerank, LLMRerank |
