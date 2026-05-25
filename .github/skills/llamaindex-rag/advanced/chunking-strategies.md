# Chunking Strategies

Chunking is the process of splitting documents into smaller units (nodes/chunks) for
indexing and retrieval. The chunking strategy strongly affects retrieval quality.

---

## 1. Fixed-Size Chunking

Split text into chunks of a fixed token or character count with optional overlap.

- Simple to implement.
- May split sentences or concepts mid-way.
- Overlap helps maintain local context across chunk boundaries.
- LlamaIndex: `TokenTextSplitter(chunk_size=512, chunk_overlap=50)`.

When to use: general purpose baseline; good starting point before evaluating alternatives.

---

## 2. Semantic Chunking

Group sentences or passages by semantic similarity rather than size. Chunk boundaries
are placed where the semantic content shifts, not at arbitrary size limits.

- Produces chunks that align with topic boundaries.
- More meaningful retrieval units at the cost of higher complexity and compute.
- Requires an embedding model to compute sentence similarity.
- LlamaIndex: `SemanticSplitterNodeParser(embed_model=..., buffer_size=1, breakpoint_percentile_threshold=95)`.

When to use: when retrieval precision matters more than simplicity; works best for
narrative text with clear topic transitions.

---

## 3. Recursive Chunking

Attempt to split at natural delimiters in a recursive fallback chain: paragraphs -> sentences
-> words. Only falls to the next level when the previous delimiter produces a chunk that
exceeds the size limit.

- Preserves structure better than fixed-size splitting.
- Produces more natural splits that respect document organization.
- LlamaIndex: `LangchainNodeParser` wrapping `RecursiveCharacterTextSplitter`, or the `Chunker` class with `"recursive"` alias.

When to use: general-purpose prose documents where structure is present but irregular.

---

## 4. Document Structure-Based Chunking

Use document structure signals (headings, sections, list items, code blocks) to define
chunk boundaries. Each structural unit becomes a separate node.

- Requires a parser that understands the document format (Markdown, HTML, JSON).
- LlamaIndex: `MarkdownNodeParser`, `HTMLNodeParser`, `JSONNodeParser`, `CodeSplitter`.
- Best combined with a text splitter to handle sections that are still too long.

When to use: when the document has clear structural organization and semantic units
correspond to structural units (e.g. documentation, legal contracts, technical manuals).

---

## 5. LLM-Based Chunking

Use an LLM to propose semantically meaningful split points within the document.

- Highest chunk quality; highest compute cost.
- The LLM understands context and can respect implicit topic shifts.
- Suitable only when the document corpus is small and chunk quality is critical.

When to use: high-value, low-volume documents where retrieval precision justifies the cost.

---

## 6. Late Chunking

A newer approach that decouples the embedding step from the splitting step:

1. Process the full document through a long-context embedding model first, producing
   contextual token embeddings for the entire document.
2. Split the resulting embeddings into chunks rather than splitting the raw text.

Because embeddings are produced before splitting, each chunk retains document-wide context
rather than being contextualized in isolation. This better preserves long-range relationships
that any text-level splitting strategy would destroy.

When to use: when long-range dependencies between distant parts of the document matter for
retrieval accuracy. Requires a long-context embedding model.

---

## Comparison

| Strategy | Quality | Cost | Best for |
|---|---|---|---|
| Fixed-size | Baseline | Low | Quick starts, general text |
| Semantic | High | Medium | Narrative text, clear topic shifts |
| Recursive | Medium-high | Low | General prose with loose structure |
| Document-structure | High (when structure is present) | Medium | Markdown, HTML, JSON, code |
| LLM-based | Highest | High | Small, high-value corpora |
| Late chunking | High | Medium-high | Documents with long-range context dependencies |

---

## Key Design Ideas

- The chunking strategy is one of the most impactful decisions in the RAG pipeline.
- Chunk size determines the trade-off between retrieval precision and context completeness.
- Chunk overlap reduces information loss at boundaries but increases index size.
- For mixed-content documents (text + tables + images), apply different chunking strategies
  per content type. See `document-preprocessing.md`.
- Sentence window retrieval can further decouple retrieval granularity from generation context
  size. See `sentence-window-retrieval.md`.
