# Loading

## Documents and Nodes

### Document

- Generic container for any data source (text file, PDF, API output, database record, etc.).
- Key attributes:
    - `text`: the raw text content.
    - `metadata`: dict of annotations (e.g. filename, category, author) injected into text for LLM and embedding calls.
    - `relationships`: dict of relationships to other Documents/Nodes.
    - `doc_id` (also `id_`): unique identifier used for index refresh and deduplication.
- Metadata control:
    - `excluded_llm_metadata_keys`: keys hidden from the LLM.
    - `excluded_embed_metadata_keys`: keys hidden from the embedding model.
    - Format customization: `metadata_seperator`, `metadata_template`, `text_template`.
- Can be created manually, via `SimpleDirectoryReader`, or via any LlamaHub reader.
- `Document.example()`: quick creation with default text for prototyping.

### Automatic Metadata Extraction

- Use LLMs to extract metadata automatically via extractor modules:
    - `SummaryExtractor`: generates a text summary over a set of nodes.
    - `QuestionsAnsweredExtractor`: generates questions each node can answer.
    - `TitleExtractor`: infers a title from context.
    - `EntityExtractor`: extracts named entities (people, places, things).
- Chain extractors in an `IngestionPipeline` as transformation steps.

### Node

- Represents a chunk of a source Document (text chunk, image, etc.).
- Inherits all metadata and templates from the parent Document.
- Key attributes:
    - `node_id`: auto-generated unique ID; can be set manually.
    - `relationships`: dict using `NodeRelationship` enum (NEXT, PREVIOUS, PARENT) pointing to `RelatedNodeInfo` objects.
    - `RelatedNodeInfo` can carry additional metadata.
- Can be created by parsing Documents through a NodeParser, or constructed manually as `TextNode` objects.
- First-class citizen in LlamaIndex: can be used directly to build indexes.

---

## SimpleDirectoryReader

- Simplest way to load data from local files into LlamaIndex.
- Supported file types (auto-detected by extension): `.csv`, `.docx`, `.epub`, `.hwp`, `.ipynb`, `.jpeg`/`.jpg`, `.mbox`, `.md`, `.mp3`/`.mp4`, `.pdf`, `.png`, `.ppt`/`.pptm`/`.pptx`.
- Unsupported types are treated as plain text.

### Options

- `input_dir`: load all supported files in a directory.
- `recursive=True`: include subdirectories.
- `input_files=[...]`: load specific files.
- `exclude=[...]`: exclude specific file paths.
- `required_exts=[...]`: only load files with given extensions.
- `num_files_limit`: cap the number of files loaded.
- `encoding`: file encoding (default: `utf-8`).
- `num_workers`: parallel loading using multiple processes.
- `filename_as_id=True`: uses full file path as `doc_id`.
- `file_metadata`: custom function `(file_path: str) -> dict` to generate metadata per file.

### Auto-Metadata

- Attached automatically: `file_path`, `file_name`, `file_type`, `file_size`, `creation_date`, `last_modified_date`, `last_accessed_date` (all UTC-normalized).

### Extensibility

- `file_extractor={".myext": MyReader()}`: plug in custom `BaseReader` instances for additional file types. Overrides default extractors for specified extensions.
- `fs`: optional remote filesystem parameter (e.g. for S3 or other fsspec-compatible backends).
- `iter_data()`: iterator to process files one by one as they load.

---

## Data Connectors (LlamaHub)

- LlamaHub is the open-source registry of data loaders (also called Readers).
- Purpose: ingest data from diverse sources into a unified `Document` representation.
- Notable connectors:
    - `SimpleDirectoryReader`: local files.
    - `NotionPageReader`: Notion pages.
    - `GoogleDocsReader`: Google Docs.
    - `SlackReader`: Slack messages.
    - `DiscordReader`: Discord channels.
    - `ApifyActor`: web crawling, scraping, file downloads.
- LlamaParse: LlamaIndex's managed parsing service for complex document formats (PDF, Word, PowerPoint, Excel, and more). Integrates directly with LlamaIndex. Available as a free managed API.

---

## Node Parsers

- Take a list of Documents and split them into Node objects (chunks).
- Child nodes inherit all attributes (metadata, templates) from the parent Document.

### File-Based Parsers

- `SimpleFileNodeParser`: auto-selects the best parser for each content type. Combine with `FlatFileReader`.
- `HTMLNodeParser`: parses raw HTML using BeautifulSoup. Default tags: `p, h1-h6, li, b, i, u, section`. Tags configurable.
- `JSONNodeParser`: parses raw JSON.
- `MarkdownNodeParser`: parses raw Markdown.

Best practice: chain a file-based parser with a text-based splitter to handle actual text length.

### Text Splitters

- `SentenceSplitter`: respects sentence boundaries. Parameters: `chunk_size`, `chunk_overlap`.
- `TokenTextSplitter`: splits by raw token count. Parameters: `chunk_size`, `chunk_overlap`, `separator`.
- `CodeSplitter`: splits by programming language. Parameters: `language`, `chunk_lines`, `chunk_lines_overlap`, `max_chars`. Supports many languages.
- `SemanticSplitterNodeParser`: adaptive splitting using embedding similarity between sentences. Parameters: `buffer_size`, `breakpoint_percentile_threshold`, `embed_model`. Requires an embedding model. Works best for English text.
- `SentenceWindowNodeParser`: splits into individual sentences; stores a surrounding window of N sentences in metadata (not visible to LLM/embeddings). Use with `MetadataReplacementNodePostProcessor`. Parameters: `window_size`, `window_metadata_key`, `original_text_metadata_key`.
- `LangchainNodeParser`: wraps any LangChain text splitter.
- `Chunker`: wraps chonkie chunkers. Supports aliases for strategies (e.g. `"recursive"`). Can also accept a chonkie instance directly.

### Relation-Based Parsers

- `HierarchicalNodeParser`: splits into multiple levels of chunk sizes (e.g. 2048, 512, 128 tokens). Each node stores a reference to its parent. Used with `AutoMergingRetriever` to automatically replace retrieved children with their parent for richer context.

### Usage Modes

- Standalone: `parser.get_nodes_from_documents(documents)`.
- In IngestionPipeline: pass as a transformation step.
- In index: set `Settings.text_splitter` globally or pass `transformations=[...]` to `from_documents()`.

---

## Ingestion Pipeline

- Applies a sequence of Transformations to input Documents, producing Nodes.
- Each node + transformation combination is hashed and cached to skip redundant reprocessing on subsequent runs.

### Transformation Types

- `TextSplitter` / `NodeParser`
- `MetadataExtractor`
- `EmbeddingModel`
- Custom: subclass `TransformComponent` and implement `__call__(nodes, **kwargs)`.

### Features

- Connect to a vector store: nodes are inserted automatically during pipeline execution. The embedding step must be included if a vector store is attached.
- Supports async execution: `await pipeline.arun(documents=...)`.
- Parallel processing: `pipeline.run(documents=..., num_workers=N)` via multiprocessing.
- Document management: attach a `docstore` to enable deduplication by `doc_id` + hash comparison. Re-processes only changed documents; skips unchanged ones.

### Caching

- Local: `pipeline.persist(path)` / `new_pipeline.load(path)`.
- Clear: `cache.clear()`.
- Remote backends: `RedisCache`, `MongoDBCache`, `FirestoreCache`.

### Global Transformations

- Set globally via `Settings.transformations = [...]` to apply automatically when calling `index.from_documents()` or `index.insert()`.
