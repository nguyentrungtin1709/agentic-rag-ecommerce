# Indexing

## Concept

- An Index is a data structure for efficient retrieval of relevant context given a user query.
- Indexes are built from Documents (split internally into Nodes).
- Expose a Retriever interface for querying, and are used to build Query Engines and Chat Engines.
- Most common: `VectorStoreIndex`.

---

## Index Types

### VectorStoreIndex

- Stores each Node together with its embedding in a vector store.
- Queries by fetching top-k most similar Nodes using embedding similarity.
- Most common and general-purpose index for RAG.

### SummaryIndex (formerly ListIndex)

- Stores Nodes as a sequential list.
- At query time, loads all Nodes into the response synthesis module by default.
- Also supports embedding-based top-k retrieval or keyword filter-based retrieval.

### TreeIndex

- Builds a hierarchical tree from Nodes (leaf nodes) upward.
- Queries by traversing from root to leaves.
- `child_branch_factor`: number of children to follow per level (default: 1).

### KeywordTableIndex

- Extracts keywords from each Node and builds a keyword-to-node mapping.
- Queries by extracting keywords from the query and matching to pre-indexed Node keywords.

### PropertyGraphIndex

- Builds a knowledge graph with labeled nodes and typed relations.
- Construction options:
    - LLM-based extraction (flexible, open schema).
    - Strict schema extraction.
    - Custom extraction module.
- Nodes can optionally be embedded for vector retrieval.
- Can connect to existing knowledge graphs (e.g. Neo4j).
- Queries using: keyword + synonym expansion, vector retrieval (if embedded), or both combined.
- Can include original source text in addition to retrieved triples.

### LlamaCloudIndex (Managed)

- Cloud-managed index via LlamaCloud service.
- Managed Ingestion API: handles parsing and document management.
- Managed Retrieval API: configures optimal retrieval for RAG.
- Retriever settings: `dense_similarity_top_k`, `sparse_similarity_top_k`, `enable_reranking`, `rerank_top_n`, `alpha` (dense vs. sparse weighting).
- `LlamaCloudCompositeRetriever`: queries across multiple LlamaCloud indexes with optional reranking.
    - Modes: `FULL` (query all indexes + global rerank) or `ROUTING` (route to most relevant index based on description).

---

## Document Management

All index types (except most external vector store integrations) support the following operations:

- `index.insert(document)`: add a new Document; it is parsed into Nodes and ingested.
- `index.delete_ref_doc(doc_id, delete_from_docstore=False)`: remove all Nodes for a document.
- `index.update_ref_doc(document)`: replace content for a document with the same `doc_id`.
- `index.refresh_ref_docs(documents)`: batch update; only re-processes documents whose content has changed; inserts new documents; returns a boolean list indicating which were refreshed.
- `index.ref_doc_info`: inspect all tracked documents and their associated node IDs.

---

## How Indexes Work (Internal Behavior Summary)

| Index | Storage | Query Mechanism |
|---|---|---|
| VectorStoreIndex | Nodes + embeddings in vector store | Top-k embedding similarity |
| SummaryIndex | Sequential node list | All nodes (or top-k embedding / keyword filter) |
| TreeIndex | Hierarchical tree | Root-to-leaf traversal |
| KeywordTableIndex | Keyword-to-node map | Keyword matching |
| PropertyGraphIndex | Knowledge graph | Keyword/synonym expansion + vector retrieval |
