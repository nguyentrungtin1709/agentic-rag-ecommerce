# Query Transformation

Using the raw user query directly for retrieval often yields poor results. Query
transformation techniques reformulate or expand the query before it reaches the retriever.

---

## 1. Query Rewriting (Rewrite-Retrieve-Read)

Use an LLM or a specialized small model to reformulate the original query into a cleaner,
more retrieval-friendly form before sending it to the retriever.

Pattern name: "Rewrite-Retrieve-Read" (replaces the traditional "Retrieve-then-Read").

### How it works

1. User submits the raw query.
2. An LLM rewrites it into a form that better matches how information is stored in the index.
3. The rewritten query is sent to the retriever.
4. Retrieved context + rewritten query are sent to the synthesizer.

### Example

- Raw: "Can you tell me which movies were popular last summer? I'm trying to find a blockbuster film."
- Rewritten: "What were the top-grossing movies released last summer?"

### Implementation in LlamaIndex

LlamaIndex's `HyDEQueryTransform` is a related pattern: instead of rewriting the query,
it generates a hypothetical answer document first, then uses that as the retrieval query.
This addresses vocabulary mismatch between short queries and longer document passages.

```python
from llama_index.core.indices.query.query_transform.base import HyDEQueryTransform
from llama_index.core.query_engine import TransformQueryEngine

hyde = HyDEQueryTransform(include_original=True)
query_engine = TransformQueryEngine(index.as_query_engine(), query_transform=hyde)
response = query_engine.query("What did the author do after RISD?")
```

---

## 2. Query Expansion

Use an LLM to generate multiple similar or related queries from the original input. All
expanded queries are sent to the retriever, increasing the number and diversity of retrieved
documents.

### How it works

1. LLM generates N variations or related questions from the original query.
2. All N+1 queries (original + expanded) are sent to the retriever.
3. Results are merged and deduplicated.
4. A re-ranking step is usually necessary to prioritize the most relevant results.

### Note

Because the candidate set grows with expansion, re-ranking becomes important after
query expansion. See `reranking.md`.

### LlamaIndex support

Use `QueryFusionRetriever` to combine results from multiple query variants:

```python
from llama_index.core.retrievers import QueryFusionRetriever

retriever = QueryFusionRetriever(
    [index.as_retriever()],
    similarity_top_k=2,
    num_queries=4,  # generate 4 query variants
    use_async=True,
)
```

---

## 3. Query Decomposition

Break complex, multi-faceted queries into simpler, focused sub-queries using an LLM.
Each sub-query targets a specific aspect of the original question.

### How it works

1. Decompose original query into N sub-queries using an LLM.
2. Run each sub-query against the retriever (can run in parallel).
3. Optionally apply keyword extraction and metadata filter extraction per sub-query
   to further narrow retrieval scope.
4. Aggregate and synthesize retrieved results from all sub-queries.
5. Generate a comprehensive answer to the original composite question.

### Example decomposition

- Original: "Why am I always tired even though I eat healthy? Should I try diet trends?"
- Sub-queries:
    1. "What dietary factors cause fatigue?"
    2. "What are popular diet trends and their effect on energy levels?"
    3. "How can I assess whether my diet is balanced?"

### LlamaIndex support

Use `SubQuestionQueryEngine` or `MultiStepQueryEngine` for query decomposition:

```python
from llama_index.core.indices.query.query_transform.base import StepDecomposeQueryTransform
from llama_index.core.query_engine import MultiStepQueryEngine

step_decompose = StepDecomposeQueryTransform(llm, verbose=True)
query_engine = MultiStepQueryEngine(index.as_query_engine(), query_transform=step_decompose)
response = query_engine.query("Who was in the first batch of the accelerator the author started?")
```

---

## Key Design Ideas

- Apply query transformation before retrieval, not after.
- HyDE is the most commonly plug-in query rewriting technique in LlamaIndex.
- Query expansion + query fusion improves recall at the cost of additional LLM calls.
- Query decomposition is most valuable for complex, multi-hop questions.
- All transformation techniques can be combined with hybrid search and re-ranking.
