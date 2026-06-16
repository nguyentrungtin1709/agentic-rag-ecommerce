"""Chat SSE streaming endpoint (Phase 14).

Accepts a user message on an existing thread and streams the agent
response back as a ``text/event-stream`` (D14.1).  Wires the
LangGraph graph (`app.state.graph`) to a per-request
``asyncio.Queue`` that the agent nodes already know how to push to
via :func:`app.agent.nodes._sse.emit_sse`.

Design choices:

- **No try/finally on the HTTP coroutine** — the background task
  ``_run_graph`` owns the idle-reset, the None-sentinel push, and
  the per-exception error event (D14.4, D14.6).  This decouples the
  SSE stream lifetime from the thread-state-machine lifetime and
  keeps the thread correctly reset to ``idle`` even when the client
  disconnects mid-stream.
- **Atomic ``set_status_if_idle``** — the 404 / 410 / 409 guards
  precede the busy claim so a thread is never left in an
  intermediate state (D14.3).
- **Full Path A injection** — ``config["configurable"]`` carries
  every long-lived service the agent nodes consume
  (``qdrant_aclient``, ``openai_client``, ``s3_service``,
  ``valkey_service``).  Injecting only a subset would fail at
  runtime in the corresponding node (D14.7, D14.9).
- **Graph timeout** — ``asyncio.timeout(settings.chat_run_timeout_seconds)``
  caps the whole run; on ``TimeoutError`` the task emits
  ``error {code: "graph_timeout"}`` and resets the thread
  (D14.10).
- **Rate limit** — ``@_limiter.limit("20/minute")`` is applied via
  ``settings.rate_limit_chat`` so the limit is configurable through
  the same env var as the other routers (D14.8).  The
  ``rate_limit`` string is resolved at import time from
  ``Settings`` (the value is cached, so test-time env overrides
  must clear ``get_settings.cache_clear()`` before re-importing).

SSE event taxonomy: see ``app/agent/nodes/_sse.py`` and
``docs/05-IMPLEMENTATION-PLAN.md`` Section 5.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._sse import emit_sse
from app.config import get_settings
from app.dependencies import (
    CurrentUserDep,
    GraphDep,
    SettingsDep,
    ThreadRepoDep,
)
from app.rate_limit import get_limiter
from app.schemas.chat import ChatRequest, ErrorPayload

logger = structlog.get_logger(__name__)

router = APIRouter()
_limiter = get_limiter()
_RATE_LIMIT_CHAT = get_settings().rate_limit_chat


@router.post(
    "/{thread_id}/runs/stream",
    summary="Send a message and stream the agent response via SSE",
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
)
@_limiter.limit(_RATE_LIMIT_CHAT)  # D14.8 — 20/minute by default
async def stream_run(
    request: Request,
    thread_id: uuid.UUID,
    body: ChatRequest,
    current_user: CurrentUserDep,
    settings: SettingsDep,
    graph: GraphDep,
    thread_repo: ThreadRepoDep,
) -> StreamingResponse:
    """Stream the agent response for a new user message (FR-001, FR-003).

    Accepts ``{message, generate_image}`` and streams seven SSE event
    types: ``token``, ``products``, ``image_ready``, ``image_failed``,
    ``thread_title``, ``done``, ``error`` (D14.1-D14.10).

    Behaviour (D14.1-D14.10):

    1. 404 when the thread is not owned by the caller.
    2. 410 when the thread is mid-deletion.
    3. 409 when the thread is busy (concurrent request or stuck state).
    4. Atomically claim the thread for this run via
       ``set_status_if_idle``.
    5. Build the per-request ``sse_queue`` and the LangGraph
       ``config`` carrying every Path A service (D14.7, D14.9).
    6. Schedule ``_run_graph`` as a background task; return
       ``StreamingResponse`` whose body is an ``event_generator``
       that drains the queue and yields ``text/event-stream``
       frames (D14.1, D14.2).
    7. ``_run_graph`` runs the graph under
       ``asyncio.timeout(settings.chat_run_timeout_seconds)`` and
       resets the thread to ``idle`` in its ``finally`` block
       (D14.4, D14.10).

    Args:
        request: Inbound FastAPI request — used to read
            ``request.app.state`` for the long-lived Path A services
            (D14.7, D14.9).
        thread_id: UUID of the target thread.
        body: Parsed ``ChatRequest`` payload.
        current_user: Verified JWT claims; ``sub`` is the owner id.
        settings: Cached application settings (rate-limit string,
            graph timeout).
        graph: Compiled LangGraph ``Pregel`` from ``app.state.graph``.
        thread_repo: Async repository for thread state transitions.

    Returns:
        A ``StreamingResponse`` with ``media_type='text/event-stream'``
        and the no-cache headers required by SSE.

    Raises:
        HTTPException: 404 / 410 / 409 on status-guard failures.
    """
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        thread_id=str(thread_id),
        user_id=current_user["sub"],
        endpoint="stream_run",
    )

    # Step 1: scope the thread to the current user.
    thread = await thread_repo.get(thread_id, current_user["sub"])
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found.",
        )

    # Step 2: refuse if the thread is mid-deletion (Phase 9 pattern).
    if thread.status == "deleting":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Thread is being deleted.",
        )

    # Step 3: atomically claim the thread for this run.  A False
    # return means the row was already busy (concurrent request
    # won the race) OR a different non-idle status slipped through
    # between get() and set_status_if_idle() (e.g. admin
    # force-flipped to 'deleting').
    claimed = await thread_repo.set_status_if_idle(thread_id, current_user["sub"], "busy")
    if not claimed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Thread is busy with another run.",
        )

    # Step 4: build the per-request queue, config, and initial
    # state.  All four Path A services are injected so every node
    # in the graph sees its dependencies on ``configurable``
    # (D14.7, D14.9).
    sse_queue: asyncio.Queue = asyncio.Queue()
    app_state = request.app.state
    config: RunnableConfig = {
        "configurable": {
            # Per-request / per-thread identifiers
            "thread_id": str(thread_id),
            "user_id": current_user["sub"],
            "correlation_id": correlation_id,
            "sse_queue": sse_queue,
            # Path A — long-lived singletons from app.state
            # (D5.1, D14.7, D14.9).  Every node that needs a
            # shared client/service reads it from this dict.
            "qdrant_aclient": app_state.qdrant.client,
            "openai_client": app_state.openai,
            "s3_service": app_state.s3,
            # Single unified key (D14.9) — both generate_title
            # and generate_image read this.  The old bare key
            # "valkey" is removed from the configurable entirely.
            "valkey_service": app_state.valkey,
        },
        "metadata": {
            "correlation_id": correlation_id,
            "thread_id": str(thread_id),
            "user_id": current_user["sub"],
        },
    }

    initial_state: dict[str, Any] = {
        "messages": [HumanMessage(content=body.message)],
        "user_id": current_user["sub"],
        "thread_id": str(thread_id),
        "correlation_id": correlation_id,
        "generate_image": body.generate_image,
        "first_user_message": body.message,
        # The rest of the state fields (user_profile,
        # retrieved_products, trend_summary, thread_title, intent,
        # title_generated, fallback_count, image_url, image_prompt,
        # summary) are loaded by the graph nodes themselves from
        # the AsyncPostgresStore / the checkpointer / LLM calls.
        # LangGraph's reducer carries them across turns
        # automatically; do NOT re-initialise them here or the
        # prior turn's state is wiped.
    }

    async def _run_graph() -> None:
        """Background coroutine: drive the graph, then signal end-of-stream.

        Catches every exception (including ``TimeoutError`` from
        ``asyncio.timeout``), translates failures to a single
        ``error`` SSE event, and always pushes the ``None`` sentinel
        so the consumer generator can break out (D14.4, D14.6,
        D14.10).  This coroutine NEVER raises — its caller is
        fire-and-forget.  The idle reset lives here (not in
        ``stream_run``) so the thread state is correctly returned
        to ``'idle'`` even when the HTTP client disconnects
        mid-stream.
        """
        try:
            async with asyncio.timeout(settings.chat_run_timeout_seconds):
                await graph.ainvoke(initial_state, config=config)
        except TimeoutError:
            logger.error(
                "chat_run_graph_timeout",
                correlation_id=correlation_id,
                thread_id=str(thread_id),
                user_id=current_user["sub"],
                timeout_seconds=settings.chat_run_timeout_seconds,
            )
            await emit_sse(
                sse_queue,
                "error",
                ErrorPayload(
                    code="graph_timeout",
                    message=(
                        f"The agent exceeded the {settings.chat_run_timeout_seconds}s run budget."
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001  -- endpoint-level boundary
            logger.error(
                "chat_run_graph_failed",
                correlation_id=correlation_id,
                thread_id=str(thread_id),
                user_id=current_user["sub"],
                error=str(exc),
                exc_info=True,
            )
            await emit_sse(
                sse_queue,
                "error",
                ErrorPayload(
                    code="internal_error",
                    message="The agent failed to complete the run.",
                ),
            )
        finally:
            # Single idle reset covering success and failure paths
            # (D14.4).  Wrapped in its own try/except so a DB error
            # during reset never crashes the cleanup sentinel.
            try:
                await thread_repo.set_status(thread_id, "idle")
                await thread_repo.touch(thread_id)
            except Exception:  # pragma: no cover  -- last-ditch logging
                logger.exception(
                    "thread_idle_reset_failed",
                    thread_id=str(thread_id),
                    correlation_id=correlation_id,
                )
            try:
                sse_queue.put_nowait(None)
            except Exception:  # pragma: no cover  -- defensive
                logger.warning(
                    "sse_queue_sentinel_push_failed",
                    correlation_id=correlation_id,
                    thread_id=str(thread_id),
                )

    asyncio.create_task(_run_graph())

    async def event_generator() -> AsyncIterator[bytes]:
        """Drain the SSE queue and yield ``text/event-stream`` frames.

        Frames are formatted as::

            event: <type>
            data: <json string>

        terminated by a blank line.  The generator stops when it
        pops the ``None`` sentinel pushed by the background task.
        """
        while True:
            item = await sse_queue.get()
            if item is None:
                return
            yield (
                f"event: {item['type']}\n"
                f"data: {json.dumps(item['payload'], ensure_ascii=False)}\n\n"
            ).encode()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )
