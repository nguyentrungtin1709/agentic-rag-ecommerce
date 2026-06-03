"""trend_scout subagent — web trend research using Tavily + DuckDuckGo.

Implements a ReAct-style subagent that searches the web for current
print-on-demand design trends relevant to the user's query.  Results
are summarised into a concise trend paragraph by the ORCHESTRATOR_MODEL.

The subagent is invoked by the orchestrator node when the query has a
strong trend-research signal (e.g. "what's popular right now").
"""

from __future__ import annotations

import structlog

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def run_trend_scout(state: AgentState) -> dict:
    """Run the trend scout subagent and populate trend_summary.

    This is a stub.  Full implementation will:
    1. Extract the user query from the latest ``HumanMessage``.
    2. Call Tavily Search API for curated results.
    3. Fall back to DuckDuckGo if Tavily quota is exhausted.
    4. Summarise findings into a 2-3 sentence trend paragraph.
    5. Return the summary in ``trend_summary``.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``trend_summary`` populated.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="trend_scout",
    )

    logger.debug(
        "trend_scout subagent invoked (stub)",
        thread_id=state["thread_id"],
        user_id=state["user_id"],
    )
    return {"trend_summary": ""}
