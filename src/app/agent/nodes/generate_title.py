"""generate_title node — auto-names the thread after the first exchange.

Runs once after the first complete assistant turn (when
``AgentState.title_generated`` is ``False``).  Calls TITLE_MODEL
(gpt-4o-mini) to generate a concise thread title from the first
user/assistant message pair.  Writes the title via ``ThreadRepository``
and stores it in ``AgentState.thread_title``.
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def generate_title(state: AgentState) -> dict:
    """Generate and persist a title for the current thread.

    This is a stub.  Full implementation will call gpt-4o-mini with the
    first user message and first assistant response to produce a concise
    title, then persist it via ``ThreadRepository.update_title()``.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``thread_title`` set.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="generate_title",
    )

    logger.debug("Title generation skipped (stub)", thread_id=state["thread_id"])
    return {"thread_title": None}
