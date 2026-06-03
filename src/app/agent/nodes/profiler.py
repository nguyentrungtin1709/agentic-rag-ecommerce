"""profiler node — loads, merges, and persists the user style profile.

Implements ``ProfilerNode`` (FR-027, FR-028, FR-031, FR-055).

Runs at the start of every agent turn.  In a single node it:
  1. Reads the current ``UserProfile`` JSON from ``AsyncPostgresStore``
     under namespace ``("profiles", user_id)``.
  2. Calls the ``ORCHESTRATOR_MODEL`` with the payload
     ``{current_profile_json, latest_user_message}`` to produce a merged
     profile that incorporates newly observed style signals.
  3. Writes the merged profile back via ``store.aput``.
  4. Returns ``{"user_profile": merged_profile}`` as a partial state update.

This is a stub.  Step 2 (LLM call) is not yet implemented.
"""

from __future__ import annotations

import structlog
from langgraph.store.base import BaseStore

from app.agent.state import AgentState

logger = structlog.get_logger(__name__)


async def profiler(state: AgentState, store: BaseStore) -> dict:
    """Load, merge, and persist the user's long-term style profile.

    This is a stub.  The LLM-driven merge step (FR-027) is not yet
    implemented; the node currently reads the existing profile and writes
    it back unchanged.

    Args:
        state: Current agent state.
        store: LangGraph ``AsyncPostgresStore`` injected by the runtime.

    Returns:
        Partial state update with ``user_profile`` populated.
    """
    # Bind correlation_id to structlog context vars so every log record
    # emitted inside this node (and any awaited callees) automatically
    # includes it — no need to pass it as a keyword argument each time.
    # FR-004 / FR-067 / FR-111.
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="profiler",
    )

    user_id = state["user_id"]
    namespace = ("profiles", user_id)

    # Step 1 — load current profile
    item = await store.aget(namespace, "profile")
    current_profile: dict | None = item.value if item else None

    logger.debug("Profile loaded", user_id=user_id, found=current_profile is not None)

    # Step 2 — LLM merge (STUB: skipped, no changes applied)
    merged_profile = current_profile

    # Step 3 — persist merged profile
    if merged_profile is not None:
        await store.aput(namespace, "profile", merged_profile)
        logger.debug("Profile persisted", user_id=user_id)

    return {"user_profile": merged_profile}
