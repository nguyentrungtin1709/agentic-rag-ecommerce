# Embedding Model Fine-Tuning

Off-the-shelf embedding models are trained on broad general datasets and may fail to
capture domain-specific vocabulary and nuances. Fine-tuning adapts a base model to
your corpus.

---

## When to Fine-Tune

Fine-tuning is most valuable when:

- The document corpus contains highly specialized terminology (medical, legal, financial,
  engineering, scientific).
- Standard retrieval quality on domain-specific queries is poor using general-purpose models.
- The vocabulary is niche enough that the base model's representations cluster incorrectly.

The more niche the dataset, the greater the potential improvement from fine-tuning.

---

## Fine-Tuning Process

1. Select a base embedding model (e.g. a Sentence Transformer from HuggingFace).
2. Prepare domain-specific training data: pairs of (query, relevant passage) examples.
   These can be curated manually or generated synthetically using an LLM.
3. Fine-tune using a contrastive loss function. The loss adjusts embeddings so that
   semantically similar items (query and its relevant passage) cluster closer together in
   vector space, and dissimilar items are pushed apart.
4. Evaluate the fine-tuned model using a curated set of query-answer pairs and measure
   retrieval metrics (hit rate, MRR) to confirm improvement.

---

## Expected Outcomes

- The fine-tuned model extends domain vocabulary coverage.
- Retrieval scores improve on domain-specific queries.
- The model may lose some general-domain performance if fine-tuned too aggressively
  (typical trade-off with domain adaptation).

---

## Integration with LlamaIndex

After fine-tuning, load the model using `HuggingFaceEmbedding` and set it as the global
embedding model:

```python
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core import Settings

Settings.embed_model = HuggingFaceEmbedding(model_name="path/to/fine-tuned-model")
```

See `components/models.md` for full embedding configuration options.

---

## Key Design Ideas

- Evaluate before and after fine-tuning with the same retrieval metric suite.
- Synthetic data generation (using an LLM to create question-passage pairs from your
  documents) is a practical way to bootstrap training data without manual annotation.
- Fine-tuned models should be versioned and stored alongside the index they were used
  to build. Changing the embedding model requires re-indexing.
