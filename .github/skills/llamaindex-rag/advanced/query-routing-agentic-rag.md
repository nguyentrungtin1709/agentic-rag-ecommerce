# Query Routing and Agentic RAG

When a RAG application has multiple data sources or query paths, a routing mechanism is
needed to direct each query to the most appropriate pipeline.

---

## 1. Query Routing

Direct each query to the most appropriate retrieval pipeline based on content and intent.

### Why it is needed

Different information types are best served by different indexes. A single monolithic index
for all data types (structured + unstructured + time-series) performs poorly across all of them.
Multi-index strategies let each index be optimized for its specific content type.

### How routing works

1. Classify the incoming query by content type, domain, or intent.
2. Route to the appropriate index or query engine.
3. Routing decisions can be:
    - **Rule-based**: simple keyword or metadata matching.
    - **LLM-based**: the LLM evaluates query complexity and domain to choose the optimal path.

### Example routing decisions

| Query type | Routed to |
|---|---|
| Fact-based retrieval ("what is X?") | VectorStoreIndex |
| Summarization ("summarize all docs about Y") | SummaryIndex |
| Structured data query ("how many orders in Q3?") | SQL query engine |
| Time-sensitive content | Filtered vector store with recency postprocessing |

### LlamaIndex support

Use `RouterQueryEngine` with `PydanticSingleSelector` or `LLMSingleSelector`:

```python
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import PydanticSingleSelector
from llama_index.core.tools import QueryEngineTool

list_tool = QueryEngineTool.from_defaults(
    query_engine=list_query_engine,
    description="Useful for summarization questions"
)
vector_tool = QueryEngineTool.from_defaults(
    query_engine=vector_query_engine,
    description="Useful for specific context retrieval"
)

query_engine = RouterQueryEngine(
    selector=PydanticSingleSelector.from_defaults(),
    query_engine_tools=[list_tool, vector_tool],
)
```

See `components/querying.md` (Routers section) for the full list of selector types.

---

## 2. Agentic RAG

An advanced form of query routing where a retrieval agent dynamically selects the best
path per query rather than applying a static routing rule.

### How it works

A central routing agent receives the query and can choose from:
- Multiple vector collections or data stores.
- Retrieval strategies: keyword-based, semantic, or hybrid search.
- Query transformation tools (for poorly structured or ambiguous queries).
- Specialized tools or APIs: text-to-SQL converters, web search, calculators, external APIs.

The agent evaluates the query characteristics, selects one or more tools, executes them,
and synthesizes a final response — potentially running multiple retrieval passes.

### Architecture

Agentic RAG functions as a network of specialized sub-agents coordinated by a single
routing agent:

```
User query
    │
    ▼
Routing Agent  ─── selects ──▶  Vector Retriever
                              ▶  SQL Query Engine
                              ▶  Web Search Tool
                              ▶  Query Rewriter
```

### When to use agentic RAG

- The application has multiple distinct data sources or retrieval modes.
- Query intent and complexity vary widely across user queries.
- Static routing rules are insufficient to cover all query types.
- The retrieval path itself needs to be adaptive (retry with different strategy if initial
  retrieval quality is low).

### LlamaIndex support

Build an agentic RAG system using `FunctionAgent` or `ReActAgent` with `QueryEngineTool`
and other tool wrappers. See `components/querying.md` (Agents section) for full details.

---

## Key Design Ideas

- Query routing is a prerequisite for any multi-index RAG application.
- Start with explicit routing rules and upgrade to LLM-based routing only when the number
  of paths grows too large for manual classification.
- Agentic RAG introduces latency due to the additional agent loop; evaluate whether
  the quality gain justifies the cost.
- Always monitor the routing decisions in production to catch systematic misrouting early.
