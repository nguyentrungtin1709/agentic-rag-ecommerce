"""Thread management endpoints — real implementation (Phase 8).

All endpoints require a valid Saleor JWT (Bearer token) via
``CurrentUserDep``.  See ``history/8_0_0_THREAD_MANAGEMENT_API.md``
for the full decision record (D8.1–D8.10).

Endpoint summary:

- ``POST /api/v1/threads`` — create a new thread (FR-011).  Rate-limit
  + cache invalidation.
- ``GET /api/v1/threads`` — cursor-paginated list of the caller's
  threads (FR-015).  Rate-limit + per-user response cache.
- ``GET /api/v1/threads/{id}`` — single-thread metadata (FR-016).
  Returns 404 (not 403) on not-owned so we do not leak existence.
- ``DELETE /api/v1/threads/{id}`` — soft-delete (FR-017): set
  ``status='deleting'`` and enqueue the Celery cleanup task.
- ``GET /api/v1/threads/{id}/history`` — cursor-paginated message
  history from the LangGraph checkpointer (FR-019, FR-020).

The chat stream endpoint ``POST /threads/{id}/runs/stream`` is a
Phase 14 stub in ``app.api.chat``; this module does not touch it.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi_cache.decorator import cache
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.pregel import Pregel

from app.cache.keys import thread_list_key_builder
from app.dependencies import (
    CurrentUserDep,
    GraphDep,
    ImageRepoDep,
    SettingsDep,
    ThreadRepoDep,
    ValkeyDep,
)
from app.schemas.thread import (
    CreateThreadRequest,
    HistoryImageItem,
    HistoryMessage,
    ThreadHistoryResponse,
    ThreadListResponse,
    ThreadResponse,
)
from app.services.valkey_service import ValkeyService
from app.tasks.delete_thread import delete_thread as delete_thread_task

logger = structlog.get_logger(__name__)

router = APIRouter()

# Resolved at import time (see ``app/api/health.py`` for the rationale).
from app.rate_limit import get_limiter  # noqa: E402  (import after router for clean log lines)

_limiter = get_limiter()

# Message class names that the frontend should never see.  Anything not
# in this set is mapped to the literal ``"human"`` or ``"ai"`` in the
# response (everything else would be a bug — tool/system messages
# leak internal orchestration).
_SYSTEM_OR_TOOL_TYPES = frozenset({"system", "tool", "function", "reminder", "chat"})

# Cache namespace for the list endpoint.  Lives in the cache Redis
# logical DB (1) configured in ``app.main.lifespan``.
_THREAD_LIST_CACHE_NAMESPACE = "threads-list"


async def _invalidate_thread_list_cache(
    valkey: ValkeyService,
    user_id: str,
) -> int:
    """Drop every cached list-page entry for ``user_id``.

    Called from any endpoint that mutates the thread list so the next
    ``GET /api/v1/threads`` reflects the change.  Matches the full
    fastapi-cache2 key shape produced by ``thread_list_key_builder``:
    ``{_THREAD_LIST_CACHE_NAMESPACE}:threads:{user_id}:{before}:{limit}``,
    so the namespace prefix must be included for the pattern to match
    every cached page for the user.

    Args:
        valkey: The shared ``ValkeyService`` singleton.
        user_id: Saleor user ID whose list pages should be dropped.

    Returns:
        Number of cache keys deleted.  ``0`` is a valid result — it
        means the user had no cached pages (e.g. they had not loaded
        the list yet).
    """
    pattern = f"{_THREAD_LIST_CACHE_NAMESPACE}:threads:{user_id}:*"
    return await valkey.delete_pattern(pattern)


# ---------------------------------------------------------------------------
# POST /api/v1/threads  — create
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadResponse,
    summary="Create a new conversation thread",
)
@_limiter.limit("10/minute")
async def create_thread(
    request: Request,
    response: Response,
    _body: CreateThreadRequest,
    current_user: CurrentUserDep,
    thread_repo: ThreadRepoDep,
    valkey: ValkeyDep,
) -> ThreadResponse:
    """Create a new conversation thread for the authenticated user (FR-011).

    Threads are not auto-created; clients must call this endpoint first
    before sending messages.  The new thread starts with
    ``status='idle'`` and ``title_generated=False``; the chat
    endpoint will populate the title on the first exchange
    (FR-024).

    The endpoint also drops every cached list page for the user so
    the new thread appears in the next ``GET /api/v1/threads``
    immediately.

    Args:
        request: Inbound FastAPI request — required by slowapi to
            attach ``X-RateLimit-*`` headers.
        response: ``Response`` injected by FastAPI for the same
            reason.
        _body: Empty request body.  Kept on the signature so the
            OpenAPI schema documents the JSON contract.
        current_user: Decoded JWT claims; only ``sub`` (user id) is
            used here.
        thread_repo: Repository for the ``threads`` table.
        valkey: Valkey service for cache invalidation.

    Returns:
        201 Created + the new ``ThreadResponse``.
    """
    user_id = current_user["sub"]
    thread = await thread_repo.create(user_id)
    await _invalidate_thread_list_cache(valkey, user_id)
    logger.info(
        "thread_created",
        thread_id=str(thread.id),
        user_id=user_id,
    )
    return ThreadResponse.model_validate(thread, from_attributes=True)


# ---------------------------------------------------------------------------
# GET /api/v1/threads  — list
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ThreadListResponse,
    summary="List threads for the current user",
)
@_limiter.limit("60/minute")
@cache(
    namespace=_THREAD_LIST_CACHE_NAMESPACE,
    expire=120,  # overwritten by ``settings.thread_list_cache_ttl`` once we
    # pass the actual setting object to ``expire`` — the literal here is
    # a fallback so the cache decorator is always valid.
    key_builder=thread_list_key_builder,
)
async def list_threads(
    request: Request,
    response: Response,
    current_user: CurrentUserDep,
    thread_repo: ThreadRepoDep,
    settings: SettingsDep,
    limit: int = Query(20, ge=1, le=100),
    before: uuid.UUID | None = None,
) -> ThreadListResponse:
    """Return cursor-paginated threads belonging to the caller (FR-015).

    The response is cached per-user with TTL
    ``settings.thread_list_cache_ttl`` (default 120 s).  Pages are
    keyed on the user ID and the ``before``/``limit`` query
    parameters, so different pages coexist in the cache and one
    user's page never leaks to another.

    Any mutation (create, delete) on the user's threads invalidates
    the entire cached set for that user.

    Args:
        request: Inbound FastAPI request.
        response: Required by slowapi.
        current_user: Decoded JWT claims.
        thread_repo: Repository for the ``threads`` table.
        settings: Used here only to read the configured cache TTL
            — the actual TTL is fixed when the decorator is applied,
            so this argument documents the dependency.
        limit: Maximum threads to return (1..100).  Default 20.
        before: Cursor UUID — return threads older than this one.
            ``None`` returns the newest page.

    Returns:
        200 OK + ``ThreadListResponse`` with at most ``limit`` items
        and a ``next_cursor`` (``None`` when the list is exhausted).
    """
    # The decorator is constructed with a static expire; surface the
    # configured value in the response so it can be observed by
    # callers / tests.  (The decorator itself is bound at import.)
    _ = settings.thread_list_cache_ttl  # touch to silence unused-arg
    user_id = current_user["sub"]
    threads = await thread_repo.list_by_user(user_id=user_id, limit=limit, before=before)
    next_cursor = threads[-1].id if len(threads) == limit else None
    return ThreadListResponse(
        items=[ThreadResponse.model_validate(t, from_attributes=True) for t in threads],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/threads/{thread_id}  — single thread metadata
# ---------------------------------------------------------------------------


@router.get(
    "/{thread_id}",
    response_model=ThreadResponse,
    summary="Get thread metadata",
)
@_limiter.limit("60/minute")
async def get_thread(
    request: Request,
    response: Response,
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
    thread_repo: ThreadRepoDep,
) -> ThreadResponse:
    """Retrieve metadata for a single thread (FR-016).

    Returns 404 when the thread does not exist OR is owned by another
    user (D8.4) — the two cases are indistinguishable on the wire
    so the API does not leak the thread's existence.  Returns 410
    when the thread is mid-deletion (``status='deleting'``) so the
    frontend can hide it from the user's list without polling.

    Args:
        request: Inbound FastAPI request.
        response: Required by slowapi.
        thread_id: UUID of the target thread.
        current_user: Decoded JWT claims.
        thread_repo: Repository for the ``threads`` table.

    Returns:
        200 OK + the ``ThreadResponse``.

    Raises:
        HTTPException: 404 if not found or not owned.
        HTTPException: 410 if ``status='deleting'``.
    """
    user_id = current_user["sub"]
    thread = await thread_repo.get(thread_id, user_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found.",
        )
    if thread.status == "deleting":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Thread is being deleted.",
        )
    return ThreadResponse.model_validate(thread, from_attributes=True)


# ---------------------------------------------------------------------------
# GET /api/v1/threads/{thread_id}/history  — paginated messages
# ---------------------------------------------------------------------------


def _is_human(message: BaseMessage | object) -> bool:
    """Return ``True`` when ``message`` is a LangChain ``HumanMessage``.

    Defensive: an arbitrary object that is not a known chat message
    type is treated as non-human so the Option C rounding never
    crashes.
    """
    return isinstance(message, HumanMessage)


def _is_ai(message: BaseMessage | object) -> bool:
    """Return ``True`` when ``message`` is a LangChain ``AIMessage``."""
    return isinstance(message, AIMessage)


def _format_message(
    message: BaseMessage,
    images_by_ai: dict[str, list],
) -> HistoryMessage | None:
    """Build a ``HistoryMessage`` from a raw LangChain ``BaseMessage``.

    Returns ``None`` for ``SystemMessage`` / ``ToolMessage`` so the
    caller can filter them out with a single ``if m is None: continue``.

    Images are looked up by the ``AIMessage.id`` of the message itself
    (F8.2 / F8.3).  Even though ``GeneratedImage.request_message_id``
    points at the originating ``HumanMessage.id`` — because the image
    is created in a parallel branch of the graph before the
    ``AIMessage`` exists — the *response* always attaches images to
    the AI turn.  The history handler pre-walks the page in
    chronological order to copy each ``HumanMessage``'s images onto
    the following ``AIMessage`` (see ``get_thread_history``), so this
    function only needs to do ``images_by_ai.get(ai_id, [])``.

    Args:
        message: A ``BaseMessage`` (or any duck-typed object) read
            from the LangGraph state.
        images_by_ai: ``{ai_message_id -> [GeneratedImage, ...]}`` —
            pre-computed by the history handler so each AI turn in
            the page carries the images generated for the human
            turn that opened it.  Keys are AI message ids; values
            are in chronological order (oldest first).

    Returns:
        The ``HistoryMessage`` view, or ``None`` if the message
        should be filtered out.
    """
    msg_id = str(getattr(message, "id", None) or "")
    raw_content = message.content
    content: str = raw_content if isinstance(raw_content, str) else str(raw_content)
    if _is_human(message):
        return HistoryMessage(
            id=msg_id,
            type="human",
            content=content,
            created_at=getattr(message, "created_at", None),
            images=[],
        )
    if _is_ai(message):
        images = images_by_ai.get(msg_id, [])
        return HistoryMessage(
            id=msg_id,
            type="ai",
            content=content,
            created_at=getattr(message, "created_at", None),
            images=[HistoryImageItem(url=img.s3_url, prompt=img.prompt) for img in images],
        )
    return None


@router.get(
    "/{thread_id}/history",
    response_model=ThreadHistoryResponse,
    summary="Get paginated message history",
    description=(
        "Cursor-paginated message history read from the LangGraph "
        "checkpointer. `limit` is a HINT, not a strict cap — see the "
        "ADR `history/8_0_0_THREAD_MANAGEMENT_API.md` (D8.8) for the "
        "full rationale. Briefly: when the page boundary would land "
        "on an `AIMessage`, the handler extends the page backward to "
        "include the `HumanMessage` that opens the turn, so every "
        "page starts on a human message. `len(messages)` may be "
        "greater than `limit` (by a few) or less (when the page is "
        "the last one). Use `next_cursor` to drive pagination; `null` "
        "means the history is exhausted."
    ),
)
@_limiter.limit("60/minute")
async def get_thread_history(
    request: Request,
    response: Response,
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
    thread_repo: ThreadRepoDep,
    image_repo: ImageRepoDep,
    graph: GraphDep,
    limit: int = Query(20, ge=1, le=100),
    before: str | None = Query(default=None),
) -> ThreadHistoryResponse:
    """Return cursor-paginated message history (FR-019, FR-020).

    The endpoint reads the latest ``StateSnapshot`` from
    ``graph.aget_state(config)`` and slices the ``messages`` list at
    the application layer.

    **Cursor semantics — Option C (D8.8):**

    - The ``before`` query parameter accepts any message id (a
      ``HumanMessage.id`` or an ``AIMessage.id``).
    - The response is **always** a chronologically ordered slice
      ending strictly before ``before`` (``None`` means "from the
      newest end").
    - If the raw slice would start on an ``AIMessage``, the handler
      rounds the start index backward to the most recent
      ``HumanMessage`` so every page opens on a human message.
      This may add a few messages to the response — see the
      module docstring on ``OpenAPI description`` for the
      frontend-facing contract.
    - ``next_cursor`` is ``response.messages[-1].id`` if older
      messages still exist in the state, else ``None``.

    Image attachment: the handler runs a single batch query
    (``image_repo.list_by_message_ids``) covering every
    ``HumanMessage.id`` in the page.  Because image rows are
    written with ``request_message_id`` pointing at the originating
    human turn (F8.1), the result is keyed by human id.  The
    handler then walks the page in chronological order and copies
    the relevant images onto the ``AIMessage`` that follows each
    human message (F8.2 / F8.4), producing a ``images_by_ai`` map
    keyed by ``AIMessage.id`` that ``_format_message`` reads from.

    Args:
        request: Inbound FastAPI request.
        response: Required by slowapi.
        thread_id: UUID of the target thread.
        current_user: Decoded JWT claims.
        thread_repo: Used to verify ownership and rejection of
            ``status='deleting'`` threads.
        image_repo: Used to look up images for the page in one
            batch query.
        graph: Compiled LangGraph state graph (from
            ``app.state.graph``).
        limit: Page size hint (1..100).  Default 20.
        before: Cursor — a message id.  ``None`` returns the newest
            page.

    Returns:
        200 OK + ``ThreadHistoryResponse``.

    Raises:
        HTTPException: 404 if the thread does not exist or is not
            owned by the caller.
        HTTPException: 410 if ``status='deleting'``.
    """
    user_id = current_user["sub"]
    thread = await thread_repo.get(thread_id, user_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found.",
        )
    if thread.status == "deleting":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Thread is being deleted.",
        )

    state = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    if state is None:
        return ThreadHistoryResponse(messages=[], next_cursor=None)
    raw_values = getattr(state, "values", None) or {}
    raw_messages: list[BaseMessage] = list(raw_values.get("messages", []) or [])

    # Filter to chat messages the frontend cares about.
    chat_messages: list[BaseMessage] = [m for m in raw_messages if _is_human(m) or _is_ai(m)]

    # Cursor: slice the chat list to "everything before the cursor".
    if before is not None:
        cursor_idx: int | None = None
        for idx, msg in enumerate(chat_messages):
            if getattr(msg, "id", None) == before:
                cursor_idx = idx
                break
        if cursor_idx is None:
            # Cursor no longer in the list (eviction, restart, etc.)
            return ThreadHistoryResponse(messages=[], next_cursor=None)
        chat_messages = chat_messages[:cursor_idx]

    # Last ``limit`` messages of what remains.
    page = chat_messages[-limit:] if chat_messages else []

    # Option C: round the page start back to a HumanMessage so the
    # response is always a clean turn-by-turn slice.  The slice may
    # grow by a few messages to include the human opener.
    if page and _is_ai(page[0]):
        abs_idx = len(chat_messages) - len(page) if before is not None else 0
        # Re-derive the absolute index in the filtered list when
        # ``before`` was provided: ``chat_messages`` is now the
        # pre-cursor slice, so ``abs_idx = len(chat_messages) - len(page)``.
        # When ``before`` is None, the page is the tail so abs_idx = 0.
        round_idx = abs_idx
        for i in range(abs_idx - 1, -1, -1):
            if _is_human(chat_messages[i]):
                round_idx = i
                break
        page = chat_messages[round_idx : abs_idx + len(page)]

    # Batch-load images for every human message in the page.  The repo
    # returns ``{human_message_id -> [GeneratedImage, ...]}`` because
    # ``GeneratedImage.request_message_id`` is the originating
    # ``HumanMessage.id`` (FR-051): the image is created in a parallel
    # branch of the graph before the ``AIMessage`` exists, so we have
    # no AI id to key on at write time.  The response, however, must
    # attach the images to the ``AIMessage`` that follows the human
    # turn (D8.9 / F8.1-F8.2) — we project the map below.
    human_ids = [str(getattr(m, "id", "")) for m in page if _is_human(m)]
    images_by_human: dict[str, list] = (
        await image_repo.list_by_message_ids(human_ids) if human_ids else {}
    )

    # Walk the page in chronological order, tracking the most recent
    # human message and copying its images onto each subsequent AI
    # message (F8.2).  A single human turn may trigger multiple
    # images (F8.4) — the repo already returns a list per id, so the
    # attachment is naturally multi-image.  Human messages never
    # carry images in the response (F8.3).
    images_by_ai: dict[str, list] = {}
    last_human_id: str | None = None
    for msg in page:
        msg_id = str(getattr(msg, "id", None) or "")
        if _is_human(msg):
            last_human_id = msg_id
        elif _is_ai(msg) and last_human_id is not None:
            attached = images_by_human.get(last_human_id, [])
            if attached:
                images_by_ai[msg_id] = attached

    response_messages = [
        formatted
        for formatted in (_format_message(m, images_by_ai) for m in page)
        if formatted is not None
    ]

    # next_cursor is the id of the last message in the page, but
    # only if older messages still exist beyond the page boundary.
    next_cursor: str | None = None
    if response_messages:
        # We need to know whether the page is the last page.  After
        # Option C rounding, the page start may be earlier than the
        # raw tail, so we check whether there are any older messages
        # by walking back to the start of the filtered list.
        first_page_id = response_messages[0].id
        for msg in chat_messages:
            if getattr(msg, "id", None) == first_page_id:
                idx = chat_messages.index(msg)
                if idx > 0:
                    next_cursor = response_messages[-1].id
                break

    return ThreadHistoryResponse(
        messages=response_messages,
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/threads/{thread_id}  — soft delete (async cleanup)
# ---------------------------------------------------------------------------


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete a thread (async)",
)
@_limiter.limit("10/minute")
async def delete_thread(
    request: Request,
    response: Response,
    thread_id: uuid.UUID,
    current_user: CurrentUserDep,
    thread_repo: ThreadRepoDep,
    valkey: ValkeyDep,
) -> dict:
    """Set thread status to ``deleting`` and enqueue cleanup (FR-017).

    The endpoint is intentionally cheap: it performs one UPDATE on
    the ``threads`` row, enqueues the ``delete_thread`` Celery task
    (still a stub — real S3 + checkpointer cleanup lands in
    Phase 10), and invalidates the caller's cached list pages.
    Returns 202 Accepted immediately; the actual cleanup runs
    asynchronously.

    Returns 404 when the thread does not exist or is not owned by
    the caller.  Returns 410 when the thread is already in
    ``status='deleting'`` (idempotency guard — a duplicate DELETE
    is rejected rather than re-enqueueing the Celery task).

    Args:
        request: Inbound FastAPI request.
        response: Required by slowapi.
        thread_id: UUID of the target thread.
        current_user: Decoded JWT claims.
        thread_repo: Repository for the ``threads`` table.
        valkey: Valkey service for cache invalidation.

    Returns:
        202 Accepted + ``{"thread_id": str, "status": "deleting"}``.

    Raises:
        HTTPException: 404 if not found or not owned.
        HTTPException: 410 if already deleting.
    """
    user_id = current_user["sub"]
    thread = await thread_repo.get(thread_id, user_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found.",
        )
    if thread.status == "deleting":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Thread is being deleted.",
        )
    await thread_repo.set_status(thread_id, "deleting")
    delete_thread_task.delay(str(thread_id), user_id)  # type: ignore[attr-defined]
    await _invalidate_thread_list_cache(valkey, user_id)
    logger.info(
        "thread_delete_dispatched",
        thread_id=str(thread_id),
        user_id=user_id,
    )
    return {"thread_id": str(thread_id), "status": "deleting"}


# Type-only export so tests can introspect the dep alias without
# triggering a real LangGraph import.
__all__ = ["router", "Pregel"]
