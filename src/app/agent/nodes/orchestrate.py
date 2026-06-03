"""orchestrate node — central LLM reasoning step.

Routes the conversation to the appropriate subagent(s) or generates a
direct response when no retrieval is needed.  Uses the ORCHESTRATOR_MODEL
(gpt-4o-mini) to minimise cost on routing decisions.
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def orchestrate(state: AgentState) -> dict:
    """Decide the next action based on the current conversation state.

    This is a stub.  Full implementation will use a LangChain chat model
    with tool calling to decide whether to invoke ``product_rag``,
    ``trend_scout``, both, or neither.

    Args:
        state: Current agent state.

    Returns:
        Partial state update (may add messages or update routing fields).
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="orchestrate",
    )

    logger.debug(
        "Orchestrator invoked",
        thread_id=state["thread_id"],
        message_count=len(state["messages"]),
    )
    # Stub: pass through unchanged.
    return {}
