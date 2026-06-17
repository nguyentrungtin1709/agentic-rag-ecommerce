"""TrendScout subagent -- web trend research using Tavily + DuckDuckGo.

Public API exported here is consumed by the parent graph::

    from app.agent.subagents.trend_scout import run_trend_scout
    from app.agent.subagents.trend_scout import TrendScoutOutput, TrendScoutState
"""

from __future__ import annotations

from app.agent.subagents.trend_scout.agent import run_trend_scout
from app.agent.subagents.trend_scout.schemas import TrendScoutOutput
from app.agent.subagents.trend_scout.state import TrendScoutState

__all__ = ["run_trend_scout", "TrendScoutOutput", "TrendScoutState"]
