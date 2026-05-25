# ColPali — Visually-Rich Document Retrieval

ColPali extends ColBERT's late interaction approach to document page images, enabling
high-quality retrieval from PDFs and other visually complex documents without text
extraction.

---

## Motivation

Traditional text extraction pipelines fail or lose information for:
- Scanned documents (no machine-readable text layer)
- Dense tables whose cell relationships are destroyed by naive text extraction
- Pages where text layout, whitespace, and visual structure carry meaning
- Documents with charts, diagrams, or figures

OCR is imperfect and costly. ColPali bypasses the text extraction step entirely by
treating each document page as an image and operating on visual embeddings.

---

## Architecture

ColPali combines:
- **Vision Transformer (ViT)**: encodes image patches into dense embeddings
- **ColBERT late interaction**: scores query-document relevance at the patch level

---

## Document Processing (Indexing)

1. Render each document page as an image.
2. Divide the page image into a fixed-size grid of patches (e.g. 14x14 or 16x16 patch grid).
3. Pass all patches through a Vision Transformer. Each patch produces a contextual embedding
   that encodes the patch's visual content plus its positional context within the page.
4. Store all patch embeddings as a **MultiVector** record in the vector database.
   One document page = one record = multiple vectors (one per patch).

MultiVector support is required. Qdrant natively supports MultiVector storage.

---

## Query Processing

Convert the user text query into token-level embeddings using a ColBERT-style text encoder.
The resulting query representation is a sequence of token embeddings, just as in standard
ColBERT.

---

## Scoring — Late Interaction on Patches

1. Compute a similarity matrix between each query token embedding and all patch embeddings
   for a given page.
2. Apply MaxSim: for each query token, take the maximum similarity across all patch embeddings.
3. Sum the per-query-token MaxSim scores to produce a final page-level relevance score.

This is late interaction: the query-document scoring happens after encoding, over the
patch-level representations.

```
Score(query, page) = SUM over all query token embeddings t:
                       MAX over all patch embeddings p on the page:
                         cosine_similarity(t, p)
```

---

## Retrieval and Generation

1. Rank all candidate pages by their ColPali relevance score.
2. Retrieve top-k page images.
3. Pass the original query + the top-k page images to a multimodal LLM (e.g. GPT-4V,
   LLaVA, Gemini).
4. The multimodal LLM generates a response grounded in both the query and the page images.

---

## Vector Database Requirements

ColPali requires a vector database that supports:
- **MultiVector storage**: storing multiple vectors per record (one per image patch).
- **MaxSim aggregation**: computing the max-similarity over multiple vectors per record.

Qdrant natively supports both. See `advanced/colbert.md` for context on the MaxSim
aggregation mechanism.

---

## When to Use ColPali

| Scenario | Use ColPali? | Notes |
|---|---|---|
| Scanned PDFs with no text layer | Yes | Text extraction is impossible |
| Image-heavy PDFs (charts, diagrams) | Yes | Visual content carries key information |
| Mixed text and tables on same page | Yes | Table structure preserved in image form |
| Clean, machine-readable text PDFs | Maybe | Standard text pipeline may suffice |
| Real-time queries at high volume | Caution | Patch-level indexing is storage-intensive |

---

## Comparison: ColPali vs. Multimodal RAG

| Aspect | ColPali | Multimodal RAG (CLIP-based) |
|---|---|---|
| Retrieval unit | Document page as image | Separate text and image items |
| Scoring mechanism | Late interaction on ViT patches | Cosine similarity on single CLIP vector |
| Text extraction needed | No | Yes for text, No for images |
| Best for | Scanned or layout-complex docs | Mixed text+image documents with discrete images |
| Multimodal LLM required at generation | Yes | Yes |
