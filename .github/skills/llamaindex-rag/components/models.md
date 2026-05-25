# Models

## LLMs

- Unified interface covering OpenAI, HuggingFace, Anthropic, LangChain, and many more providers.
- Support:
    - Text completion and chat endpoints.
    - Streaming and non-streaming.
    - Synchronous and asynchronous.
- Usage roles:
    - Always used during response synthesis.
    - May also be used during index construction, insertion, and query traversal depending on index type.
    - Can be used as standalone modules.
- Configuration:
    - Global default via `Settings.llm`.
    - Local override per query engine or chat engine by passing `llm=...` argument.
    - Default model: OpenAI `gpt-3.5-turbo`.
- Custom LLM:
    - Subclass `CustomLLM` and implement `complete()` and `stream_complete()`.
    - Must define `metadata` property returning `LLMMetadata` (context_window, num_output, model_name).
    - Decorator `@llm_completion_callback()` adds observability via callbacks (optional).

### Tokenization

- Default tokenizer: `cl100k` from tiktoken (matches `gpt-3.5-turbo`).
- Changing the LLM requires updating the tokenizer to ensure accurate token counting and chunking.
- Tokenizer: any callable that takes a string and returns a list.
- Set via `Settings.tokenizer`.
- Supported tokenizers: tiktoken (OpenAI models), HuggingFace `AutoTokenizer` (open-source models).

---

## Embeddings

- Convert text into numerical vectors capturing semantic meaning.
- Default model: `text-embedding-ada-002` from OpenAI.
- Default similarity method: cosine similarity.
- Configuration:
    - Global via `Settings.embed_model`.
    - Per-index by passing `embed_model=...` to `VectorStoreIndex.from_documents()`.
    - Can also be called standalone for single or batch embeddings.
- Customization:
    - Batch size: `embed_batch_size` parameter (default: 10); tune for rate limits or throughput.

### Local Embedding Models

- Use `HuggingFaceEmbedding` from `llama-index-embeddings-huggingface`.
- Supports any Sentence Transformers model from HuggingFace.
- Additional kwargs pass through to the underlying `SentenceTransformer` instance (e.g. `backend`, `model_kwargs`, `truncate_dim`, `revision`).

### ONNX / OpenVINO Optimizations

- Accelerate local inference on CPUs or GPUs via Sentence Transformers + Optimum.
- Backends: `onnxruntime`, `onnxruntime-gpu`, `openvino`.
- Set `backend="onnx"` or `backend="openvino"` in `HuggingFaceEmbedding`.
- If the model repository lacks the optimized format, it is auto-converted using Optimum.

### Other Integrations

- LangChain embeddings: any embedding from LangChain can be used via `llama-index-embeddings-langchain`.
- Custom embedding model: subclass `BaseEmbedding` and implement `_get_query_embedding()`, `_get_text_embedding()`, `_get_text_embeddings()`, and their async variants.

---

## Multi-Modal Models (Experimental)

- Base abstraction: `MultiModalLLM` for text + image input, text output.
- Supports models such as GPT-4V.
- Image loading:
    - From URL list via `load_image_urls()`.
    - From local directory via `SimpleDirectoryReader`.
- `MultiModalVectorStoreIndex`:
    - Stores separate text and image vector collections (e.g. in Qdrant).
    - Constructed via `StorageContext` with both `vector_store` (text) and `image_store` (image).
- Multi-modal retriever:
    - Configurable `similarity_top_k` (text) and `image_similarity_top_k` (images).
    - `text_to_image_retrieve()` for image-only retrieval.
- `SimpleMultiModalQueryEngine`: unified query engine for mixed text+image data.
