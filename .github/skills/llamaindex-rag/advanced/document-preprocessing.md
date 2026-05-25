# Document Pre-Processing

Before documents can be loaded into LlamaIndex, they often require pre-processing steps.
This stage is distinct from the LlamaIndex ingestion pipeline and happens before any
LlamaIndex API is called.

---

## 1. Data Acquisition and Integration

Collect documents from multiple heterogeneous sources into a unified knowledge base.
Different source types require different extraction approaches. Plan the collection pipeline
per source type before writing any ingestion code.

---

## 2. Extraction and Parsing by Source Type

| Source type | Parsing approach |
|---|---|
| Markdown, DOCX, plain text | Structure-preserving text extraction |
| Scanned docs / image-based PDFs | OCR (Optical Character Recognition) to convert to LLM-readable text |
| Web content | HTML parsing via DOM traversal |
| Spreadsheets | Cell-relationship-aware parsing |
| All types | Metadata extraction: author, timestamps, document properties |

Note: ColPali and ColQwen can embed document page images directly, potentially making
traditional OCR unnecessary for scanned documents. See `colpali.md`.

---

## 3. Data Cleaning and Noise Reduction

- Remove irrelevant content: headers, footers, repeated boilerplate, navigation links.
- Correct inconsistencies, normalize whitespace and encoding.
- Handle missing values while preserving structural integrity.
- Goal: maximize the ratio of useful content to noise before any chunk ever enters the index.

---

## 4. Data Transformation and Document Partitioning

Convert all extracted content into a standardized schema regardless of original file type.
Document partitioning separates content into logical units such as paragraphs, sections,
and tables. This is distinct from chunking, which happens later inside the ingestion pipeline.

---

## 5. Handling Mixed Content Types

Real-world documents contain multiple content types within a single file (text, tables,
images, code blocks). Each type needs different handling:

- Plain text: standard LLM-ready input.
- Tables: must be structure-preserving (not converted to a flat text dump). Keep as a
  single node or serialize with row/column context preserved.
- Images embedded in documents: extract separately; treat as multimodal content.
  See `multimodal-rag.md`.

---

## 6. Recommended Libraries

### Unstructured

Open-source library that partitions PDFs, DOCX, HTML, and many other formats into
typed elements (Title, NarrativeText, Table, Image, etc.). Preserves layout signals.

Install: `pip install unstructured`

Outputs a sequence of typed elements that can be selectively included, skipped, or
routed into different parsing paths based on element type.

### Docling

Document parsing library with strong table and layout understanding. Outputs structured
representations suitable for downstream chunking.

Install: `pip install docling`

Especially useful for technical documents, financial reports, and multi-column PDFs where
layout carries meaning.

---

## 7. Integration with LlamaIndex

After pre-processing with Unstructured or Docling, feed the cleaned output into
LlamaIndex as `Document` objects before passing them to the ingestion pipeline:

```python
from llama_index.core import Document

# outputs from Unstructured or Docling processing
preprocessed_chunks = [...]

documents = [Document(text=chunk["text"], metadata=chunk["metadata"])
             for chunk in preprocessed_chunks]
```

Then continue with the standard LlamaIndex ingestion pipeline.

---

## Key Design Ideas

- Treat pre-processing as a separate, versioned pipeline stage.
- Parse and clean before chunking; garbage in produces garbage chunks.
- Route different content types (tables, images) to different downstream paths.
- Preserve metadata (source, page number, section) early; it is hard to add later.
