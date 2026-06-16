"""generate_title node — auto-names the thread after the first exchange.

Runs once after the first complete assistant turn (when
``AgentState.title_generated`` is ``False``).  Calls
``settings.title_model`` (``gpt-4o-mini``) to produce a concise
thread title from the first user message, then persists it via
``ThreadRepository.update_title``.

The node is dispatched on a parallel branch from ``START`` (D12.8)
so it does NOT block the main ``profiler -> summarize -> orchestrate
-> synthesize`` pipeline — the user gets the streamed response first,
the title arrives via a separate ``thread_title`` SSE event a few
hundred milliseconds later.

Resilience (D12.10-D12.11):

- **No-op when title already generated** — the graph re-uses the
  same parallel branch on every turn, but ``title_generated=True``
  short-circuits the work after the first call.
- **Truncation fallback** — after
  ``settings.title_generation_max_attempts`` failed LLM attempts
  (default 3), the node falls back to truncating the first user
  message to ``settings.title_truncation_length`` characters.  This
  guarantees a title is always written; the worst case is a
  less-polished one.
- **LLM exception → empty return** — when the LLM call raises
  (timeout, rate limit, API error), the node logs and returns
  ``{}``.  The attempt counter was already incremented so the next
  turn retries.  The thread keeps working — it just lacks a
  human-readable name until generation succeeds.
- **Valkey cache invalidation is best-effort** — a failure to
  invalidate the thread-list cache is logged but does not fail the
  run; the cache will expire naturally on its TTL.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from app.agent.nodes._sse import emit_sse
from app.agent.prompts import load_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.db.session import get_asyncpg_pool
from app.repositories.thread_repo import ThreadRepository
from app.schemas.chat import ThreadTitlePayload

logger = structlog.get_logger(__name__)


_LLM_TIMEOUT_SECONDS = 10.0
_TITLE_MAX_CHARS = 100


def _sanitize_title(raw: object) -> str:
    """Coerce the LLM output into a clean title string.

    Strips wrapping quotes (some models return ``"Birthday Gift"``
    literally), drops anything after the first newline (the prompt
    asks for one line), trims whitespace, and hard-caps to 100
    characters.  An empty result means the model produced nothing
    usable and the caller should fall back to truncation.

    Args:
        raw: The LLM response — typically an ``AIMessage`` whose
            ``.content`` is a string, but defensively accepts any
            object (``str``, ``AIMessage``, etc.) and coerces to
            ``str`` first.

    Returns:
        Cleaned title string.  ``""`` when the input cannot be
        coerced to a non-empty title.
    """
    text = getattr(raw, "content", raw)
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    # Drop wrapping quotes (single or double).
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    # First line only.
    if "\n" in text:
        text = text.split("\n", 1)[0].strip()
    # Hard cap.
    if len(text) > _TITLE_MAX_CHARS:
        text = text[:_TITLE_MAX_CHARS].strip()
    return text


def _truncate_title(first_user_message: str, max_chars: int) -> str:
    """Truncate the first user message to ``max_chars`` for a fallback title.

    Mirrors the Phase 8 ``title_system`` style: keep the user turn's
    opening words verbatim, cut at the boundary.  Whitespace is
    trimmed so trailing space does not leak into the sidebar.
    """
    return first_user_message[:max_chars].strip()


async def generate_title(
    state: AgentState,
    config: Optional[RunnableConfig] = None,  # noqa: UP045 — LangGraph introspection requires ``Optional[X]``, not PEP 604 ``X | None``
) -> dict:
    """Generate and persist a title for the current thread (FR-022-FR-024).

    See module docstring for the full resilience contract.  Briefly:

    1. No-op when ``state["title_generated"] is True``.
    2. No-op when ``state["first_user_message"]`` is ``None`` or empty.
    3. Increment ``title_generation_attempts``; if the new count
       exceeds the configured maximum, jump to the truncation path.
    4. Call the LLM with a 10-second timeout.
    5. Sanitize the LLM output; if empty, fall through to
       truncation.
    6. Persist via ``ThreadRepository.update_title`` (sets
       ``title_generated=TRUE``).
    7. Best-effort Valkey cache invalidation on the thread-list key.
    8. Emit ``thread_title`` SSE event (best-effort — the queue may
       be ``None`` in tests).
    9. Return ``{"thread_title": <title>, "title_generated": True}``.

    Args:
        state: Current agent state.
        config: LangGraph runtime config; ``config["configurable"]``
            may carry the per-request ``sse_queue`` and the
            ``ValkeyService`` instance.  ``None`` is allowed (e.g.
            in unit tests).

    Returns:
        Partial state update.  ``{}`` on no-op / LLM failure.
        ``{"thread_title": <str>, "title_generated": True}`` on
        success (LLM or truncation fallback).
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="generate_title",
    )

    settings = get_settings()

    # Step 1: skip when the title is already finalised.  The parallel
    # branch runs on every turn but this guard turns subsequent
    # invocations into a single SELECT-only no-op.
    if state.get("title_generated"):
        logger.debug("Title already generated, skipping", thread_id=state["thread_id"])
        return {}

    first_user_message = state.get("first_user_message")
    if not first_user_message:
        # Nothing to derive a title from (e.g. an agent-driven run
        # without a human turn yet).
        logger.debug(
            "No first user message available, skipping title generation",
            thread_id=state["thread_id"],
        )
        return {}

    thread_id = state["thread_id"]
    thread_uuid = uuid.UUID(thread_id) if isinstance(thread_id, str) else thread_id
    configurable = (config.get("configurable", {}) if config else {}) or {}
    sse_queue = configurable.get("sse_queue") if isinstance(configurable, dict) else None
    valkey = configurable.get("valkey") if isinstance(configurable, dict) else None

    # Build a repository against the active asyncpg pool.  Inside a
    # graph run the pool is the long-lived singleton from
    # ``app.state``; tests inject a mock via ``app.db.session``.
    pool = get_asyncpg_pool()
    thread_repo = ThreadRepository(pool)

    # Step 2: increment attempt counter.  When this is the
    # (max+1)-th try we switch to the truncation path so the user
    # still gets *some* title after a few LLM failures.
    attempts = await thread_repo.increment_title_attempts(thread_uuid)
    if attempts > settings.title_generation_max_attempts:
        title = _truncate_title(first_user_message, settings.title_truncation_length)
        logger.warning(
            "generate_title_truncation_fallback",
            thread_id=thread_id,
            reason="attempts_exceeded",
            attempts=attempts,
            max_attempts=settings.title_generation_max_attempts,
            title=title,
        )
        return await _persist_and_emit(
            thread_repo=thread_repo,
            thread_uuid=thread_uuid,
            thread_id=thread_id,
            state=state,
            title=title,
            sse_queue=sse_queue,
            valkey=valkey,
        )

    # Step 3: LLM call with a hard timeout (D12.10).
    llm = ChatOpenAI(model=settings.title_model)
    try:
        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    SystemMessage(content=load_prompt("title_system")),
                    HumanMessage(content=first_user_message),
                ],
                config={"metadata": {"correlation_id": state["correlation_id"]}},
            ),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001 — defensive
        # The attempt counter is already incremented; the next turn
        # retries.  After max+1 attempts we fall back to truncation.
        logger.warning(
            "generate_title_llm_failed",
            thread_id=thread_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {}

    # Step 4: sanitise the LLM output.
    title = _sanitize_title(response)
    if not title:
        logger.warning(
            "generate_title_empty_output",
            thread_id=thread_id,
        )
        return {}

    return await _persist_and_emit(
        thread_repo=thread_repo,
        thread_uuid=thread_uuid,
        thread_id=thread_id,
        state=state,
        title=title,
        sse_queue=sse_queue,
        valkey=valkey,
    )


async def _persist_and_emit(
    *,
    thread_repo: ThreadRepository,
    thread_uuid: uuid.UUID,
    thread_id: str,
    state: AgentState,
    title: str,
    sse_queue: asyncio.Queue | None,
    valkey: Any,
) -> dict:
    """Persist the title, invalidate cache, emit SSE, return state update.

    Centralised so both the LLM path and the truncation path share
    the exact same downstream behaviour.
    """
    await thread_repo.update_title(thread_uuid, title)

    # Cache invalidation is best-effort.  A failure does not block
    # the run — the cache will expire on its TTL.
    if valkey is not None:
        try:
            await valkey.delete_pattern(f"threads:{state['user_id']}:*")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "generate_title_cache_invalidation_failed",
                thread_id=thread_id,
                error=str(exc),
            )

    await emit_sse(sse_queue, "thread_title", ThreadTitlePayload(title=title))

    logger.info(
        "generate_title_completed",
        thread_id=thread_id,
        title=title,
    )

    return {"thread_title": title, "title_generated": True}
