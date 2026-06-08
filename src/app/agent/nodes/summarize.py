"""summarize node — threshold-based conversation message summarization.

Runs on every turn, immediately after ``ProfilerNode``.  Acts as a no-op
(returns ``{}``) when the number of messages in state is below
``MESSAGE_SUMMARIZE_THRESHOLD``.  When the threshold is reached it:

  1. Takes the oldest ``MESSAGE_SUMMARIZE_COUNT`` messages.
  2. Calls ``SUMMARIZE_MODEL`` to produce an updated summary string,
     extending any previously accumulated ``state["summary"]``.
  3. Removes the summarised messages from state via ``RemoveMessage``
     and writes the new summary back to ``AgentState.summary``.

This is a stub.  Full implementation is in Phase 2.
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def summarize(state: AgentState) -> dict:
    """Summarize oldest messages when the threshold is reached.

    This is a stub.  Full implementation covers LLM call, RemoveMessage
    operations, and summary accumulation (Phase 2).

    Args:
        state: Current agent state.

    Returns:
        Empty dict (no-op) below threshold; partial state update with
        ``summary`` and ``messages`` delete ops when threshold is met.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="summarize",
    )

    logger.debug(
        "Summarize node invoked (stub — no-op)",
        thread_id=state["thread_id"],
        message_count=len(state["messages"]),
    )
    # Stub: always pass through unchanged.
    return {}
