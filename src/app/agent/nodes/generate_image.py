"""generate_image node — DALL-E image generation with S3 + quota + SSE (Phase 13).

Runs as a parallel branch from the orchestrator when the user opts
into image generation for a turn (FR-040 - FR-053).  The node
assembles the full chain in one call:

1. Skip guards — ``generate_image`` flag and ``image_prompt``
   presence (D13.1, D13.2).
2. Daily quota check via Valkey (D13.3) — fails fast when the
   user has already hit ``settings.image_daily_limit`` for the day.
3. DALL-E call via the injected ``AsyncOpenAI`` client with
   ``response_format="b64_json"`` (D13.4).  Decoding the base64
   payload avoids a second HTTP call to fetch the public URL
   (which would be valid for ~1h only).
4. S3 upload via the injected ``S3Service`` (D13.5) — content type
   ``image/png``.
5. DB row insert via ``ImageRepository.create`` (D13.6) — the
   ``request_message_id`` is the **last** HumanMessage id in
   ``state["messages"]`` (FR-051).  The HumanMessage convention is
   locked in F8.1 — at image-creation time the synthesised
   AIMessage does not exist yet because ``synthesize`` runs in
   parallel.
6. Quota increment via Valkey (D13.7) — increment is *after* the
   S3 upload + DB row succeed so a failed upload does not consume
   the user's daily budget.  Valkey increment failure is swallowed
   (FR-052 is best-effort, the DB row is the source of truth).
7. SSE emission — ``image_ready`` on success (D13.8),
   ``image_failed`` on any exception (D13.9).
8. Return shape: ``{"image_url": url, "image_prompt": prompt}`` on
   success, ``{}`` on any failure (D13.10).

Design choices:

- **No try/except around the final SSE emission** — failures
  before the final emit are already covered by D13.9; the
  ``emit_sse`` helper itself never raises (see ``_sse.py``).
- **Best-effort cache increments** — Valkey failures in steps 3
  and 6 are logged but do not fail the node.  The DB row is the
  source of truth; the daily counter is a fast pre-flight check
  that is allowed to drift.
- **Resources are injected** via ``config["configurable"]``
  (``sse_queue``, ``openai_client``, ``s3_service``,
  ``valkey_service``) per DI.X2.  When the chat handler runs
  production requests it threads in the ``app.state`` singletons;
  tests inject mocks directly.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from datetime import date
from typing import Any, Optional, cast

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agent.nodes._sse import emit_sse
from app.agent.state import AgentState
from app.config import get_settings
from app.db.session import get_asyncpg_pool
from app.repositories.image_repo import ImageRepository
from app.schemas.chat import ImageFailedPayload, ImageReadyPayload

logger = structlog.get_logger(__name__)


_DALLE_MODEL = "dall-e-3"
_DALLE_SIZE = "1024x1024"
_DALLE_N = 1
_IMAGE_CONTENT_TYPE = "image/png"
_QUOTA_TTL_SECONDS = 86400


def _extract_last_human_id(messages: list[Any]) -> str | None:
    """Walk ``messages`` from the end and return the first HumanMessage id.

    The trend-scout branch and the human-driven chat branch both
    produce HumanMessages; the synthesised AIMessage comes from
    ``synthesize`` running in parallel.  We return the id of the
    last HumanMessage seen so the image row is attached to the
    correct turn in thread history (FR-051).

    Args:
        messages: ``state["messages"]`` — list of ``BaseMessage``.

    Returns:
        The ``HumanMessage.id`` string, or ``None`` when no
        HumanMessage is present (rare; e.g. an agent-only run).
    """
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return cast("str | None", getattr(msg, "id", None))
    return None


def _build_quota_key(user_id: str) -> str:
    """Return the daily image-quota key (D13.3)."""
    return f"image_quota:{user_id}:{date.today().isoformat()}"


def _decode_dalle_b64(payload: str) -> bytes:
    """Decode the ``b64_json`` DALL-E payload to raw PNG bytes.

    Centralised so a single try/except covers all decode failures
    in one place and the error message stays consistent in
    ``generate_image``'s logs.
    """
    return base64.b64decode(payload)


async def _emit_image_failed(
    sse_queue: asyncio.Queue | None,
    *,
    reason: str,
    thread_id: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Emit an ``image_failed`` SSE event and log a warning (D13.9)."""
    await emit_sse(sse_queue, "image_failed", ImageFailedPayload(reason=reason))
    extra: dict[str, Any] = {"thread_id": thread_id, "reason": reason}
    if context:
        extra.update(context)
    logger.warning("generate_image_failed", **extra)


async def generate_image(
    state: AgentState,
    config: Optional[RunnableConfig] = None,  # noqa: UP045 — LangGraph introspection requires ``Optional[X]``, not PEP 604 ``X | None``
) -> dict:
    """Generate an image and upload it to S3 (FR-040 to FR-053).

    Behaviour (per Phase 13, D13.1-D13.10):

    1. No-op when ``state["generate_image"] is not True`` (D13.1).
    2. No-op when ``state["image_prompt"]`` is missing (D13.2) —
       no fallback to ``first_user_message`` per user-confirmed
       decision.
    3. Check the Valkey daily quota (D13.3) — when the user has
       already hit the limit, emit ``image_failed
       {reason: "rate_limit_exceeded"}`` and return ``{}`` without
       touching DALL-E.
    4. Call DALL-E with ``response_format="b64_json"`` (D13.4).
       Any exception → ``image_failed {reason: "generation_failed"}``
       and return ``{}``.
    5. Upload the decoded bytes to S3 (D13.5).  Same error path.
    6. Insert a ``generated_images`` row linked to the last
       HumanMessage id (D13.6).  Same error path — S3 orphans are
       reclaimed by the Phase 10 cleanup job.
    7. Increment the Valkey quota (D13.7) — best-effort; failures
       are logged but do not roll back the DB row.
    8. Emit ``image_ready {url, prompt}`` (D13.8) and return
       ``{"image_url": url, "image_prompt": prompt}`` (D13.10).

    Args:
        state: Current agent state.  Read-only here — the only
            mutation is the returned ``image_url`` /
            ``image_prompt`` which the LangGraph reducer persists
            into the checkpoint.
        config: LangGraph runtime config;
            ``config["configurable"]`` carries the per-request
            ``sse_queue`` (Phase 14, DI.X1) plus the injected
            services (``openai_client``, ``s3_service``,
            ``valkey_service``) per DI.X2.  ``None`` is allowed
            (e.g. unit tests that exercise only the no-op paths).

    Returns:
        ``{}`` on no-op or failure.  ``{"image_url": <s3_url>,
        "image_prompt": <state["image_prompt"]>}`` on success.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="generate_image",
    )

    settings = get_settings()
    thread_id = state["thread_id"]

    configurable = (config.get("configurable", {}) if config else {}) or {}
    sse_queue = configurable.get("sse_queue") if isinstance(configurable, dict) else None
    openai_client = configurable.get("openai_client") if isinstance(configurable, dict) else None
    s3_service = configurable.get("s3_service") if isinstance(configurable, dict) else None
    valkey_service = configurable.get("valkey_service") if isinstance(configurable, dict) else None

    # Step 1: D13.1 — primary skip path.  Most turns are not image turns.
    if not state.get("generate_image"):
        logger.debug("Image generation not requested, skipping", thread_id=thread_id)
        return {}

    # Step 2: D13.2 — node only fires when the TrendScout subagent
    # produced a real image_prompt.  No fallback to
    # first_user_message per user-confirmed decision.
    image_prompt = state.get("image_prompt")
    if not image_prompt:
        logger.debug("No image_prompt, skipping", thread_id=thread_id)
        return {}

    user_id = state["user_id"]
    quota_key = _build_quota_key(user_id)

    # Step 3: D13.3 — daily quota pre-flight.  Skipped when the
    # chat handler did not inject a Valkey client (unit tests that
    # mock the LLM call boundary directly).
    if valkey_service is not None:
        try:
            count = await valkey_service.get_quota(quota_key)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "generate_image_quota_read_failed",
                thread_id=thread_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            count = 0
        if count >= settings.image_daily_limit:
            logger.warning(
                "generate_image_quota_exceeded",
                thread_id=thread_id,
                count=count,
                limit=settings.image_daily_limit,
            )
            await _emit_image_failed(
                sse_queue,
                reason="rate_limit_exceeded",
                thread_id=thread_id,
                context={"count": count, "limit": settings.image_daily_limit},
            )
            return {}

    # Step 4: D13.4 — DALL-E call.  ``response_format="b64_json"``
    # so we get the bytes inline and skip a second HTTP call to
    # fetch the public URL (which is valid for ~1h anyway).
    try:
        response = await openai_client.images.generate(  # type: ignore[union-attr]
            prompt=image_prompt,
            n=_DALLE_N,
            size=_DALLE_SIZE,
            model=_DALLE_MODEL,
            response_format="b64_json",
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "generate_image_dalle_call_failed",
            thread_id=thread_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _emit_image_failed(
            sse_queue,
            reason="generation_failed",
            thread_id=thread_id,
            context={"stage": "dalle"},
        )
        return {}

    # The DALL-E SDK returns a list of images.  We requested n=1 so
    # the first entry is always present.
    b64_payload = response.data[0].b64_json
    image_bytes = _decode_dalle_b64(cast(str, b64_payload))

    # Step 5: D13.5 — S3 upload.
    thread_uuid = uuid.UUID(thread_id) if isinstance(thread_id, str) else thread_id
    timestamp = int(time.time())
    s3_key = s3_service.build_key(  # type: ignore[union-attr]
        user_id=user_id,
        thread_id=thread_uuid,
        timestamp=timestamp,
    )
    try:
        s3_url = await s3_service.aupload_image(  # type: ignore[union-attr]
            user_id=user_id,
            thread_id=thread_uuid,
            timestamp=timestamp,
            image_bytes=image_bytes,
            content_type=_IMAGE_CONTENT_TYPE,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "generate_image_s3_upload_failed",
            thread_id=thread_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _emit_image_failed(
            sse_queue,
            reason="generation_failed",
            thread_id=thread_id,
            context={"stage": "s3_upload"},
        )
        return {}

    # Step 6: D13.6 — DB row insert.  The cleanup task in
    # Phase 10 sweeps orphaned S3 objects at thread_expiry_days,
    # so we do NOT delete the S3 object on DB failure.
    last_human_id = _extract_last_human_id(state["messages"])
    try:
        pool = get_asyncpg_pool()
        image_repo = ImageRepository(pool)
        await image_repo.create(
            thread_id=thread_uuid,
            user_id=user_id,
            prompt=image_prompt,
            s3_key=s3_key,
            s3_url=s3_url,
            model=_DALLE_MODEL,
            request_message_id=last_human_id,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "generate_image_db_insert_failed",
            thread_id=thread_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _emit_image_failed(
            sse_queue,
            reason="generation_failed",
            thread_id=thread_id,
            context={"stage": "db_insert"},
        )
        return {}

    # Step 7: D13.7 — quota increment.  Best-effort; the DB row
    # already exists and FR-052 explicitly allows the counter to
    # drift.  Incrementing *after* S3 + DB so a failed upload does
    # not consume the user's daily budget.
    if valkey_service is not None:
        try:
            await valkey_service.increment_quota(quota_key, ttl=_QUOTA_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "generate_image_quota_increment_failed",
                thread_id=thread_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # Step 8: D13.8 — terminal SSE event and D13.10 — return shape.
    await emit_sse(
        sse_queue,
        "image_ready",
        ImageReadyPayload(url=s3_url, prompt=image_prompt),
    )

    logger.info(
        "generate_image_completed",
        thread_id=thread_id,
        s3_key=s3_key,
        request_message_id=last_human_id,
    )

    return {"image_url": s3_url, "image_prompt": image_prompt}
