# Multimodal RAG

Multimodal RAG extends the standard text-only retrieval pipeline to include images
alongside — or instead of — text, enabling responses grounded in image content.

---

## Motivation

Many information sources combine text and images: technical diagrams, product catalogs,
scientific figures, scanned documents with charts. A text-only RAG pipeline discards
the image content entirely. Multimodal RAG preserves and utilizes it.

---

## CLIP Embeddings

CLIP (Contrastive Language-Image Pretraining) maps both text and images into a shared
embedding space.

### Training approach (background)
CLIP is trained contrastively on large-scale text-image pairs. A text encoder and an image
encoder are trained so that matched pairs have high cosine similarity (pulled close) while
mismatched pairs are pushed apart. The result: images and their textual descriptions end
up near each other in the same vector space.

### Enabled retrieval modes
- Text query → retrieve images by semantic similarity.
- Image query → retrieve semantically related text or images.
- Mixed queries → retrieve across both modalities simultaneously.

---

## Retrieval Architecture Options

### Option A: Separate indexes, text routing
- Maintain a text index and a separate image index.
- At query time, use an LLM or classifier to decide which index to query.
- Pass results from both to the synthesizer.

### Option B: Shared embedding space (CLIP-based)
- Embed all text and images into a single CLIP vector space.
- Run a single retrieval query across the combined index.
- Retrieved candidates may include text nodes, image nodes, or both.

### Option C: Tool-calling agent routing
- Define text retrieval and image retrieval as separate tools.
- An LLM agent dynamically selects the appropriate retrieval tool based on query intent.
- Enables nuanced routing: some queries need both text and image results; others need
  only one.

---

## Multimodal Prompting

After retrieval, pass both the text context and the retrieved images to a multimodal LLM
(e.g. GPT-4V, LLaVA, Gemini).

The LLM generates a response grounded in both modalities:
```
System: You are a helpful assistant.
User:   Query: "What does the architecture diagram show?"
        [Retrieved text chunk: "Figure 3 depicts the three-layer architecture..."]
        [Retrieved image: <base64 or URL of the diagram>]
```

Considerations:
- Not all LLMs support image inputs; check the model's multimodal capabilities.
- Image tokens are significantly more expensive than text tokens; monitor costs.
- Image resolution and format affect both quality and token count.

---

## FastEmbed

FastEmbed is a lightweight embedding library by Qdrant. It supports CLIP and other
embedding models and is designed for fast local embedding generation with minimal
dependencies.

Use cases:
- Generating CLIP embeddings locally for both text and images.
- Suitable for environments where full PyTorch pipelines are too heavy.

```bash
pip install fastembed
```

---

## When to Use Multimodal RAG

| Scenario | Appropriate? | Notes |
|---|---|---|
| Documents with embedded diagrams | Yes | Diagrams carry non-textual meaning |
| Product catalogs with images | Yes | Image similarity often more useful than text |
| Pure text documents | No | Adds complexity with no benefit |
| Scanned PDFs with charts | Yes | Consider ColPali instead for page-level retrieval |
| Real-time image query | Yes | CLIP shared space or tool routing |

---

## Relationship to ColPali

ColPali (see `advanced/colpali.md`) also handles image content but operates at the
document-page level using late interaction on ViT patch embeddings. Multimodal RAG
and ColPali address overlapping scenarios:

- Use **Multimodal RAG** when documents mix text paragraphs and discrete images that
  should be retrieved and used independently.
- Use **ColPali** when documents are scanned pages or visually complex layouts where
  the page as a whole is the meaningful retrieval unit.
