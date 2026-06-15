# 11.0.0 — TrendScout Subagent

## Status
Accepted — 2026-06-15

## Context

Phase 11 replaces the `run_trend_scout` stub (46 lines) in
`src/app/agent/subagents/trend_scout/agent.py` with a real
`create_agent` (langchain 1.3.2) subagent. The subagent is
dispatched by the orchestrator node when the intent is
`need_trend_info` (DRAFT 0.6 §2.2).

The subagent must:

- Search the web for current POD design trends.
- Return a `TrendScoutOutput` with `trend_summary` (2-3 sentence
  trend report) and an optional `image_prompt` (text-to-image
  prompt for DALL-E).
- Gracefully degrade when search backends are unavailable.

Phase 11 is the first subagent to use the `create_agent` API
(replacing the deprecated `create_react_agent` mentioned in the
implementation plan). The pattern established here (dynamic
SystemMessage injection, single exposed tool with internal fallback,
subagent-level summarisation middleware) will be reused by later
subagents.

All required infrastructure is already in place:

- `TavilySearchResults` from `langchain_community.tools.tavily_search`
  (Phase 1 dependency pin, no new dep required).
- `DuckDuckGoSearchRun` from `langchain_community.tools.ddg_search.tool`
  (Phase 1 dependency pin, no new dep required).
- `TAVILY_API_KEY` is set in `.env`.
- The `trend_scout_system.md` prompt template exists at
  `src/app/agent/prompts/trend_scout_system.md` and is loaded via
  `app.agent.prompts.load_prompt`.
- The parent graph already wires
  `builder.add_node("run_trend_scout", run_trend_scout)` and the
  orchestrator routes to it on `intent == "need_trend_info"`.

## Decisions

### D11.1 — `create_agent` from `langchain.agents`, NOT `create_react_agent`

The implementation plan §11.4 still mentions
`create_react_agent`, but that API is **deprecated** in
`langchain==1.3.2`. The current API is `create_agent` from
`langchain.agents` (DRAFT 0.6 §2.2 already specifies it).

`create_agent` supports everything we need:

- `model=` (chat model instance)
- `tools=` (list of `@tool` decorated functions)
- `state_schema=` (TypedDict subclass for subgraph state)
- `response_format=` (Pydantic `BaseModel` for structured output,
  written to `result["structured_response"]`)
- `middleware=` (list of built-in or custom middleware, including
  `SummarizationMiddleware`)

Verified via `.venv/lib/python3.12/site-packages/langchain/agents/factory.py:702-703`
(`response_format` and `state_schema` are first-class kwargs).

### D11.2 — Fallback Tavily→DuckDuckGo inside the tool body, not at the agent level

The ReAct loop already has built-in tool retry semantics. Putting
the fallback **inside** `tavily_search` keeps it invisible to the
LLM:

- The LLM calls one tool (`tavily_search`).
- The tool body tries Tavily; on `Exception` (rate-limit, network,
  quota) it logs a warning and calls the `duckduckgo_search`
  module-level helper.
- The LLM never sees "tool X failed, try tool Y".

This avoids a wasted round-trip ("tool failed, let me try a
different tool") and gives the agent a single, reliable
interface for "search the web".

This is the same D11.2 / D11.12 decision the implementation plan
in `TEMP.md` records. The D11.12 clarification is that the
fallback helper is a plain function — not a `@tool` — so it is
never exposed to the LLM (see D11.12).

### D11.3 — Wrapper forwards the full `state["messages"]` into the subagent

The implementation plan §11.4 originally proposed passing only the
last 4 messages. The wrapper, mirroring `run_product_rag`, forwards
**all** parent messages. Rationale:

- The TrendScout subagent is a research agent, not a chat
  agent. The full context (earlier user turns, prior tool
  results) helps the LLM frame a focused search query.
- The parent graph already does thread-level memory management
  via `SummarizeNode` (Phase 8). When the conversation is long,
  old messages are summarised and the **summary** is what
  reaches the subagent via the dynamic `SystemMessage`
  builder — the un-summarised older messages are dropped at
  the parent level, not the subagent level.
- The `SummarizationMiddleware` (D11.13) handles the
  subagent-level safety net for unusually long threads.

### D11.4 — `TrendScoutState` extends `langchain.agents.AgentState`

`langchain.agents.AgentState` is a TypedDict that already carries
`messages: list[BaseMessage]` with the `add_messages` reducer.
The TrendScout subgraph state extends it with one extra field:

- `generate_image: bool` — read by the dynamic SystemMessage
  builder to decide whether to instruct the agent to emit an
  `image_prompt`.

`messages[0]` is the dynamic `SystemMessage` injected by the
wrapper. The reducer preserves it for the ReAct loop's first
node call.

### D11.5 — `TrendScoutOutput` is a Pydantic `BaseModel` passed via `response_format`

Pydantic `BaseModel` is the canonical structured-output schema for
`create_agent`. The agent emits one of these as its final message
and the wrapper extracts it from `result["structured_response"]`.

`TrendScoutOutput` has two fields, both nullable:

- `trend_summary: str | None` — the 2-3 sentence trend report.
  `None` when no synthesis was possible (graceful degradation).
- `image_prompt: str | None` — text-to-image prompt (DALL-E
  compatible). `None` when `generate_image` was `False` or the
  query is not design-related.

### D11.6 — Graceful degradation when both backends fail

When Tavily fails AND the DuckDuckGo fallback also fails, the
**tool body** raises `RuntimeError`. The wrapper catches this
and any other exception at the wrapper level and returns
`{"trend_summary": None, "image_prompt": None}`. The orchestrator
node treats both as "no trend data available" and routes to
`synthesize` so the user gets a graceful response.

The alternative — letting the exception propagate — would crash
the parent graph. The current design keeps the parent graph
running and surfaces the failure as a structured log event.

### D11.7 — Subgraph compiled once at import time with `checkpointer=None`

`checkpointer=None` mirrors the `_PRODUCT_RAG_GRAPH` pattern.
The subagent state is transient: it runs in the parent's
orchestration step and its state is not checkpointed. The
parent graph's `AsyncPostgresSaver` handles thread-level
checkpoints at the parent level.

Compiling once at import time (`_TREND_SCOUT_GRAPH`) keeps
`run_trend_scout` cheap to call — the per-call work is the
`ainvoke`, not a `create_agent` invocation.

### D11.8 — `model=settings.orchestrator_model` (NOT a separate `trend_scout_model`)

The TrendScout subagent uses the orchestrator model
(`gpt-5.4-mini` by default per the project naming convention).
Confirmed by:

- `docs/05-IMPLEMENTATION-PLAN.md:754` —
  `create_react_agent(model=ChatOpenAI(settings.orchestrator_model), ...)`
- `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md:326` —
  `model=settings.orchestrator_model`

There is no `trend_scout_model` field in `Settings` and none is
added. Using the same model across the orchestrator and
TrendScout subagent keeps the routing tier cost-effective
(gpt-5.4-mini is a small, fast model suitable for
classification + light synthesis).

### D11.9 — `correlation_id` forwarded through `config["metadata"]`

The wrapper copies `state["correlation_id"]` into
`config["metadata"]["correlation_id"]` so the LangSmith trace
(NFR-021) links back to the originating HTTP request. The
structlog context vars are also bound at the top of
`run_trend_scout` so every log line in the subagent's async
context is tagged.

### D11.10 — No new dependencies

`langchain-community==0.4.2` already provides both
`TavilySearchResults` (in `langchain_community.tools.tavily_search`)
and `DuckDuckGoSearchRun` (in `langchain_community.tools.ddg_search.tool`).
The `TAVILY_API_KEY` env var is already in `.env`.

The newer `langchain-tavily` package is intentionally **not**
added — it would replace a working dependency for no functional
gain.

### D11.11 — `config.py:42` default aligned to `gpt-5.4-mini`

The `Settings.orchestrator_model` field default was
`"gpt-4o-mini"`, which conflicts with:

- `.env` and `.env.example` (`ORCHESTRATOR_MODEL=gpt-5.4-mini`)
- `docs/analysis/04-MULTI-AGENT-ARCHITECTURE-DESIGN.md` §6
  (lists `gpt-5.4-mini` for the orchestrator tier)
- `docs/05-IMPLEMENTATION-PLAN.md` §11.3 (TrendScout uses
  `orchestrator_model` per the project's model hierarchy)

The default is updated to `"gpt-5.4-mini"`. This is a
consistency fix only — runtime behaviour is unchanged because
`.env` always overrides the code default.

### D11.12 — Single exposed tool: only `tavily_search` is a `@tool`

The TrendScout subagent exposes exactly one tool to the LLM:
`tavily_search`. The `duckduckgo_search` function exists in
the same module as a **plain module-level function** (no
`@tool` decorator) and is called only from inside
`tavily_search` when the Tavily backend fails.

Reasons:

- The LLM should not have to reason about provider routing
  (Tavily vs DuckDuckGo). It only needs to know "search the
  web".
- A smaller tool schema means fewer prompt tokens and less
  risk of the LLM misusing a low-quality backend.
- Observability is simpler: one tool call, one set of metrics,
  one failure mode to alert on.
- The LLM never gets a "tool not found" error for
  `duckduckgo_search` — that function is unreachable from the
  agent's perspective.

This is the standard "tool as façade, helpers as implementation
detail" pattern. The single tool name (`tavily_search`)
preserves the design doc's terminology while the actual call
graph is a cascade.

### D11.13 — Subagent-level `SummarizationMiddleware` for context safety

The TrendScout subagent inherits a potentially long `messages`
list from the parent state (D11.3). The ReAct loop can also
accumulate many tool calls during a single invocation. While
the parent-level `SummarizeNode` (Phase 8) handles thread-level
memory, a subagent-level safety net is added to prevent the
subagent's context from blowing past the model's input limit
on edge-case threads (50+ messages with many tool exchanges).

`SummarizationMiddleware` is a built-in middleware from
`langchain.agents.middleware` (requires `langchain>=1.1`; we
have 1.3.2). Configuration:

- `model=settings.summarize_model` — separate, cheaper model
  for the summary generation call. Reuses the existing
  `SUMMARIZE_MODEL` env var (no new setting).
- `trigger=("tokens", N)` where
  `N = int(0.8 × subagent_model.profile["max_input_tokens"])`.
  Computed once at build time in `_resolve_summarize_trigger`
  and logged. Using the explicit `tokens` form (not
  `("fraction", 0.8)`) makes the threshold visible in logs
  and test assertions.
- `keep=("messages", 20)` — default safe value; recent 20
  messages are preserved verbatim.

Fallback: if `model.profile["max_input_tokens"]` is missing
(`gpt-5.4-mini` is a project-specific name that may not be in
`models.dev` yet), `_resolve_summarize_trigger` logs a warning
and uses a hardcoded fallback threshold of 100_000 tokens.
This matches the default profile the `SummarizationMiddleware`
docs use as a baseline.

## Consequences

**Positive**

- Single exposed tool simplifies the LLM's tool-selection
  logic and makes the agent's behaviour predictable.
- `SummarizationMiddleware` is a safety net for unusually long
  threads without adding per-call complexity.
- The wrapper's exception handling guarantees the parent graph
  always receives a valid partial state update (D11.6).
- Dynamic `SystemMessage` injection (built per-call in
  `_build_trend_scout_system`) gives the LLM full context
  (summary, user profile, retrieved products, image flag)
  without hard-coding that context in the prompt template.
- No new dependencies. The pattern established here is
  reusable for future subagents (the `create_agent` +
  `middleware` + dynamic SystemMessage trio).

**Negative / trade-offs**

- The `duckduckgo_search` helper is private to the
  `tools.py` module. If a future subagent wants the same
  fallback, it will need to import it or duplicate the logic.
  Mitigation: the helper signature is stable and trivial to
  extract if/when needed.
- `model.profile["max_input_tokens"]` may be missing for
  `gpt-5.4-mini` (project-specific name). The fallback
  threshold (100_000) is conservative and matches the
  `SummarizationMiddleware` docs baseline; if it proves too
  low or too high in practice, the hardcoded value is the
  only thing to adjust.
- The `SummarizationMiddleware` adds one extra LLM call when
  the trigger fires. With a typical TrendScout invocation
  (1-3 search calls, 3k-8k tokens), the trigger does **not**
  fire, so the cost is zero in the common case. The
  extra-call cost is bounded by the long-thread edge case
  the middleware exists to handle.

## Implementation notes

- `_build_trend_scout_graph()` is called once at module import
  time and the result cached as `_TREND_SCOUT_GRAPH`. The
  cached graph is reused for every `run_trend_scout` call.
- `_resolve_summarize_trigger(model)` is a small helper that
  reads `model.profile`, computes the 80% threshold, and
  returns a `("tokens", N)` tuple. It logs the threshold so
  operators can see it in the startup logs.
- The DDG result parser (`_parse_ddg_output`) is intentionally
  simple: split on `"\n\n"`, extract title/snippet/url from
  the parenthesised URL. `DuckDuckGoSearchRun.invoke()` returns
  a free-text string whose exact format can drift between
  versions; the parser is defensive (empty chunks are dropped,
  `max_results` cap is applied) and the function is unit
  tested directly.

## Test coverage

- 6 unit tests in `tests/unit/agent/subagents/test_trend_scout_tools.py`:
  Tavily OK, Tavily fail → DDG fallback, DDG string parsing,
  DDG raises `RuntimeError`, `tavily_search` is a `@tool`
  (has `.name` / `.args_schema`), `duckduckgo_search` is a
  plain function (no `.name` attribute).
- 6 unit tests in `tests/unit/agent/subagents/test_trend_scout_system.py`:
  empty inputs → base only, each of the four injected sections
  appears when its source is non-empty, all sections appear in
  the correct order.
- 8 unit tests in `tests/unit/agent/subagents/test_trend_scout_wrapper.py`:
  state passthrough, structured-response extraction, metadata
  forwarding, subgraph-raises graceful degradation, missing
  `structured_response` graceful degradation, `user_profile=None`
  safe handling, D11.12 contract (`tools=[tavily_search]`), and
  D11.13 contract (middleware is attached with the correct
  trigger threshold).
