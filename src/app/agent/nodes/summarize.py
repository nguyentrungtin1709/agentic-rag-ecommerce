"""summarize node — threshold-based conversation message summarization.

Runs on every turn, immediately after ``ProfilerNode``.  Acts as a no-op
(returns ``{}``) when the number of messages in state is below
``message_summarize_threshold``.  When the threshold is reached it:

  1. Takes the oldest ``message_summarize_count`` messages.
  2. Calls ``SUMMARIZE_MODEL`` to produce an updated summary string,
     extending any previously accumulated ``state["summary"]``.
  3. Removes the summarised messages from state via ``RemoveMessage``
     and writes the new summary back to ``AgentState.summary``.
"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.config import get_settings

logger = structlog.get_logger(__name__)


async def summarize(state: AgentState) -> dict:
    """Summarize oldest messages when the threshold is reached.

    Args:
        state: Current agent state.

    Returns:
        Empty dict when below threshold; partial state update with
        ``summary`` (new accumulated summary string) and ``messages``
        (list of ``RemoveMessage`` delete ops) when threshold is met.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="summarize",
    )

    settings = get_settings()
    messages = list(state["messages"])
    message_count = len(messages)

    # Step 1 — threshold guard
    if message_count < settings.message_summarize_threshold:
        logger.debug(
            "Summarize skipped (below threshold)",
            thread_id=state["thread_id"],
            message_count=message_count,
            threshold=settings.message_summarize_threshold,
        )
        return {}

    logger.debug(
        "Summarize triggered",
        thread_id=state["thread_id"],
        message_count=message_count,
    )

    # Step 2 — determine cut point: start at message_summarize_count, then walk
    # backward until the first message that would remain is a HumanMessage.
    # This guarantees the surviving message list starts with a HumanMessage,
    # which is required by most LLM providers (e.g. OpenAI, Anthropic).
    # We prefer cutting *less* (safe direction) over cutting more.
    cut = settings.message_summarize_count
    while cut > 0 and not isinstance(messages[cut], HumanMessage):
        cut -= 1

    if cut == 0:
        # Every candidate starts with a non-Human message; skip summarization
        # this turn rather than producing an invalid message sequence.
        logger.warning(
            "Summarize skipped — cannot find a HumanMessage boundary",
            thread_id=state["thread_id"],
            message_count=message_count,
        )
        return {}

    messages_to_summarize = messages[:cut]

    logger.debug(
        "Cut point adjusted",
        requested=settings.message_summarize_count,
        actual=cut,
        thread_id=state["thread_id"],
    )

    # Step 3 — build instruction based on existing summary
    existing_summary = state.get("summary", "")
    if existing_summary:
        instruction = (
            f"Existing summary:\n{existing_summary}\n\n"
            "Incorporate the new messages below and return an updated summary:"
        )
    else:
        instruction = "Summarize the following conversation:"

    # Step 4 — call SUMMARIZE_MODEL
    llm = ChatOpenAI(model=settings.summarize_model)
    response = await llm.ainvoke(
        [
            SystemMessage(content=load_prompt("summarize_system")),
            *messages_to_summarize,
            HumanMessage(content=instruction),
        ]
    )
    new_summary: str = (
        response.content if isinstance(response.content, str) else str(response.content)
    )

    logger.debug(
        "Summary produced",
        thread_id=state["thread_id"],
        chars=len(new_summary),
    )

    # Step 5 — build RemoveMessage delete ops (skip messages without an id)
    delete_ops = [RemoveMessage(id=m.id) for m in messages_to_summarize if m.id is not None]

    return {"summary": new_summary, "messages": delete_ops}
