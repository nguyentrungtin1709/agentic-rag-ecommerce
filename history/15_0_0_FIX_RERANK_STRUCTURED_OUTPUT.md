# Fix rerank structured output type

**Version**: 15.0.0
**Date**: 2026-06-17
**Status**: Completed

## What
Replace the invalid `with_structured_output(list[str])` call in
`ProductRAG.llm_postprocess_node` with a Pydantic `RerankOutput`
`BaseModel` carrying a `ranked_ids: list[str]` field, restoring the
LLM rerank stage of the hybrid retrieval pipeline.

## Why
`app.log` shows two distinct failures from `llm_postprocess_node` on
every `need_product_search` turn:

1. **Original**: `TypeError: list[str] is not a module, class, method,
   or function.` — LangChain's `with_structured_output` does not
   accept a raw type annotation. With `from __future__ import
   annotations` enabled, `list[str]` is the string `"list[str]"` at
   runtime, which LangChain cannot resolve to a schema.
2. **After RootModel fix**: `BadRequestError: 400 - Invalid schema
   for response_format 'RerankOutput': schema must be a JSON Schema
   of 'type: "object"', got 'type: "array"'` — OpenAI's
   structured-outputs endpoint (the default method for
   `langchain-openai>=0.3`) only accepts top-level `type: "object"`.
   A `RootModel[list[str]]` generates a `type: "array"` schema and
   is rejected with HTTP 400.

The `product_rag_llm_postprocess_failed` error handler masks the
failure and falls back to the raw Qdrant score ordering, so the
symptom is silent quality loss — the system still returns products,
but the semantic rerank never runs.

## How
- Define `RerankOutput(BaseModel)` with a single field
  `ranked_ids: list[str]` in
  `src/app/agent/subagents/product_rag/schemas.py`.
- Update `llm_postprocess_node` in `nodes.py` to bind
  `with_structured_output(RerankOutput)` and read the IDs via the
  `.ranked_ids` attribute.
- Update all rerank unit tests to construct
  `RerankOutput(ranked_ids=[...])` mocks.
- Add a regression-guard test
  (`test_rerank_output_schema_is_object_type`) that pins the
  generated JSON schema to `type: "object"` with an
  `items: {type: "string"}` array, so a future refactor that
  re-introduces an array-only schema is caught at unit-test time.

## Key Decisions
- Decision 1: Use `BaseModel` with a `ranked_ids` field rather than
  `RootModel[list[str]]` — `RootModel` produces `type: "array"` JSON
  Schemas that OpenAI rejects. The named-field `BaseModel` produces
  `type: "object"` with a list-valued property, satisfying both
  LangChain's "must be a class" rule and OpenAI's "must be an
  object" rule. This matches the pattern used by
  `PrepareQueryOutput` in the same file.
- Decision 2: Rejected the `method="function_calling"` alternative
  — it would also work, but the warning issued by LangChain
  identifies it as a workaround rather than the default path, and
  using a `BaseModel` keeps the project on the default structured
  outputs path for all nodes.
- Decision 3: Do NOT remove the `handle_llm_postprocess_error`
  fallback — the rerank LLM can still fail for transient reasons
  (rate limit, timeout); the fallback remains a useful safety net.

## Impact
- `src/app/agent/subagents/product_rag/schemas.py` — add `RerankOutput`
  as a `BaseModel` (not `RootModel`).
- `src/app/agent/subagents/product_rag/nodes.py` — change
  `with_structured_output` argument and read `parsed.ranked_ids`.
- `tests/unit/agent/subagents/test_llm_postprocess.py` — update all
  ten `RerankOutput(root=[...])` mocks to
  `RerankOutput(ranked_ids=[...])`; add the schema-shape regression
  guard.
- No API or graph topology change; no migration needed.
