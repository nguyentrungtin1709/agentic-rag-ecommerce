"""Internal SSE event-bus helpers shared by every terminal node.

The chat SSE endpoint (Phase 14) hands every run a fresh
``asyncio.Queue`` (one queue per HTTP request) and threads it into
the graph via ``config["configurable"]["sse_queue"]``.  Any node
that wants to push an event to the wire calls :func:`emit_sse` with
the typed payload; the endpoint drains the queue and serialises
each entry as an ``event: <type>\\ndata: <json>\\n\\n`` SSE frame.

The helper is intentionally tiny:

- It only knows about ``put_nowait`` (non-blocking) because the
  emitter always runs inside the agent graph and must never block
  the LLM stream.  Slow consumers are mitigated upstream — the
  endpoint caps queue depth and drops the run if it overflows.
- It silently no-ops when the queue is ``None`` (tests that do not
  care about streaming, and the Phase 12 parallel ``generate_image``
  branch when the request is not a streaming request).
- It never raises — a malformed payload must not crash the agent
  graph.  Errors are logged via structlog and dropped on the floor.

This module is INTERNAL — the public schema types live in
:mod:`app.schemas.chat`.  Callers import ``emit_sse`` from here but
they construct the Pydantic payload themselves so the schemas stay
the single source of truth for the wire contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


async def emit_sse(
    queue: asyncio.Queue | None,
    event_type: str,
    payload: BaseModel,
) -> None:
    """Enqueue a typed event onto the per-request SSE queue.

    Silently no-ops when ``queue`` is ``None`` (tests that don't care
    about streaming, or non-streaming callers).  Wraps any
    ``queue.put_nowait`` failure in a structlog warning — we never
    want a malformed SSE payload or a full queue to crash the
    agent graph.

    Args:
        queue: The per-request ``asyncio.Queue`` injected at the API
            boundary (Phase 14, D14.x).  ``None`` is allowed.
        event_type: SSE ``event:`` field value, e.g. ``"token"``,
            ``"products"``, ``"image_ready"``, ``"thread_title"``,
            ``"done"``, ``"error"``.  See ``docs/05-IMPLEMENTATION-PLAN.md``
            Section 5 for the consolidated taxonomy.
        payload: Pydantic model instance to serialise via
            ``model_dump()``.  The dict is sent as the SSE ``data:``
            field as a JSON string by the endpoint.
    """
    if queue is None:
        return
    try:
        event: dict[str, Any] = {
            "type": event_type,
            "payload": payload.model_dump(),
        }
        queue.put_nowait(event)
    except Exception as exc:  # pragma: no cover — defensive
        # Never let a queue/serialisation failure crash the agent.
        # The endpoint will time out and the client will see a
        # truncated stream — recoverable, log and move on.
        logger.warning(
            "sse_emit_failed",
            event_type=event_type,
            error=str(exc),
            error_type=type(exc).__name__,
        )
