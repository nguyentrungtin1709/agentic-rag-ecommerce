# Storing

## Concept

- LlamaIndex has a swappable, modular storage layer.
- All storage is managed through a `StorageContext` object.
- `StorageContext` bundles together document store, index store, vector store, and optionally graph store and chat store.

---

## Storage Components

### Document Store

- Persists ingested Node objects.
- Used by indexes that do not offload storage entirely to a vector database.
- Implementations:
    - `SimpleDocumentStore`: in-memory; can persist to and load from local disk via `docstore.persist()`.
    - `MongoDocumentStore`: MongoDB-backed; auto-persists.
    - `RedisDocumentStore`: Redis-backed.
    - `FirestoreDocumentStore`: Google Cloud Firestore.
    - `CouchbaseDocumentStore`: Couchbase.

### Index Store

- Persists index metadata (structure, node references).
- `SimpleIndexStore`: default in-memory store; persisted via `StorageContext`.

### Vector Store

- Stores embedding vectors (and often the associated text).
- Many vector store integrations store everything (text + embeddings) internally, eliminating the need for a separate document or index store.
- Vector stores with full self-managed storage (no separate docstore needed):
    - `ChromaVectorStore`, `PineconeVectorStore`, `QdrantVectorStore`, `WeaviateVectorStore`,
    - `MilvusVectorStore`, `RedisVectorStore`, `CassandraVectorStore`, `OpensearchVectorStore`,
    - `LanceDBVectorStore`, `UpstashVectorStore`, `AzureAISearchVectorStore`, and more.
- For these, data is persisted automatically without calling `storage_context.persist()`.

### Property Graph Store

- Stores knowledge graph data for `PropertyGraphIndex`.

### Chat Store

- Stores and organizes chat message history.

---

## Storage Backends (for local stores)

- Local filesystem
- AWS S3
- Cloudflare R2
- Any fsspec-compatible backend.

---

## Persistence

### Saving

```python
index.storage_context.persist(persist_dir="./storage")
```

- To save multiple indexes to the same directory, set a unique index ID first:
  `index.set_index_id("my_index")` then persist.

### Loading

```python
storage_context = StorageContext.from_defaults(persist_dir="./storage")
index = load_index_from_storage(storage_context)
# or with a specific index ID:
index = load_index_from_storage(storage_context, index_id="my_index")
# or load multiple:
indexes = load_index_from_storage(storage_context, index_ids=["id1", "id2"])
```

---

## Low-Level Usage Pattern

Use `StorageContext.from_defaults(docstore=..., vector_store=..., index_store=...)` to assemble a custom storage context, then pass it to `VectorStoreIndex(nodes, storage_context=storage_context)`.

For vector store integrations: build from existing data via `VectorStoreIndex.from_vector_store(vector_store)`.
