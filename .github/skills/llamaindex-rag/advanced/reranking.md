# Re-ranking

Re-ranking is a two-stage retrieval pattern that improves answer quality without sacrificing
retrieval speed.

---

## Concept

Initial retrieval (vector search, BM25, or hybrid) optimizes for speed using approximate
methods that may miss nuanced relevance signals. Re-ranking refines the candidate set with
a more accurate but slower model.

### Two-Stage Pattern

```
Query
  │
  ▼
Stage 1: Fast retrieval (vector / BM25 / hybrid)
  → Retrieve large candidate set (e.g. top-50)
  │
  ▼
Stage 2: Re-ranker scores each candidate against the query
  → More accurate relevance scoring
  │
  ▼
Return top-k re-ranked results to the synthesizer
```

---

## Why Re-ranking Matters

- Bi-encoders (used in vector search) encode query and document independently. They are
  fast for indexing millions of documents but cannot model the interaction between a query
  and a specific document at scoring time.
- Cross-encoders jointly encode the query and each candidate, scoring their interaction
  directly. Much more accurate but cannot be used for first-stage retrieval (too slow to
  score all documents).
- Re-ranking uses the cross-encoder only on the small candidate set, combining the speed
  of the first stage with the accuracy of the second stage.

| Model type | Encoding | Speed | Accuracy |
|---|---|---|---|
| Bi-encoder | Independent | Fast | Moderate |
| Cross-encoder (re-ranker) | Joint query + doc | Slow at scale, fast on small sets | High |

---

## LlamaIndex Built-In Re-rankers

### CohereRerank

Uses Cohere's hosted rerank API. Returns top N nodes.

```python
from llama_index.postprocessor.cohere_rerank import CohereRerank

reranker = CohereRerank(api_key="...", top_n=5, model="rerank-english-v2.0")
query_engine = index.as_query_engine(node_postprocessors=[reranker])
```

### SentenceTransformerRerank

Uses cross-encoder models from the sentence-transformers library. Fully local.
Default model: `cross-encoder/ms-marco-TinyBERT-L-2-v2` (fastest).

```python
from llama_index.core.postprocessor import SentenceTransformerRerank

reranker = SentenceTransformerRerank(model="cross-encoder/ms-marco-MiniLM-L-2-v2", top_n=5)
query_engine = index.as_query_engine(node_postprocessors=[reranker])
```

### LLMRerank

Uses an LLM to score relevance. Highest quality; highest cost.

```python
from llama_index.core.postprocessor import LLMRerank

reranker = LLMRerank(top_n=5)
query_engine = index.as_query_engine(node_postprocessors=[reranker])
```

### JinaRerank

Uses Jina's hosted rerank API. Returns top N nodes.

---

## Integration with Query Expansion and Hybrid Search

Re-ranking is especially important after:
- Query expansion: the candidate set grows with each additional query variant.
- Hybrid search: merging two ranked lists may produce a mixed-quality set.

Apply re-ranking as the final postprocessing step before handing nodes to the synthesizer.

---

## Key Design Ideas

- Set the first-stage retrieval top-k larger than needed (e.g. 20-50), then let the
  re-ranker select the final top-k (e.g. 5-10). Larger first-stage sets give the re-ranker
  more candidates to identify the best results.
- For latency-sensitive applications, use a local cross-encoder (SentenceTransformerRerank)
  over an API re-ranker.
- For highest accuracy without latency constraints, CohereRerank or LLMRerank are preferred.
- Always evaluate re-ranker impact with a retrieval metric (hit rate, MRR) before adding it.
