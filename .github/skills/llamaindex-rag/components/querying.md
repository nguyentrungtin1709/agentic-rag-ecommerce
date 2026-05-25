# Querying

## Query Engine

- High-level interface: takes a natural language query, returns a rich response.
- Built on a Retriever + Response Synthesizer pipeline.
- Can compose multiple query engines for advanced workflows.

### Configuration

- High-level API: `index.as_query_engine(response_mode=..., streaming=..., verbose=..., llm=...)`.
- Low-level API: manually compose `VectorIndexRetriever` + `get_response_synthesizer()` + `RetrieverQueryEngine`.
- Custom query engine: subclass `CustomQueryEngine`, add Pydantic attributes, implement `custom_query(query_str)`.

### Response Modes

- `compact` (default): packs as many retrieved chunks as possible into the context window, then refines across parts if needed. Fewer LLM calls than `refine`.
- `refine`: processes each retrieved chunk sequentially; generates/refines an answer with each one. More LLM calls; best for detailed answers.
- `tree_summarize`: recursively summarizes all chunks in a tree structure. Good for summarization tasks.
- `simple_summarize`: truncates all chunks to fit into a single LLM call. Fast but may lose detail.
- `no_text`: retrieves nodes without calling the LLM; use to inspect `response.source_nodes`.
- `context_only`: returns all retrieved text concatenated, without LLM synthesis.
- `accumulate`: runs the same query independently against each chunk; returns concatenated answers.
- `compact_accumulate`: same as `accumulate` but packs prompts like `compact`.

### Streaming

- Enable with `streaming=True` on the query engine.
- Returns a `StreamingResponse`; iterate via `response_gen` or call `print_response_stream()`.
- Supported by: OpenAI, HuggingFace LLM, most LangChain LLMs.
- In multi-LLM-call scenarios, only the last LLM call is streamed.

### Query Transformations

- `HyDEQueryTransform`: generates a hypothetical answer document first, then uses it for embedding lookup. Useful when the query phrasing differs from document phrasing.
- `StepDecomposeQueryTransform`: breaks a complex query into sequential sub-queries; runs each against the index and accumulates context.
- Use via `TransformQueryEngine` or `MultiStepQueryEngine` wrappers.

---

## Chat Engine

- Stateful, conversation-aware interface. Maintains chat history across turns.
- Methods: `chat(msg)`, `stream_chat(msg)`, `astream_chat(msg)`, `reset()`, `chat_repl()`.

### Chat Modes

- `best`: auto-selects ReAct or OpenAI agent depending on LLM support. Default.
- `condense_question`: rewrites each user message using chat history, then queries the index.
- `context`: retrieves relevant nodes on every message; inserts them into the system prompt.
- `condense_plus_context`: combines `condense_question` and `context`.
- `simple`: direct LLM conversation, no retrieval.
- `react`: forces ReAct agent.
- `openai`: forces OpenAI function-calling agent.

### Streaming

- Synchronous: `stream_chat()` returns a response with `response_gen` iterator.
- Asynchronous: `await astream_chat()` with `async for token in response.async_response_gen()`.

---

## Retriever

- Retrieves the most relevant Nodes for a query.
- Built on top of an index or defined independently.

### Retriever Modes by Index Type

- `VectorStoreIndex`: always `VectorIndexRetriever` (mode parameter is ignored).
- `SummaryIndex`: `default` (all nodes), `embedding` (top-k embedding), `llm` (LLM-selected).
- `TreeIndex`: `select_leaf`, `select_leaf_embedding`, `all_leaf`, `root`.
- `KeywordTableIndex`: `default` (GPT-based), `simple`, `rake`.
- `KnowledgeGraphIndex`: `keyword`, `embedding`, `hybrid`.
- `DocumentSummaryIndex`: `llm`, `embedding`.

### Advanced Retrieval Techniques

- BM25 hybrid retrieval (keyword + dense).
- Reciprocal rerank fusion across multiple retrievers.
- Query fusion: expands a query into multiple sub-queries, retrieves for each, and merges results.
- Auto-merging retrieval: automatically replaces retrieved child nodes with parent nodes when a majority of children are retrieved (used with `HierarchicalNodeParser`).
- Metadata replacement: replaces node text with a metadata field before synthesis (used with `SentenceWindowNodeParser`).
- Auto-retrieval: semi-structured queries combining semantic search with metadata filters.
- Router retriever: routes to different retrievers based on query.
- Ensemble retriever: combines results from multiple retrievers.
- Multi-doc retrieval, composable retrievers.
- Managed retrievers: Google, Vectara, Amazon Bedrock, VideoDB.
- Text-to-SQL retrieval for structured data.

---

## Response Synthesizer

- Generates a final response from a query and a set of retrieved Nodes.
- Response modes are the same set as listed under Query Engine above.
- Can be used standalone or plugged into a `RetrieverQueryEngine`.

### Additional Options

- `structured_answer_filtering=True`: uses LLM (preferably function-calling) to filter out irrelevant nodes before synthesis. Best with OpenAI models.
- Custom prompts with extra variables: pass additional kwargs to `get_response(**kwargs)` or `synthesize(**kwargs)`.
- Custom synthesizer: subclass `BaseSynthesizer`, implement `get_response()` and `aget_response()`.
- Async synthesis: `await synthesizer.asynthesize(...)`.

---

## Routers

- Use an LLM or Pydantic selector to pick one or more options from a set of labeled choices.
- Useful for selecting between multiple query engines, retrievers, or data sources.

### Selector Types

- `LLMSingleSelector` / `LLMMultiSelector`: decision via text completion API.
- `PydanticSingleSelector` / `PydanticMultiSelector`: decision via function calling API (more reliable structured output).

### Use Cases

- `RouterQueryEngine`: routes a query to the most appropriate query engine tool.
- `RouterRetriever`: routes to the most appropriate retriever.
- `ToolRetrieverRouterQueryEngine`: for very large tool sets where the tools themselves need to be retrieved (beta).
- Standalone selector: pass choices as `ToolMetadata` objects or plain strings; returns `selector_result.selections`.

---

## Node Postprocessors

- Applied after retrieval and before response synthesis to filter or transform nodes.

### Built-in Postprocessors

- `SimilarityPostprocessor`: removes nodes below a similarity score threshold (`similarity_cutoff`).
- `KeywordNodePostprocessor`: keeps nodes that contain required keywords; removes nodes with excluded keywords.
- `MetadataReplacementPostProcessor`: replaces node text with a specified metadata field (used with `SentenceWindowNodeParser` to expand context).
- `LongContextReorder`: reorders nodes so that the most relevant content appears at the start and end of the context (addresses "lost in the middle" problem).
- `SentenceEmbeddingOptimizer`: removes low-relevance sentences from each node based on embedding similarity to the query. Configurable via `percentile_cutoff` or `threshold_cutoff`.
- `CohereRerank`: reranks nodes using Cohere's rerank API; returns top N.
- `SentenceTransformerRerank`: reranks using cross-encoder models from sentence-transformers; returns top N.
- `LLMRerank`: reranks nodes using an LLM; returns top N with relevance scores.
- `JinaRerank`: reranks nodes using Jina's rerank API; returns top N.
- `FixedRecencyPostprocessor`: returns top-K nodes sorted by a date metadata field.
- `EmbeddingRecencyPostprocessor`: sorts by date, then deduplicates by embedding similarity.
- `TimeWeightedPostprocessor`: applies time-weighted scoring; biases toward nodes not recently retrieved.
- `PIINodePostprocessor` / `NERPIINodePostprocessor`: removes personally identifiable information using LLM or HuggingFace NER model (beta).
- `PrevNextNodePostprocessor`: fetches adjacent nodes via PREVIOUS/NEXT relationships (beta).

### Custom Postprocessor

- Subclass `BaseNodePostprocessor` and implement `_postprocess_nodes(nodes, query_bundle)`.

---

## Structured Outputs

- Methods for making LLMs return structured data:

### Pydantic Programs

- Map an input prompt to a structured Pydantic object.
- Types:
    - Text Completion Pydantic Programs: use text completion + output parser.
    - Function Calling Pydantic Programs: use LLM function calling API (OpenAI, Guidance); more reliable.
    - Prepackaged programs: `DFProgram` for DataFrames, `EvaporateProgram` for information extraction.

### Output Parsers

- Operate before (format instructions) and after (parsing) a text completion LLM call.
- Integrations:
    - `GuardrailsOutputParser`: uses Guardrails' RAIL schema for specification, validation, and correction.
    - `LangchainOutputParser`: wraps LangChain output parsers (e.g. `StructuredOutputParser`).

---

## Agents

- An agent combines an LLM, memory, and tools to autonomously handle user inputs with a loop of: receive message -> decide tool calls or respond -> execute tools -> repeat.

### Agent Types

- `FunctionAgent`: uses LLM provider's function/tool calling capabilities.
- `ReActAgent`: uses the ReAct prompting strategy (reason + act).
- `CodeActAgent`: uses code execution as the action mechanism.
- `AgentWorkflow`: orchestrates multiple agents in a multi-agent system with handoff capability.

### Tools

- Define as plain Python functions (type annotations + docstring are used as schema).
- `FunctionTool`: wraps any callable with additional configuration.
- `QueryEngineTool`: wraps a query engine as a tool.
- Tool Specs: pre-defined tool sets for common APIs.

### Memory

- Default: `ChatMemoryBuffer` (token-limited FIFO queue of chat history). Being replaced by `Memory` class.
- `Memory` class:
    - Short-term: FIFO queue up to `token_limit`. Configurable: `token_limit`, `chat_history_token_ratio`, `token_flush_size`.
    - Long-term memory blocks (optional):
        - `StaticMemoryBlock`: always-present static content.
        - `FactExtractionMemoryBlock`: extracts and maintains facts from flushed history using an LLM. Configurable `max_facts`.
        - `VectorMemoryBlock`: stores and retrieves batches of messages from a vector database. Configurable `similarity_top_k`, `retrieval_context_window`, `node_postprocessors`.
    - Long-term blocks specify `priority` to control insertion order.
- Pass memory via `agent.run(..., memory=memory)` or retrieve from context after a run.

### Multi-Modal Agents

- Pass `ChatMessage` with `ImageBlock` + `TextBlock` content to agents backed by multi-modal LLMs.

### Manual Agent Loop

- Use `llm.chat_with_tools()`, `llm.get_tool_calls_from_response()`, and a while loop for full custom control over tool execution and error handling.
