# Settings

## Concept

- `Settings` is a global singleton that holds default configurations for the entire LlamaIndex application.
- Components use `Settings` as a fallback when no local configuration is provided.
- Local overrides (per-index, per-query engine) always take precedence over global defaults.

---

## Configurable Attributes

### LLM

- `Settings.llm`: the LLM used for response generation, query traversal, and all LLM-dependent operations.
- Default: OpenAI `gpt-3.5-turbo`.

### Embedding Model

- `Settings.embed_model`: the embedding model used to convert text to vectors.
- Default: OpenAI `text-embedding-ada-002`.

### Node Parser / Text Splitter

- `Settings.text_splitter`: the node parser used when splitting documents into chunks.
- Shortcuts:
    - `Settings.chunk_size`: sets chunk size on the default splitter.
    - `Settings.chunk_overlap`: sets chunk overlap on the default splitter.

### Transformations

- `Settings.transformations`: a list of `TransformComponent` objects applied during `from_documents()` and `insert()`.
- Overrides the default text splitter if explicitly set.

### Tokenizer

- `Settings.tokenizer`: a callable that takes a string and returns a list of tokens. Used for token counting throughout the framework.
- Must match the LLM being used.
- OpenAI example: `tiktoken.encoding_for_model("gpt-3.5-turbo").encode`.
- HuggingFace example: `AutoTokenizer.from_pretrained("model-name")`.

### Callback Manager

- `Settings.callback_manager`: a `CallbackManager` instance with attached handlers for observability and token counting.

### Prompt Helper Arguments

- `Settings.context_window`: maximum input tokens for the LLM. Normally inferred from LLM metadata; override only when needed (default: 4096).
- `Settings.num_output`: number of output tokens reserved for generation (default: 256).

---

## Local Override Pattern

Configuration can be passed directly to any interface that uses it, overriding global defaults for that specific call:

```python
index = VectorStoreIndex.from_documents(
    documents, embed_model=my_embed_model, transformations=[my_splitter]
)
query_engine = index.as_query_engine(llm=my_llm)
```
