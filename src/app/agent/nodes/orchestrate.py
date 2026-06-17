"""orchestrate node — central LLM intent classifier.

Routes the conversation to the appropriate subagent(s) or generates a
direct response when no retrieval is needed.  Uses the ORCHESTRATOR_MODEL
to minimise cost on routing decisions.

This node is the central LLM-driven intent classifier.  It performs a
single LLM call per visit with the ``update_intent`` tool bound, extracts
the chosen intent, and writes it to ``AgentState.intent``.

CRITICAL — This node does NOT modify ``state["messages"]``.

The graph can loop back to ``orchestrate`` from ``run_product_rag`` and
``run_trend_scout``, meaning the same node may execute 2-4 times in a
single turn.  ``state["messages"]`` is managed by LangGraph's
``add_messages`` reducer, which APPENDS rather than replaces — returning
a ``messages`` key from this node would accumulate duplicate context
notes on every loop iteration and pollute the conversation history.
Context hints about ``retrieved_products``, ``trend_summary``,
``image_prompt``, and the ``generate_image`` flag are therefore built
as LOCAL variables and passed only to the LLM as input.

Related FRs: FR-058 (intent classification), FR-059 (fallback path),
FR-088 (prompt-injection safety on user content), NFR-021 (correlation
ID forwarded to LangSmith), NFR-025 (externalised prompts).
"""

from __future__ import annotations

from typing import Literal

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.config import get_settings

logger = structlog.get_logger(__name__)


# ── Intent taxonomy ──────────────────────────────────────────────────────────

IntentType = Literal[
    "need_product_search",
    "need_trend_info",
    "sufficient",
    "clarification_needed",
    "out_of_scope",
    "fallback",
]

# Pre-computed set for O(1) membership tests against tool-call output.
_VALID_INTENTS: frozenset[str] = frozenset(IntentType.__args__)


# ── Tool definition ──────────────────────────────────────────────────────────


@tool
def update_intent(intent: IntentType) -> str:
    """Update the conversation routing intent for the orchestrator.

    Call this tool exactly once per turn with the single most appropriate
    intent for the user's current request.  Valid values are documented
    in the system prompt at ``prompts/orchestrator_system.md``.
    """
    return intent


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_intent(response: AIMessage) -> str:
    """Pull the intent string out of the LLM tool-call response.

    Falls back to ``"fallback"`` when the LLM emits no tool call or the
    intent value is not one of the six known values.  Never raises.

    Args:
        response: The ``AIMessage`` returned by the LLM with the
            ``update_intent`` tool bound.

    Returns:
        The chosen intent string, or ``"fallback"`` on any extraction
        failure.
    """
    tool_calls = getattr(response, "tool_calls", None) or []
    if not tool_calls:
        logger.warning(
            "orchestrate_no_tool_call",
            response=str(response)[:200],
        )
        return "fallback"
    raw = tool_calls[0].get("args", {}).get("intent")
    if raw not in _VALID_INTENTS:
        logger.warning("orchestrate_unknown_intent", raw_value=raw)
        return "fallback"
    return raw


# ── Main node ────────────────────────────────────────────────────────────────


async def orchestrate(
    state: AgentState,
    config: RunnableConfig,
) -> dict:
    """Classify the current user intent and dispatch to the next node.

    This node runs once per visit by the graph.  It does NOT loop or
    chain tool calls.  Routing is performed by the conditional edge
    ``route_orchestrate`` in ``graph.py``, which reads the ``intent``
    field written here.

    Behaviour summary:

    1. If ``configurable.remaining_steps <= AGENT_FALLBACK_THRESHOLD``,
       return ``{"intent": "fallback"}`` immediately without calling the
       LLM.  This prevents the graph from hitting LangGraph's hard
       ``recursion_limit``.
    2. Build an LLM with the ``update_intent`` tool bound.
    3. Assemble the message list: system prompt + ``state["messages"]``
       + (optional) local context notes about already-retrieved
       products, trend data, an image prompt prepared by TrendScout,
       and the ``generate_image`` client opt-in flag (positive or
       negative).  The notes are LOCAL — they are not returned as
       part of the state update.
    4. Invoke the LLM and extract the chosen intent from its tool call.
    5. Return ``{"intent": <chosen value>}`` only.

    Args:
        state: Current agent state.  Read-only — this function does
            not modify ``state["messages"]``.
        config: LangGraph runtime config containing
            ``configurable.remaining_steps``.

    Returns:
        A partial state update ``{"intent": <one of six values>}``.
        NEVER contains a ``messages`` key.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="orchestrate",
    )

    settings = get_settings()

    # Step 1 — step-budget guard. Force fallback before recursion_limit.
    remaining = config.get("configurable", {}).get("remaining_steps", settings.max_agent_steps)
    if remaining <= settings.agent_fallback_threshold:
        logger.warning(
            "orchestrate_forced_fallback",
            thread_id=state["thread_id"],
            remaining_steps=remaining,
            threshold=settings.agent_fallback_threshold,
        )
        return {"intent": "fallback"}

    # Step 2 — bind the intent-classification tool to the LLM.
    llm = ChatOpenAI(model=settings.orchestrator_model).bind_tools([update_intent])

    # Step 3 — assemble messages as a LOCAL variable.
    # IMPORTANT: `messages` is a local variable. It is only used as input
    # to the LLM call below and is never returned from this function.
    # Returning it would mutate state["messages"] (MessagesState uses
    # the add_messages reducer, which APPENDS) and accumulate duplicate
    # context notes on every loop iteration.
    messages: list[BaseMessage] = [
        SystemMessage(content=load_prompt("orchestrator_system")),
        *state["messages"],
    ]
    retrieved = state.get("retrieved_products") or []
    trend = state.get("trend_summary")
    image_prompt = state.get("image_prompt")
    generate_image = bool(state.get("generate_image"))

    if retrieved:
        # Compact format: one line per product with name + category +
        # price_range.  Enough for the LLM to recognise follow-up
        # questions about a specific retrieved product without flooding
        # the prompt with descriptions, URLs, or thumbnails.
        lines = ["[Context — products already retrieved this turn]"]
        for p in retrieved:
            name = p.get("name", "?")
            category = p.get("category", "?")
            price_range = p.get("price_range", "?")
            lines.append(f"- Name: {name} | Category: {category} | Price: {price_range}")
        messages.append(HumanMessage(content="\n".join(lines)))

    if trend:
        # Trend summary is short by design (2-3 sentences per DRAFT 0.6
        # Section 2.2). Include the full text so the orchestrator can
        # recognise when a follow-up question is already answered by
        # existing trend data.
        messages.append(
            HumanMessage(
                content=(f"[Context — trend research already completed this turn]\n{trend}")
            )
        )

    if image_prompt:
        # Mirror the trend hint: when TrendScout has produced an
        # image_prompt, the orchestrator should know the trend branch
        # has produced the artifact the image-generation node will
        # consume. Include the full prompt text so the LLM can
        # recognise follow-up questions that reference it.
        messages.append(
            HumanMessage(
                content=(f"[Context — image prompt is already prepared this turn]\n{image_prompt}")
            )
        )

    if generate_image:
        # Positive signal: the client opted in to image generation for
        # this turn. The image-generation node will execute only when
        # an image_prompt is also set, so the orchestrator should
        # dispatch need_trend_info when the request involves a design,
        # artwork, or image — and respect the products-before-trend
        # dispatch order when both are needed.
        messages.append(
            HumanMessage(
                content=(
                    "[Context — image generation is enabled for this turn. "
                    "If the user's request involves a design, artwork, or image to be "
                    "created, dispatch need_trend_info so TrendScout can produce an "
                    "image_prompt. The dispatch order rule still applies: products "
                    "before trend when both are needed.]"
                )
            )
        )
    else:
        # Negative signal: the client did not opt in to image generation.
        # The image-generation node will be a no-op regardless of any
        # image_prompt gathered, so dispatching need_trend_info solely
        # to produce an image_prompt is wasteful. Trend research is
        # still allowed for trends / style / design ideas (cases where
        # no image is required).
        messages.append(
            HumanMessage(
                content=(
                    "[Context — image generation is NOT enabled for this turn. "
                    "The user did not request image generation in this turn, so the "
                    "image-generation node will be a no-op regardless of any trend or "
                    "image_prompt gathered. Do not dispatch need_trend_info solely to "
                    "produce an image_prompt. Trend research may still be dispatched "
                    "when the query asks for trends, style reports, or design ideas.]"
                )
            )
        )

    # Step 4 — invoke the LLM. Forward correlation_id for trace linkage.
    response = await llm.ainvoke(
        messages,
        config={"metadata": {"correlation_id": state["correlation_id"]}},
    )

    # Step 5 — extract intent from the tool call (falls back gracefully).
    intent = _extract_intent(response)

    logger.info(
        "orchestrate_invoked",
        thread_id=state["thread_id"],
        intent=intent,
        remaining_steps=remaining,
        retrieved_products_count=len(retrieved),
        has_trend_summary=trend is not None,
        has_image_prompt=image_prompt is not None,
        generate_image=generate_image,
    )

    return {"intent": intent}
