# Observability

## Concept

- Enables monitoring, debugging, and evaluation of LLM applications in production.
- Covers both the indexing and querying stages.
- Capabilities:
    - View LLM and prompt inputs/outputs.
    - Verify that component outputs (LLMs, embeddings) meet expectations.
    - View full call traces.
    - Count tokens for cost monitoring.

---

## Global Handler (Legacy + Partner Integrations)

- One-line toggle: `set_global_handler("<handler_name>", **kwargs)`.
- Seamlessly pipes execution data to partner tools.
- Partners include: various observability and evaluation services (see official docs for full list).

---

## Token Counting

- Use `TokenCountingHandler` from `llama_index.core.callbacks`.
- Attach via `Settings.callback_manager = CallbackManager([token_counter])`.
- Tracks:
    - `total_embedding_token_count`: tokens used for embedding.
    - `prompt_llm_token_count`: tokens in LLM prompts.
    - `completion_llm_token_count`: tokens in LLM completions.
    - `total_llm_token_count`: total LLM tokens.
- Tokenizer is configurable; should match the LLM in use.
- Call `token_counter.reset_counts()` to reset at any point.

---

## Instrumentation Module (v0.10.20+)

The new instrumentation module replaces the legacy `CallbackManager`. During the deprecation period, both are supported.

### Core Concepts

- `Event`: a single moment in time with `id_`, `timestamp`, `span_id`, and event-specific fields.
- `EventHandler`: listens for Events; subclass `BaseEventHandler`, implement `handle(event)`.
- `Span`: represents execution flow over a duration; contains Events. Has an open/close lifecycle.
- `SpanHandler`: manages Span lifecycle; subclass `BaseSpanHandler`, implement `new_span()`, `prepare_to_exit_span()`, `prepare_to_drop_span()`.
- `Dispatcher`: emits Events and enters/exits Spans. Retrieved via `instrument.get_dispatcher(name)`.

### Using the Instrumentation Module

1. Get a dispatcher: `dispatcher = instrument.get_dispatcher(__name__)`.
2. Attach an `EventHandler`: `dispatcher.add_event_handler(my_handler)`.
3. Attach a `SpanHandler`: `dispatcher.add_span_handler(my_span_handler)`.

### Defining Custom EventHandler

- Subclass `BaseEventHandler`, implement `handle(event)`.
- Check `isinstance(event, SpecificEventClass)` to branch on event type.
- Attach to dispatcher.

### Defining Custom Event

- Subclass `BaseEvent` (Pydantic model); add new fields.
- Emit via `dispatcher.event(MyEvent(...))`.

### Defining Custom Span

- Subclass `BaseSpan`, add fields.
- Create a matching `SpanHandler` subclass implementing the three abstract methods.
- Use `@dispatcher.span` decorator on any function to auto-manage span entry/exit.
- Or manually call `dispatcher.span_enter(...)`, `dispatcher.span_exit(...)`, `dispatcher.span_drop(...)`.

### Built-in LLM Events

- `LLMChatStartEvent`: fired when an LLM chat call begins; contains messages and model info.
- `LLMChatInProgressEvent`: fired during streaming; contains response delta.
- `LLMChatEndEvent`: fired when an LLM chat call ends; contains final response.
