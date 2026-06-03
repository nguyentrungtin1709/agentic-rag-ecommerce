"""synthesize node — builds the final response for the user.

Combines retrieved products, trend summary, and conversation history
into a coherent, on-brand assistant message using RESPONSE_MODEL (gpt-4o).
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def synthesize(state: AgentState) -> dict:
    """Generate the final response message.

    This is a stub.  Full implementation will use a LangChain chat model
    to render a styled recommendation referencing retrieved products and
    trend insights.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with the assistant ``AIMessage`` appended to
        ``messages``.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="synthesize",
    )

    logger.debug("Synthesizer invoked", thread_id=state["thread_id"])
    # Stub: pass through unchanged.
    return {}
