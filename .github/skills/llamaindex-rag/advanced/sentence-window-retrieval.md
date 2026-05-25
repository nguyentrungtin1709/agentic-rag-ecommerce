# Sentence Window Retrieval

Sentence window retrieval decouples retrieval granularity from generation context size,
improving both retrieval precision and response quality.

---

## Motivation

Standard chunking (fixed-size or semantic) creates a trade-off:

- Small chunks → higher retrieval precision (the retrieved text closely matches the query)
- Large chunks → more context for the LLM to generate a complete answer

Sentence window retrieval resolves this conflict by indexing at sentence granularity but
expanding the context window at generation time.

---

## Mechanism

1. **Indexing phase** — Each document is parsed into individual sentences. Each sentence
   node stores its surrounding sentences (the "window") as metadata.
2. **Retrieval phase** — The index is queried against the small, precise sentence
   embeddings. Only sentence-level nodes need to be compared.
3. **Post-processing phase** — Before handing nodes to the LLM, the window (surrounding
   sentences stored in metadata) replaces the matched sentence text.

The LLM receives expanded context while the vector similarity search still operated on
precise sentence embeddings.

```
Index:
  [ sentence_1 ]  → embedding of sentence_1
  [ sentence_2 ]  → embedding of sentence_2   (metadata: window = sent_1 + sent_2 + sent_3)
  [ sentence_3 ]  → embedding of sentence_3

Retrieval:
  Query hits sentence_2 (small, precise match)

Post-processing (MetadataReplacementPostProcessor):
  Replace sentence_2 text with its stored window: sent_1 + sent_2 + sent_3
  → LLM sees 3-sentence context, not just 1
```

---

## LlamaIndex Implementation

### Step 1: Parse with SentenceWindowNodeParser

```python
from llama_index.core.node_parser import SentenceWindowNodeParser

node_parser = SentenceWindowNodeParser.from_defaults(
    window_size=3,                         # number of sentences to include on each side
    window_metadata_key="window",          # metadata key where the window text is stored
    original_text_metadata_key="original_text",
)
```

### Step 2: Build the index

```python
from llama_index.core import VectorStoreIndex

nodes = node_parser.get_nodes_from_documents(documents)
index = VectorStoreIndex(nodes)
```

### Step 3: Apply MetadataReplacementPostProcessor

```python
from llama_index.core.postprocessor import MetadataReplacementPostProcessor

postprocessor = MetadataReplacementPostProcessor(
    target_metadata_key="window",          # must match window_metadata_key above
)

query_engine = index.as_query_engine(
    node_postprocessors=[postprocessor],
)
```

---

## Configuration Guidelines

| Parameter | Typical value | Notes |
|---|---|---|
| `window_size` | 1–5 | Larger window = more context, higher token use |
| First-stage retrieval top-k | 5–10 | Smaller is fine, context is expanded by window anyway |
| Combine with re-ranking | Recommended | Apply re-ranker before MetadataReplacementPostProcessor |

---

## When to Use

- Documents with tight sentence structure where individual sentences are meaningful but
  each sentence alone lacks sufficient context (research papers, legal text, manuals).
- When standard chunking produces results that are accurate but incomplete for generation.
- When you want finer retrieval recall without reducing LLM answer quality.

---

## Interaction with Other Techniques

- **Chunk size comparison**: sentence window is a specialized chunking + postprocessing
  pattern, not merely smaller chunks. The window guarantees coherent surrounding context
  rather than arbitrary character boundaries.
- **Re-ranking**: re-ranking can be applied before the metadata replacement step to select
  which sentence-level nodes to expand.
- **Evaluation**: use faithfulness and context relevance (Ragas) to verify that the
  expanded window improves answers without introducing noise.
