# GraphRAG — Graph-Based Retrieval-Augmented Generation

GraphRAG replaces flat vector retrieval with a knowledge-graph-backed retrieval strategy,
enabling multi-hop reasoning and long-range relationship traversal.

---

## Limitations of Standard Vector RAG

| Problem | Description |
|---|---|
| Flat retrieval | Vector similarity returns isolated chunks; relational structure is discarded |
| Long-range connections | A fact in chunk 1 and a related fact in chunk 50 are never linked |
| Multi-hop queries | "What did person A say about topic X, and how does it relate to person B's work on Y?" cannot be answered by single-chunk retrieval |
| Explainability | Retrieved chunks do not show why they are related to each other |
| Context overlap | Multiple chunks may say the same thing, wasting context window tokens |

---

## Knowledge Graph Construction

1. Load documents from sources.
2. Use an LLM to extract **entities** and **relationships** from the text, producing
   triples: `entity → relation → entity`.
   - Example: `("Albert Einstein", "developed", "General Relativity")`
3. Store the triples in a graph database.
4. Optionally generate text embeddings for each entity node and relationship.

---

## Knowledge Graph Summarization

After building the graph:
1. Cluster the graph into **communities** — groups of entities that are closely related.
2. Generate hierarchical summaries:
   - **High-level community summaries**: broad overviews for general queries.
   - **Detailed sub-graph summaries**: focused details for specific queries.

This mirrors the "global" and "local" search modes described in the Microsoft GraphRAG paper.
- Global search: uses high-level community summaries for broad, thematic questions.
- Local search: traverses specific graph neighborhoods for targeted, entity-specific questions.

---

## Query Strategies

### Entity-Based Retrieval (LLMSynonymRetriever)
1. Extract named entities or key topics from the user query.
2. Use an LLM to expand the entities with synonyms and related terms.
3. Query the knowledge graph by matching expanded entity names.
4. Retrieve the graph neighborhood (connected entities and relationships) around matched entities.

### Vector-Based Retrieval (VectorContextRetriever)
1. Embed the user query.
2. Find semantically similar entity nodes or community summary embeddings in the graph.
3. Retrieve the context around the most similar nodes.

### Combined Strategy
Run both LLMSynonymRetriever and VectorContextRetriever in parallel. Merge the retrieved
subgraphs, re-rank, and filter before passing to the synthesizer. This covers both
entity-name matches and semantic similarity matches.

---

## Full GraphRAG Implementation Steps

1. Load documents from heterogeneous sources.
2. Extract entities, relationships, and optionally generate text embeddings for nodes.
3. Store triples and embeddings in a graph database.
4. Build community structure and generate hierarchical summaries.
5. At query time: run LLMSynonymRetriever and/or VectorContextRetriever.
6. Re-rank and filter retrieved subgraph context.
7. Construct the prompt: system prompt + user query + retrieved graph context.
8. Generate response with the LLM.

---

## Graph Neural Networks (Background)

GNNs are the ML foundation for learning on graph-structured data. Relevant for understanding
why graph structure helps and how embedding-based graph operations work.

### Graph Structure
- **Nodes**: represent entities (people, documents, concepts, products).
- **Edges**: represent relationships between entities.

### Task Levels

| Task level | Description | RAG relevance |
|---|---|---|
| Node-level | Classify or embed individual nodes | Entity disambiguation, entity relevance scoring |
| Edge-level | Predict or classify relationships | Link prediction between entities |
| Graph-level | Classify or cluster whole graphs | Document topic classification, community detection |

### Graph Convolutional Networks (GCN)
GCNs aggregate information from a node's neighbors iteratively across layers, producing
node representations that encode both local features and global graph structure. These
representations can be used to:
- Classify entity nodes.
- Predict entity relationships.
- Embed graph communities for similarity search.

Implementation library: **PyTorch Geometric**

---

## Comparison: GraphRAG vs. Standard Vector RAG

| Aspect | Standard Vector RAG | GraphRAG |
|---|---|---|
| Data structure | Flat chunks | Graph of entities and relationships |
| Retrieval unit | Text chunk | Entity neighborhood / community summary |
| Multi-hop reasoning | No | Yes (graph traversal) |
| Long-range connections | No | Yes (edges link distant entities) |
| Query complexity | Simple to moderate | Moderate to complex |
| Setup cost | Low | High (KG construction, graph DB) |
| Best for | General Q&A over text documents | Complex reasoning over interconnected facts |

---

## When to Use GraphRAG

- Documents describe a rich entity landscape (research papers, legal cases, company reports,
  code documentation).
- Queries require multi-hop reasoning ("What was the impact of X on Y, given Z's position?").
- Long-range cross-document relationships need to be preserved.
- Explainability of retrieval is important (graph paths show why results are related).

---

## Libraries and Tools

| Component | Tooling |
|---|---|
| KG construction | LlamaIndex KG extractors, LangChain graph builders |
| Graph database | Neo4j, Amazon Neptune, FalkorDB |
| Vector similarity on nodes | Any ANN-capable vector store |
| GNN implementation | PyTorch Geometric |
| Microsoft GraphRAG reference | `microsoft/graphrag` on GitHub |
