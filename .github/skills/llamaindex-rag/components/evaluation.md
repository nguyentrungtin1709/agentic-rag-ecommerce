# Evaluation

## Concept

- Evaluation measures the quality of LLM applications (RAG systems, agents).
- Two main evaluation targets: response quality and retrieval quality.
- All evaluators implement `BaseEvaluator` with `evaluate()` and `evaluate_response()` methods.
- Output: `EvaluationResult` with fields `passing` (bool), `score` (float), `feedback` (string).

---

## Response Evaluation

Evaluates generated answers using LLM-based judges (e.g. GPT-4). Most modules do not require ground-truth labels.

### Evaluators

- `FaithfulnessEvaluator`: checks whether the answer is grounded in the retrieved context (hallucination detection). No labels required.
- `AnswerRelevancyEvaluator`: checks whether the generated answer is relevant to the query. No labels required.
- `ContextRelevancyEvaluator`: checks whether the retrieved context is relevant to the query. No labels required.
- `CorrectnessEvaluator`: checks whether the generated answer matches a reference (ground-truth) answer. Requires labels.
- `SemanticSimilarityEvaluator`: measures semantic similarity between the generated answer and a reference answer. Requires labels.
- `GuidelineAdherenceEvaluator`: checks whether the answer follows specified guidelines. No labels required.

### Question Generation

- `RagDatasetGenerator.from_documents(documents, llm=..., num_questions_per_chunk=...)`: auto-generates evaluation questions from source documents.

---

## Retrieval Evaluation

- Evaluates the retriever independently from the response synthesizer.
- Approach: synthetically generate (question, context) pairs from the corpus, then measure retrieval ranking quality.
- Metrics: MRR (Mean Reciprocal Rank), hit-rate, precision, and others.

---

## LabelledRagDataset

- Standardized dataset format for RAG evaluation.
- Each example contains: `query`, `reference_answer`, `reference_contexts`, and metadata about creation method.
- Create manually with `LabelledRagDataExample` objects, or generate automatically with `RagDatasetGenerator`.
- Available datasets can be downloaded via `download_llama_dataset()` or `llamaindex-cli download-llamadataset`.
- Contribute datasets back to LlamaHub by submitting the JSON + source files.

### Running Evaluation

- `RagEvaluatorPack`: end-to-end pack that runs predictions and evaluations; outputs a benchmark DataFrame with mean scores for Correctness, Relevancy, Faithfulness, and Context Similarity.
- `EvaluatorBenchmarkerPack`: evaluates an evaluator on a `LabelledEvaluatorDataset` (for benchmarking judge LLMs).
- `LabelledPairwiseEvaluatorDataset`: evaluates pairwise comparison evaluators (`PairwiseComparisonEvaluator`).

---

## Community Integrations

- UpTrain
- Tonic Validate (includes a web UI)
- DeepEval
- Ragas
- RAGChecker
- Cleanlab
