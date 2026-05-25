# Vector Search Filtering: Distance Thresholding and Autocut

Top-k retrieval always returns exactly k results regardless of their actual relevance.
These two techniques manage result quality by removing obvious bad matches.

---

## 1. Distance Thresholding

Set a maximum allowed distance (or minimum similarity score) between the query vector
and each result vector. Any result that exceeds the distance threshold is filtered out,
even if it falls within the top-k count.

### How it works

After retrieving top-k candidates, apply a post-retrieval filter:
- If similarity score < cutoff → discard.
- If similarity score >= cutoff → keep.

### LlamaIndex support

Use `SimilarityPostprocessor`:

```python
from llama_index.core.postprocessor import SimilarityPostprocessor

postprocessor = SimilarityPostprocessor(similarity_cutoff=0.75)
filtered_nodes = postprocessor.postprocess_nodes(nodes)
```

Or apply at query engine level:

```python
query_engine = index.as_query_engine(
    node_postprocessors=[SimilarityPostprocessor(similarity_cutoff=0.75)]
)
```

### Trade-offs

- Simple and fast.
- Requires manual threshold calibration; the optimal value depends on the embedding model
  and corpus.
- A threshold that is too high rejects valid results; too low passes irrelevant ones.

---

## 2. Autocut

A dynamic approach that automatically identifies the natural boundary between relevant
and irrelevant results without requiring a fixed threshold.

### How it works

1. Retrieve top-k candidates with their distance scores.
2. Compute the gaps between consecutive distance scores in the ranked list.
3. Find the largest gap in the distance distribution (an inflection point indicating a
   natural boundary between a cluster of relevant results and a cluster of irrelevant ones).
4. Discard everything beyond that gap.

### Advantages over fixed thresholding

- No manual calibration required.
- Adapts dynamically to queries: for a highly specific query where all top results are
  tightly clustered and relevant, autocut keeps all of them. For a vague query where
  quality degrades quickly, it aggressively cuts the tail.

### Availability

Autocut is available natively in Weaviate vector store. For other vector stores, it can
be implemented as a custom postprocessor by computing delta scores between ranked results.

---

## Key Design Ideas

- Always post-filter retrieval results; relying solely on top-k count is fragile.
- Use `SimilarityPostprocessor` as the simplest baseline.
- Consider autocut when threshold calibration is impractical (large number of query types,
  rapidly changing corpus, or no evaluation dataset for threshold tuning).
- Combine with re-ranking for robust two-stage quality control. See `reranking.md`.
