---
name: llamaindex-rag
description: "Build RAG systems with LlamaIndex covering the full pipeline: data pre-processing, loading, chunking, indexing, storing, query transformation, retrieval, re-ranking, response synthesis, evaluation, and observability. Includes guidance on advanced techniques: hybrid search, re-ranking, ColBERT, ColPali, multimodal RAG, GraphRAG, and local models."
---

# LlamaIndex RAG Skill

Use this skill when:
- Building a Retrieval-Augmented Generation system with LlamaIndex
- Explaining the full RAG workflow to another engineer
- Reviewing or refining a LlamaIndex pipeline at any stage
- Choosing which advanced technique (hybrid search, re-ranking, GraphRAG, etc.) to apply
- Selecting evaluation metrics and observability setup for a LlamaIndex-based system

## How to Use This Skill

This file is the entry point. Each pipeline step has a brief description here and points to
a detailed reference file. Consult the reference file when implementing that step.

- Component reference files: `.github/skills/llamaindex-rag/components/`
- Advanced technique files: `.github/skills/llamaindex-rag/advanced/`
- Pipeline diagram: `.github/skills/llamaindex-rag/diagrams/LlamaIndex-RAG.mermaid`

## Pipeline Overview

The full flow (see diagram for visual representation):

```
Data Sources
  -> Step 0: Pre-Processing  (acquisition, OCR, cleaning, partitioning)
  -> Step 1: Loading         (connectors, Documents, Nodes)
  -> Step 2: Chunking        (strategy selection, ingestion pipeline)
  -> Step 3: Indexing        (embeddings, VectorStoreIndex)
  -> Step 4: Storing         (StorageContext, vector store persistence)

User Query
  -> Step 5: Pre-Retrieval   (query transformation: rewrite / expand / decompose / route)
  -> Step 6: Retrieval       (vector / BM25 / hybrid search)
  -> Step 7: Post-Retrieval  (re-ranking, context selection)
  -> Step 8: Response Synthesis (prompt assembly, LLM call, response modes)

  -> Step 9:  Evaluation     (retrieval quality, generation quality, cost)
  -> Step 10: Observability  (logging, callbacks, tracing)
```

Evaluation feedback applies to all stages. Observability is a cross-cutting concern.

## Step 0: Pre-Processing

Before documents enter LlamaIndex, they must be acquired, parsed, cleaned, and partitioned.
This stage is outside LlamaIndex itself but is required for real-world data.

**Reference**: `advanced/document-preprocessing.md`

Key steps:
- Data acquisition from multiple heterogeneous sources.
- Parsing by source type (OCR for scanned PDFs, HTML DOM traversal, table-aware extraction).
- Cleaning: remove boilerplate, normalize encoding, handle missing values.
- Partitioning: separate content into logical units (paragraphs, sections, tables) before
  chunking. This is distinct from LlamaIndex's `Transformations` step.

Recommended libraries: Unstructured, Docling.

---

## Step 1: Loading

Loading brings the pre-processed content into LlamaIndex as `Document` and `Node` objects.

**Reference**: `components/loading.md`

### Core concepts

- `Connectors` / `Readers`: ingest data from files, DBs, APIs, or LlamaHub readers
- `Document`: a container for a source item and its metadata
- `Node`: a chunk-level unit derived from a document
- `Transformations` / `IngestionPipeline`: chunking, parsing, metadata extraction

### Key design ideas

- Choose readers based on where the data lives.
- Add metadata early if it will matter during retrieval or filtering.
- Treat ingestion as a controlled pipeline, not only as file reading.

```python
from llama_index.core import SimpleDirectoryReader

documents = SimpleDirectoryReader("./data").load_data()
```

```python
from llama_index.core import download_loader
from llama_index.readers.database import DatabaseReader

reader = DatabaseReader(
    scheme=os.getenv("DB_SCHEME"),
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASS"),
    dbname=os.getenv("DB_NAME"),
)
documents = reader.load_data(query="SELECT * FROM users")
```

```python
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import TokenTextSplitter

pipeline = IngestionPipeline(transformations=[TokenTextSplitter()])
nodes = pipeline.run(documents=documents)
```

```python
document = Document(
    text="text",
    metadata={"filename": "<doc_file_name>", "category": "<category>"},
)
```

## Step 2: Chunking Strategies

Chunking converts `Document` objects into `Node` objects via `Transformations` in the
`IngestionPipeline`. Strategy choice has a large impact on retrieval quality.

**Reference**: `advanced/chunking-strategies.md`

| Strategy | Description | LlamaIndex class |
|---|---|---|
| Fixed-size | Fixed token/character count with optional overlap | `TokenTextSplitter`, `SentenceSplitter` |
| Semantic | Group sentences by topic boundary | `SemanticSplitterNodeParser` |
| Recursive | Fall back through natural delimiters | `RecursiveCharacterTextSplitter` |
| Document-structure | Split at heading/section/table boundaries | `MarkdownNodeParser`, `HTMLNodeParser` |
| LLM-based | Use LLM to propose split points | Custom with `LLMNodeParser` |

Advanced option: **Late chunking** — process the full document with a long-context embedding
model first, then split. Preserves document-wide context in each chunk embedding.

---

## Step 3: Indexing

Indexing builds the retrieval structure by embedding nodes and storing them in an index.

**Reference**: `components/indexing.md`
**Embedding fine-tuning**: `advanced/embedding-fine-tuning.md`

### Core concepts

- `Embeddings`: dense numerical representations of semantic meaning
- `VectorStoreIndex`: standard index for vector similarity retrieval
- `top_k`: number of most similar nodes to retrieve per query

### Key design ideas

- Embedding quality impacts retrieval quality directly.
- Domain-specific corpora may benefit from fine-tuned embedding models.
- Chunk size affects both retrieval precision and the context available to the LLM.

```python
from llama_index.core import VectorStoreIndex

index = VectorStoreIndex.from_documents(documents)
# or from nodes directly:
index = VectorStoreIndex(nodes)
```

Configure the global embedding model:

```python
from llama_index.core import Settings
from llama_index.embeddings.openai import OpenAIEmbedding

Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")
```

---

## Step 4: Storing

Storing persists the index so embeddings do not need to be recomputed on each run.

**Reference**: `components/storing.md`

### Core concepts

- `StorageContext`: holds vector store, doc store, index store
- `persist` / `load_index_from_storage`: disk-based persistence
- Pluggable vector stores: Chroma, Qdrant, Pinecone, Weaviate, and others

```python
# Persist
index.storage_context.persist(persist_dir="<persist_dir>")

# Load
from llama_index.core import StorageContext, load_index_from_storage
storage_context = StorageContext.from_defaults(persist_dir="<persist_dir>")
index = load_index_from_storage(storage_context)
```

Use Chroma as persistent vector store:

```python
import chromadb
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

db = chromadb.PersistentClient(path="./chroma_db")
chroma_collection = db.get_or_create_collection("quickstart")
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_documents(documents, storage_context=storage_context)
```

---

## Step 5: Pre-Retrieval — Query Transformation

Raw user queries often yield suboptimal retrieval results. Transform the query before
passing it to the retriever.

**Reference**: `advanced/query-transformation.md`
**Routing and agentic RAG**: `advanced/query-routing-agentic-rag.md`

| Technique | Description | LlamaIndex component |
|---|---|---|
| Query rewriting | Rephrase into a more retrieval-friendly form | `TransformQueryEngine` |
| Query expansion | Generate N similar queries; merge retrieved results | `QueryFusionRetriever` |
| Query decomposition | Break multi-hop queries into sub-queries | `MultiStepQueryEngine` |
| Query routing | Direct queries to the appropriate pipeline or index | `RouterQueryEngine` |
| Agentic RAG | Agent dynamically selects retrieval strategy and tools | `ReActAgent` with retrieval tools |

Query expansion:

```python
from llama_index.core.retrievers import QueryFusionRetriever

retriever = QueryFusionRetriever(
    retrievers=[vector_retriever],
    similarity_top_k=5,
    num_queries=4,
    use_async=True,
)
```

Query decomposition:

```python
from llama_index.core.query_engine import MultiStepQueryEngine

query_engine = MultiStepQueryEngine(
    query_engine=base_query_engine,
    query_transform=step_decompose_transform,
    num_steps=3,
)
```

---

## Step 6: Retrieval

The retriever finds relevant nodes from the index for the (possibly transformed) query.

**Hybrid search**: `advanced/hybrid-search.md`
**Distance filtering and autocut**: `advanced/vector-search-filtering.md`
**ColBERT late interaction**: `advanced/colbert.md`

| Retrieval mode | Description | When to use |
|---|---|---|
| Vector (semantic) | Cosine similarity on dense embeddings | General semantic search |
| Keyword (BM25) | TF-IDF/BM25 exact term matching | Terminology-heavy queries |
| Hybrid (vector + BM25) | Weighted fusion via alpha parameter | Queries needing conceptual + exact match |
| ColBERT | Token-level MaxSim late interaction | High-precision retrieval scenarios |

Vector retriever:

```python
from llama_index.core.retrievers import VectorIndexRetriever

retriever = VectorIndexRetriever(index=index, similarity_top_k=10)
```

Hybrid retriever:

```python
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.retrievers import QueryFusionRetriever

bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=10)
fusion_retriever = QueryFusionRetriever(
    retrievers=[vector_retriever, bm25_retriever],
    similarity_top_k=5,
    mode="reciprocal_rerank",
)
```

---

## Step 7: Post-Retrieval

After retrieval, refine the candidate set before handing it to the synthesizer.

**Re-ranking**: `advanced/reranking.md`
**Sentence window retrieval**: `advanced/sentence-window-retrieval.md`
**All postprocessors**: `components/querying.md`

| Postprocessor | Purpose | LlamaIndex class |
|---|---|---|
| Similarity cutoff | Remove nodes below a similarity threshold | `SimilarityPostprocessor` |
| Cross-encoder re-rank | Re-score with a more accurate model | `CohereRerank`, `SentenceTransformerRerank`, `LLMRerank` |
| Metadata replacement | Replace sentence with its surrounding window | `MetadataReplacementPostProcessor` |

Re-ranking example:

```python
from llama_index.postprocessor.cohere_rerank import CohereRerank

reranker = CohereRerank(api_key="...", top_n=5)
query_engine = index.as_query_engine(node_postprocessors=[reranker])
```

Similarity cutoff:

```python
from llama_index.core.postprocessor import SimilarityPostprocessor

postprocessor = SimilarityPostprocessor(similarity_cutoff=0.7)
```

---

## Step 8: Response Synthesis

The response synthesizer combines the query and retrieved nodes into the final LLM response.

**Reference**: `components/querying.md`

| Response mode | Description |
|---|---|
| `default` | Refine iteratively across nodes |
| `compact` | Compact nodes into fewer prompts |
| `tree_summarize` | Build a tree of summaries |
| `no_text` | Return retrieved nodes only |
| `accumulate` | Collect per-node answers and concatenate |

Basic query:

```python
query_engine = index.as_query_engine()
response = query_engine.query("What does the document say about X?")
print(response)
```

Custom query engine:

```python
from llama_index.core import get_response_synthesizer
from llama_index.core.query_engine import RetrieverQueryEngine

response_synthesizer = get_response_synthesizer(response_mode="compact")
query_engine = RetrieverQueryEngine(
    retriever=retriever,
    response_synthesizer=response_synthesizer,
    node_postprocessors=[reranker],
)
response = query_engine.query("Your question here")
```

Configure the global LLM:

```python
from llama_index.core import Settings
from llama_index.llms.openai import OpenAI

Settings.llm = OpenAI(model="gpt-4o", temperature=0.0)
```

---

## Step 9: Evaluation

Evaluation measures both retrieval quality and generation quality independently.

**LlamaIndex built-in**: `components/evaluation.md`
**Extended metrics with Ragas**: `advanced/evaluation-ragas.md`

| Metric | LlamaIndex built-in | Ragas |
|---|---|---|
| Faithfulness | Yes | Yes |
| Answer relevance | Partial | Yes |
| Context relevance | No | Yes |
| Answer correctness | Limited | Yes |
| Context recall | No | Yes |
| Context precision | No | Yes |
| Retrieval MRR / hit rate | Yes | No |
| Synthetic dataset generation | No | Yes |

Faithfulness evaluation:

```python
from llama_index.core.evaluation import FaithfulnessEvaluator
from llama_index.llms.openai import OpenAI

evaluator = FaithfulnessEvaluator(llm=OpenAI(model="gpt-4", temperature=0.0))
eval_result = evaluator.evaluate_response(response=response)
print(eval_result.passing)
```

Retrieval evaluation:

```python
from llama_index.core.evaluation import RetrieverEvaluator

retriever_evaluator = RetrieverEvaluator.from_metric_names(
    ["mrr", "hit_rate"], retriever=retriever
)
retriever_evaluator.evaluate(query="query", expected_ids=["node_id1", "node_id2"])
```

Token usage estimation:

```python
from llama_index.core.llms import MockLLM
from llama_index.core import MockEmbedding
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
import tiktoken

token_counter = TokenCountingHandler(
    tokenizer=tiktoken.encoding_for_model("gpt-3.5-turbo").encode
)
Settings.llm = MockLLM(max_tokens=256)
Settings.embed_model = MockEmbedding(embed_dim=1536)
Settings.callback_manager = CallbackManager([token_counter])
```

---

## Step 10: Observability

Observability is a cross-cutting concern across all pipeline stages.

**Reference**: `components/observability.md`

Enable basic debug logging:

```python
import logging, sys
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))
```

Set a simple global callback handler:

```python
import llama_index.core
llama_index.core.set_global_handler("simple")
```

---

## Advanced Techniques

Apply these when the baseline pipeline does not meet quality requirements. Validate each
technique independently before combining multiple.

### Hybrid Search
Combine vector and BM25 retrieval. Use when queries need both semantic understanding and
exact keyword matching.
Reference: `advanced/hybrid-search.md`

### Re-ranking
Two-stage retrieval: retrieve a large candidate set, re-score with a cross-encoder.
LlamaIndex classes: `CohereRerank`, `SentenceTransformerRerank`, `LLMRerank`.
Reference: `advanced/reranking.md`

### Sentence Window Retrieval
Index at sentence granularity for precise retrieval, expand to surrounding context at
generation time. LlamaIndex: `SentenceWindowNodeParser` + `MetadataReplacementPostProcessor`.
Reference: `advanced/sentence-window-retrieval.md`

### Multimodal RAG
Retrieve and reason over images alongside text using CLIP embeddings and multimodal LLMs.
Reference: `advanced/multimodal-rag.md`

### ColBERT
Late interaction retrieval using token-level MaxSim scoring. Higher accuracy than
single-vector bi-encoders. Library: RAGatouille.
Reference: `advanced/colbert.md`

### ColPali
Page-image retrieval using ViT patch embeddings and ColBERT late interaction. Avoids text
extraction for scanned or visually complex documents. Requires MultiVector support in the
vector store (Qdrant).
Reference: `advanced/colpali.md`

### GraphRAG
Knowledge-graph-backed retrieval enabling multi-hop reasoning and long-range entity
connections. Uses `LLMSynonymRetriever` and `VectorContextRetriever`.
Reference: `advanced/graphrag.md`

### Local Models with Ollama
Run LLM and embedding models locally via Ollama. Drop-in replacement for hosted APIs
through LlamaIndex Settings. Packages: `llama-index-llms-ollama`, `llama-index-embeddings-ollama`.
Reference: `advanced/local-models-ollama.md`

### Embedding Fine-Tuning
Fine-tune embedding models on domain-specific data to improve retrieval quality for
specialized vocabulary.
Reference: `advanced/embedding-fine-tuning.md`

---

## LlamaIndex Component Reference

| Component group | Reference file | Key classes |
|---|---|---|
| Models (LLMs + Embeddings) | `components/models.md` | `OpenAI`, `Anthropic`, `OllamaEmbedding`, `OpenAIEmbedding` |
| Prompts | `components/prompts.md` | `PromptTemplate`, `ChatPromptTemplate` |
| Loading | `components/loading.md` | `SimpleDirectoryReader`, `IngestionPipeline`, `Document`, `Node` |
| Indexing | `components/indexing.md` | `VectorStoreIndex`, `SummaryIndex`, `KnowledgeGraphIndex` |
| Storing | `components/storing.md` | `StorageContext`, `ChromaVectorStore`, `QdrantVectorStore` |
| Querying | `components/querying.md` | `QueryEngine`, `Retriever`, `NodePostprocessor`, `ResponseSynthesizer`, `Router` |
| Evaluation | `components/evaluation.md` | `FaithfulnessEvaluator`, `RetrieverEvaluator`, `CorrectnessEvaluator` |
| Observability | `components/observability.md` | `CallbackManager`, `TokenCountingHandler`, `set_global_handler` |
| Settings | `components/settings.md` | `Settings` (global LLM, embed model, callbacks) |

---

## Practical Guidance

1. **Start simple.** Use `SimpleDirectoryReader`, `VectorStoreIndex`, and
   `index.as_query_engine()` for a working baseline before adding complexity.
2. **Evaluate before optimizing.** Run faithfulness and context relevance metrics first.
   Fix the weakest link; do not layer advanced techniques onto an unmeasured baseline.
3. **One technique at a time.** Add hybrid search, re-ranking, or query expansion one at a
   time and measure impact. Combining multiple techniques without evaluation makes it
   impossible to attribute improvements.
4. **Persist embeddings.** Always use `StorageContext` to avoid recomputing embeddings
   between runs.
5. **Add observability early.** Enable the simple callback handler and debug logging before
   scaling; traces are much easier to interpret at low query volume.
6. **Match chunking to retrieval granularity.** Small chunks improve retrieval precision;
   sentence window retrieval recovers context for synthesis. Fixed-size chunking is rarely
   optimal for complex documents.
7. **Use local models during development.** Ollama eliminates API costs and rate-limit
   friction during iteration. Switch to hosted models for production evaluation.

---

## Limits of This Skill

- This skill covers the standard vector-based LlamaIndex RAG workflow and the most common
  advanced techniques.
- It does not cover every LlamaIndex index type, every vectorstore integration, or every
  managed cloud deployment path.
- For production deployments, consult the official LlamaIndex documentation for the exact
  API surface and version compatibility of specific components.
- Advanced techniques (ColBERT, ColPali, GraphRAG) require external libraries (RAGatouille,
  Qdrant, PyTorch Geometric) that have their own setup requirements.