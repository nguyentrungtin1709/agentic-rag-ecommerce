# Local Models with Ollama

Ollama is a lightweight local model server that allows running LLM and embedding models
on local hardware without requiring an API key or internet connection.

---

## Why Use Local Models

| Use case | Reason |
|---|---|
| Offline or air-gapped environments | No external API calls allowed |
| Cost reduction | Eliminate API costs for high query volumes |
| Development and testing | Fast local iteration without rate limits or per-call charges |
| Data privacy | Sensitive data never leaves the local machine |
| Prototyping | Test pipeline changes quickly before switching to hosted models |

---

## How Ollama Works

- Ollama runs as a local server (default: `http://localhost:11434`).
- It exposes OpenAI-compatible HTTP endpoints, so any library that supports the OpenAI
  API can communicate with it.
- Models are downloaded with `ollama pull <model_name>` and served locally.
- Supports LLM inference (chat, completion) and embedding generation.

---

## LlamaIndex Integration

LlamaIndex provides first-class Ollama support in dedicated packages.

### LLM via Ollama

```python
from llama_index.llms.ollama import Ollama

llm = Ollama(model="llama3", request_timeout=120.0)
```

Set this as the global LLM:

```python
from llama_index.core import Settings
Settings.llm = llm
```

### Embeddings via Ollama

```python
from llama_index.embeddings.ollama import OllamaEmbedding

embed_model = OllamaEmbedding(model_name="nomic-embed-text")
```

Set as global embedding model:

```python
Settings.embed_model = embed_model
```

Required packages:
- `llama-index-llms-ollama`
- `llama-index-embeddings-ollama`

---

## Recommended Models

| Task | Model | Notes |
|---|---|---|
| LLM (general) | llama3, mistral, gemma | Good quality, reasonable size |
| LLM (code) | codellama, deepseek-coder | Code generation and understanding |
| Embeddings | nomic-embed-text | Strong retrieval performance, small size |
| Embeddings | mxbai-embed-large | Higher dimensional, better recall |

Models are pulled with:
```bash
ollama pull <model_name>
```

---

## Limitations Compared to Hosted Models

- Inference speed is limited by local hardware (GPU VRAM, CPU threads).
- Smaller local models may have lower reasoning quality for complex tasks.
- Not suitable for production workloads with high concurrent query volumes unless running
  on dedicated GPU infrastructure.
- Embedding model dimensions may differ from hosted models; re-indexing is required when
  switching models.

---

## Usage Pattern in a Full RAG Pipeline

1. Start Ollama: `ollama serve`
2. Pull needed models: `ollama pull llama3 && ollama pull nomic-embed-text`
3. Configure LlamaIndex Settings with Ollama LLM and embedding model.
4. Build or load the index (embeddings generated locally by Ollama).
5. Run queries (LLM inference handled locally by Ollama).

No code changes are needed to the pipeline itself — Ollama is a drop-in replacement
for hosted model providers through the LlamaIndex `Settings` abstraction.
