# Evaluation with Ragas

Ragas is a dedicated evaluation framework for RAG systems. It measures retrieval quality
and generation quality independently and provides the most comprehensive metric coverage
of any open-source RAG evaluation library.

---

## Why Not Use Faithfulness Alone

Evaluating only faithfulness misses common failure modes:

| Problem | Faithfulness | Answer relevance | Context relevance |
|---|---|---|---|
| Answer is accurate but does not address the question | PASS | FAIL | — |
| Retrieval returns irrelevant chunks | — | — | FAIL |
| Answer exceeds what the context supports | FAIL | — | — |

Running the full metric suite identifies which specific stage of the pipeline is weak.

---

## Ragas Metric Suite

| Metric | What it measures | Formula basis |
|---|---|---|
| **Faithfulness** | Whether the answer is fully supported by the retrieved context | Verify each claim in the answer against context |
| **Answer relevance** | Whether the answer directly addresses the user query | Cosine similarity of reverse-generated questions to original query |
| **Context relevance** | Whether retrieved context is relevant to the query | Proportion of context sentences that are relevant |
| **Answer correctness** | Whether the answer matches a reference (ground-truth) answer | Semantic + factual overlap with ground truth |
| **Context recall** | Whether retrieved context covers all relevant information from the reference | Coverage of ground-truth claims in retrieved context |
| **Context precision** | What fraction of the retrieved context is actually relevant | Precision of retrieved context relative to the ground truth |

Note: `answer correctness`, `context recall`, and `context precision` require a ground-truth
reference answer. The other metrics are reference-free.

---

## Ragas vs. LlamaIndex Built-in Evaluation

| Capability | LlamaIndex eval | Ragas |
|---|---|---|
| Faithfulness | Yes | Yes |
| Answer relevance | Partially (CorrectnessEvaluator) | Yes (dedicated metric) |
| Context relevance | No | Yes |
| Answer correctness | Limited | Yes |
| Context recall | No | Yes |
| Context precision | No | Yes |
| Synthetic dataset generation | No | Yes |

---

## Synthetic Test Dataset Generation

Ragas can generate evaluation datasets directly from a document corpus, removing the need
for manual labeling.

Process:
1. Provide the document corpus to Ragas.
2. Ragas uses an LLM to generate question-answer pairs from the documents.
3. The synthetic dataset is used to benchmark the RAG pipeline end-to-end.

This is especially valuable when:
- A human-labeled test set does not exist.
- You want to quickly estimate pipeline quality before investing in manual annotation.
- You are comparing multiple versions of a pipeline.

---

## Integration with LlamaIndex

Ragas has a LlamaIndex integration module. Key steps:

1. Collect query outputs from a LlamaIndex query engine (query, response, source_nodes).
2. Convert to Ragas `EvaluationDataset` format.
3. Run `evaluate()` with the desired metrics.

```python
from ragas.integrations.llamaindex import evaluate

# results is a LlamaIndex evaluation result object
score = evaluate(results, metrics=[faithfulness, answer_relevance, context_relevance])
```

---

## Practical Guidance

- Run Ragas evaluation as part of CI/CD pipelines when changing chunking strategies,
  embedding models, retrieval top-k, or prompts.
- Track metric trends over time rather than spot-checking after changes.
- Use synthetic datasets for quick feedback cycles; use human-labeled data for final
  validation before production deployment.
- When faithfulness drops, inspect the synthesizer and prompts.
- When context relevance drops, inspect chunk sizes, embedding models, and top-k.
- When context recall drops, increase retrieval diversity (query expansion, hybrid search).
