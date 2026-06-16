"""Synthesize node — builds the final response for the user (Phase 12).

Combines retrieved products, trend summary, optional user profile, and
prior conversation context into a coherent, on-brand assistant message
via the RESPONSE_MODEL (``settings.response_model``).  Streams the LLM
output to the per-request SSE queue as a sequence of ``token`` events,
then emits a single ``products`` event (when applicable) and a
terminal ``done`` event with usage metadata.

The node is dispatched from the conditional edge ``route_orchestrate``
for every intent that does not require further retrieval —
``sufficient``, ``clarification_needed``, ``out_of_scope``,
``fallback`` — plus any unknown intent value (defensively mapped to
``fallback``).  Each intent is paired with a dedicated system prompt
under ``src/app/agent/prompts/synthesize_*_system.md`` (D12.1-D12.3)
so the model gets the right tone and structure for the situation.

Key design choices:

- **No try/except around the LLM stream** (D12.6).  If the LLM call
  fails partway through, the exception bubbles to the graph runtime.
  The endpoint's error handler emits a single ``error`` event and
  closes the stream.
- **One ``token`` event per non-empty chunk** (D12.4).  Empty
  ``AIMessageChunk.content`` payloads are dropped silently.
- **Token usage is summed across chunks** when ``usage_metadata`` is
  present; falls back to ``(0, 0, 0.0)`` for models that don't report
  it.  The sum is written into the ``done`` payload so the frontend
  can surface cost in the dev tools.
- **No Pydantic output parser** (D12.7).  The synthesize prompt asks
  for free-form prose, not a structured response.  Anything stricter
  (e.g. JSON-only) would conflict with the brand-tone requirements
  in the system prompts.

SSE event-bus contract: see ``_sse.py`` and
``docs/05-IMPLEMENTATION-PLAN.md`` Section 5 for the full taxonomy.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from app.agent.nodes._sse import emit_sse
from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.schemas.chat import (
    ChatChunk,
    DonePayload,
    ProductItem,
    ProductsPayload,
    UsagePayload,
)

logger = structlog.get_logger(__name__)


# Map every known intent to its dedicated system-prompt file.  Any
# intent that is missing, ``None``, or unknown at runtime is mapped
# defensively to ``"fallback"`` (D12.1) so the worst case is still a
# safe, on-brand response.
_PROMPT_BY_INTENT: dict[str, str] = {
    "sufficient": "synthesize_sufficient_system",
    "clarification_needed": "synthesize_clarification_system",
    "out_of_scope": "synthesize_out_of_scope_system",
    "fallback": "synthesize_fallback_system",
}

_DESCRIPTION_MAX_CHARS = 500


def _select_prompt_name(intent: str | None) -> str:
    """Return the system-prompt file stem for the given intent.

    Args:
        intent: The orchestrator's intent string, or ``None`` if the
            orchestrate node did not run (e.g. the graph entered
            ``synthesize`` via a future direct path).

    Returns:
        The file stem (no ``.md`` extension) registered in the
        prompt cache.
    """
    if intent is None:
        return _PROMPT_BY_INTENT["fallback"]
    return _PROMPT_BY_INTENT.get(intent, _PROMPT_BY_INTENT["fallback"])


def _format_user_profile(profile: dict | None) -> str:
    """Render the user profile as a pretty-printed JSON block.

    Returns an empty string when the profile is missing or empty —
    the prompt template can then be read as "no profile available".
    """
    if not profile:
        return ""
    import json

    return json.dumps(profile, indent=2, ensure_ascii=False)


def _format_products_section(products: list[dict]) -> str:
    """Render the retrieved products as a compact one-line-per-item list.

    The full product payload is forwarded to the frontend in the
    ``products`` SSE event; the LLM only needs a short, scannable
    summary (name + category + price) to weave into prose (D12.4).
    """
    if not products:
        return ""
    lines = ["[Retrieved products this turn]"]
    for p in products:
        name = p.get("name", "?")
        category = p.get("category", "?")
        price = p.get("price_range", "?")
        lines.append(f"- {name} | {category} | {price}")
    return "\n".join(lines)


def _format_context_section(
    *,
    user_profile: dict | None,
    retrieved_products: list[dict],
    trend_summary: str | None,
    summary: str,
) -> str:
    """Assemble the dynamic context block appended to the base prompt.

    Each section is omitted when its source is empty so the prompt
    does not carry ``"None"`` placeholders that could confuse the
    LLM.  The block is wrapped in a single ``"## Conversation
    Context"`` heading.
    """
    parts: list[str] = []
    profile_block = _format_user_profile(user_profile)
    if profile_block:
        parts.append(f"### User profile\n```json\n{profile_block}\n```")
    products_block = _format_products_section(retrieved_products)
    if products_block:
        parts.append(f"### {products_block}")
    if trend_summary:
        parts.append(f"### Trend insights\n{trend_summary}")
    if summary:
        parts.append(f"### Conversation summary so far\n{summary}")
    if not parts:
        return ""
    return "## Conversation Context\n\n" + "\n\n".join(parts)


def _coerce_chunk_text(chunk: AIMessageChunk | Any) -> str:
    """Extract the textual delta from an LLM streaming chunk.

    LangChain returns ``AIMessageChunk`` objects whose ``content``
    is either a plain string or a list of content blocks (for
    multi-modal models).  We coerce both shapes to ``str``; empty
    content is returned as an empty string so the caller can
    filter it out.
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # List of content blocks — concatenate any ``text`` fields.
        bits: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    bits.append(text)
            elif isinstance(block, str):
                bits.append(block)
        return "".join(bits)
    return str(content)


def _accumulate_usage(usage: dict | None, totals: dict[str, int]) -> None:
    """Add the chunk's usage fields to the running totals (D12.4).

    LangChain reports usage in two shapes depending on the model:

    - OpenAI: ``{"input_tokens": N, "output_tokens": M, "total_tokens": K}``
    - Some models: ``{"prompt_tokens": N, "completion_tokens": M}``

    We sum the first shape we see; unknown fields are ignored.
    """
    if not usage:
        return
    for key in ("input_tokens", "prompt_tokens"):
        if key in usage:
            totals["prompt_tokens"] = totals.get("prompt_tokens", 0) + int(usage[key])
            break
    for key in ("output_tokens", "completion_tokens"):
        if key in usage:
            totals["completion_tokens"] = totals.get("completion_tokens", 0) + int(usage[key])
            break


def _build_product_items(products: list[dict]) -> list[ProductItem]:
    """Project the Qdrant product payload to the SSE ``ProductItem`` shape.

    Truncates ``description`` to ``_DESCRIPTION_MAX_CHARS`` to keep the
    SSE payload bounded (a single turn carries at most ~3 products, so
    this is well under the typical 4 KB frame limit).
    """
    items: list[ProductItem] = []
    for p in products:
        description = p.get("description", "") or ""
        if len(description) > _DESCRIPTION_MAX_CHARS:
            description = description[:_DESCRIPTION_MAX_CHARS]
        items.append(
            ProductItem(
                id=str(p.get("product_id", "")),
                name=p.get("name", ""),
                description=description,
                price_range=p.get("price_range", ""),
                saleor_url=p.get("saleor_url", ""),
                thumbnail_url=p.get("thumbnail_url", "") or "",
            )
        )
    return items


async def synthesize(state: AgentState, config: RunnableConfig) -> dict:
    """Generate the final streamed response for the user (FR-003, FR-070).

    Behaviour (per Phase 12, D12.1-D12.7):

    1. Bind context vars (correlation_id, node name).
    2. Select the system prompt by intent (defensive fallback).
    3. Append a dynamic context block to the base prompt.
    4. Build ``ChatOpenAI(model=settings.response_model)``.
    5. Read the per-request SSE queue from ``config["configurable"]``.
    6. Iterate ``await llm.astream(messages)``; emit one ``token``
       event per non-empty chunk; accumulate usage.
    7. Emit one ``products`` event if ``retrieved_products`` is
       non-empty.
    8. Emit a terminal ``done`` event with usage and intent.
    9. Return ``{"messages": [AIMessage(content="<concatenated text>")]}``
       so the response is persisted to the LangGraph state.

    Args:
        state: Current agent state.  Read-only here — the only
            mutation is the appended ``AIMessage`` in the return value.
        config: LangGraph runtime config; ``config["configurable"]``
            carries the per-request ``sse_queue`` (Phase 14).

    Returns:
        Partial state update ``{"messages": [AIMessage(...)]}`` with
        the concatenated streamed text.  Empty list when the LLM
        emitted no text content (rare; e.g. refusal) — the graph
        still proceeds because the SSE stream is the source of truth
        for the user-facing response.

    Raises:
        Any exception raised by the LLM call is propagated to the
        graph runtime (D12.6).  The endpoint translates it to a
        single ``error`` SSE event.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="synthesize",
    )

    settings = get_settings()
    intent = state.get("intent")

    # Step 1: select the system prompt.
    prompt_name = _select_prompt_name(intent)
    base_prompt = load_prompt(prompt_name)

    # Step 2: append the dynamic context block.
    context_block = _format_context_section(
        user_profile=state.get("user_profile"),
        retrieved_products=state.get("retrieved_products") or [],
        trend_summary=state.get("trend_summary"),
        summary=state.get("summary", ""),
    )
    system_text = base_prompt if not context_block else f"{base_prompt}\n\n{context_block}"

    # Step 3: assemble the message list.  ``state["messages"]``
    # already contains the user turn(s); the system prompt goes
    # at index 0 (per LangChain convention).
    messages: list[BaseMessage] = [
        SystemMessage(content=system_text),
        *state["messages"],
    ]

    # Step 4: build the LLM.
    llm = ChatOpenAI(model=settings.response_model)

    # Step 5: read the SSE queue.  ``None`` is fine — ``emit_sse``
    # no-ops in that case (tests that don't care about streaming).
    configurable = config.get("configurable", {}) if config else {}
    sse_queue = configurable.get("sse_queue") if isinstance(configurable, dict) else None

    # Step 6: stream.
    deltas: list[str] = []
    usage_totals: dict[str, int] = {}
    async for chunk in llm.astream(
        messages,
        config={"metadata": {"correlation_id": state["correlation_id"]}},
    ):
        text = _coerce_chunk_text(chunk)
        if text:
            deltas.append(text)
            await emit_sse(
                sse_queue,
                "token",
                ChatChunk(delta=text, done=False),
            )
        usage_metadata = getattr(chunk, "usage_metadata", None)
        if usage_metadata:
            _accumulate_usage(usage_metadata, usage_totals)

    full_text = "".join(deltas)

    # Step 7: emit the products event when the turn retrieved any.
    products = state.get("retrieved_products") or []
    if products:
        await emit_sse(
            sse_queue,
            "products",
            ProductsPayload(items=_build_product_items(products)),
        )

    # Step 8: emit the terminal done event.
    usage_payload = UsagePayload(
        prompt_tokens=usage_totals.get("prompt_tokens", 0),
        completion_tokens=usage_totals.get("completion_tokens", 0),
        cost_usd=0.0,
    )
    await emit_sse(
        sse_queue,
        "done",
        DonePayload(
            run_id=str(uuid.uuid4()),
            thread_id=state["thread_id"],
            intent=intent,
            usage=usage_payload,
        ),
    )

    logger.info(
        "synthesize_completed",
        thread_id=state["thread_id"],
        intent=intent,
        prompt_name=prompt_name,
        delta_count=len(deltas),
        prompt_tokens=usage_payload.prompt_tokens,
        completion_tokens=usage_payload.completion_tokens,
        retrieved_count=len(products),
    )

    # Step 9: return the new AIMessage so it is persisted to the
    # LangGraph checkpointer.  The SSE stream is the source of
    # truth for the client; the persisted message is what shows
    # up in the thread history.
    return {"messages": [AIMessage(content=full_text)]}
