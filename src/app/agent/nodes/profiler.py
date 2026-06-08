"""profiler node — loads, merges, and persists the user style profile.

Implements ``ProfilerNode`` (FR-027, FR-028, FR-031, FR-055).

Runs at the start of every agent turn.  In a single node it:
  1. Reads the current ``UserProfile`` JSON from ``AsyncPostgresStore``
     under namespace ``("profiles", user_id)``.
  2. Extracts the latest ``HumanMessage`` content from ``state["messages"]``.
  3. Calls ``SUMMARIZE_MODEL`` with structured output (``UserProfile``)
     using only ``current_profile`` and ``latest_message``
     (FR-028 — never the full conversation history).
  4. Writes the merged profile back via ``store.aput``.
  5. Returns ``{"user_profile": merged_profile}`` as a partial state update.
"""

from __future__ import annotations

import json
from typing import cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.store.base import BaseStore

from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.models.profile import UserProfile

logger = structlog.get_logger(__name__)


async def profiler(state: AgentState, store: BaseStore) -> dict:
    """Load, merge, and persist the user's long-term style profile.

    Reads the existing ``UserProfile`` from ``AsyncPostgresStore``, extracts
    the latest user message, merges any new style signals via
    ``SUMMARIZE_MODEL``, persists the result, and returns the merged profile
    as a partial state update.

    Args:
        state: Current agent state.
        store: LangGraph ``AsyncPostgresStore`` injected by the runtime.

    Returns:
        Partial state update ``{"user_profile": <merged profile dict>}``.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="profiler",
    )

    settings = get_settings()
    user_id = state["user_id"]
    namespace = ("profiles", user_id)

    # Step 1 — load current profile
    item = await store.aget(namespace, "profile")
    if item is not None:
        try:
            current_profile = UserProfile.model_validate(item.value)
        except Exception:
            current_profile = UserProfile()
    else:
        current_profile = UserProfile()

    logger.debug("Profile loaded", user_id=user_id, found=item is not None)

    # Step 2 — extract latest HumanMessage (scan from end; FR-028)
    latest_message: str | None = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content
            latest_message = content if isinstance(content, str) else str(content)
            break

    if latest_message is None:
        logger.debug("No HumanMessage found, skipping LLM merge", user_id=user_id)
        return {"user_profile": current_profile.model_dump()}

    # Step 3 — call SUMMARIZE_MODEL with structured output
    # FR-028: pass ONLY current_profile + latest_message (never full history)
    llm = ChatOpenAI(model=settings.summarize_model).with_structured_output(UserProfile)
    human_body = json.dumps(
        {
            "current_profile": current_profile.model_dump(),
            "latest_message": latest_message,
        },
        ensure_ascii=False,
    )
    merged = cast(
        UserProfile,
        await llm.ainvoke(
            [
                SystemMessage(content=load_prompt("profiler_system")),
                HumanMessage(content=human_body),
            ]
        ),
    )

    logger.debug("Profile merged", user_id=user_id)

    # Step 4 — persist merged profile
    await store.aput(namespace, "profile", merged.model_dump())
    logger.debug("Profile persisted", user_id=user_id)

    return {"user_profile": merged.model_dump()}
