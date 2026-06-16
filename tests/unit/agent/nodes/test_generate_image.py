"""Unit tests for app.agent.nodes.generate_image.

Covers Phase 13, D13.1-D13.10:

* No-op when ``generate_image`` is False.
* No-op when ``image_prompt`` is missing.
* Quota exceeded → ``image_failed {reason: rate_limit_exceeded}``.
* DALL-E success → S3 upload, DB row, Valkey increment,
  ``image_ready`` SSE, return dict.
* DALL-E failure → ``image_failed {reason: generation_failed}``.
* S3 upload failure → same ``image_failed`` payload, no DB insert.
* ``request_message_id`` is the id of the last HumanMessage.
* Valkey increment failure is swallowed; the DB row is the source
  of truth and the return dict is still populated.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.state import AgentState

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


# ── Helpers ───────────────────────────────────────────────────────────────────


_THREAD_UUID = uuid.uuid4()
_THREAD_UUID_STR = str(_THREAD_UUID)
_USER_ID = "user-123"
_CORR_ID = "test-corr-id"
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-payload-bytes"
_FAKE_B64 = base64.b64encode(_FAKE_PNG).decode("ascii")
_FAKE_S3_URL = "https://my-bucket.s3.amazonaws.com/images/u/t/123.png"


def _make_state(
    *,
    messages: list | None = None,
    image_prompt: str | None = "A minimalist cotton tee with bold typography",
    generate_image: bool = True,
    **kwargs: Any,
) -> AgentState:
    """Build a minimal AgentState dict for generate_image tests."""
    base: dict = {
        "messages": messages if messages is not None else [],
        "correlation_id": _CORR_ID,
        "user_id": _USER_ID,
        "thread_id": _THREAD_UUID_STR,
        "summary": "",
        "generate_image": generate_image,
        "first_user_message": None,
        "user_profile": None,
        "retrieved_products": [],
        "trend_summary": None,
        "thread_title": None,
        "intent": None,
        "title_generated": False,
        "fallback_count": 0,
        "image_url": None,
        "image_prompt": image_prompt,
    }
    base.update(kwargs)
    return cast("AgentState", base)


def _make_config(
    *,
    sse_queue: asyncio.Queue | None = None,
    openai_client: Any = None,
    s3_service: Any = None,
    valkey_service: Any = None,
) -> RunnableConfig:
    """Build a RunnableConfig with all four injected services.

    The ``sse_queue`` defaults to a real ``asyncio.Queue`` so the
    tests that need emission order assertions can ``queue.join()``
    or drain the queue.  Tests that only care about the return
    shape pass an explicit ``MagicMock(spec=asyncio.Queue)``.
    """
    return cast(
        "RunnableConfig",
        {
            "configurable": {
                "sse_queue": sse_queue if sse_queue is not None else asyncio.Queue(),
                "openai_client": openai_client,
                "s3_service": s3_service,
                "valkey_service": valkey_service,
            }
        },
    )


def _drain(queue: asyncio.Queue) -> list[dict[str, Any]]:
    """Drain a queue into a list of ``{type, payload}`` dicts."""
    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    """Inject minimal env vars and clear the settings cache around each test."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SALEOR_WEBHOOK_SECRET", "test-secret-32-chars-minimum-abc")
    yield
    get_settings.cache_clear()


# ── Test 1: no-op when generate_image is False ───────────────────────────────


async def test_generate_image_is_noop_when_generate_image_false() -> None:
    """``state["generate_image"] = False`` short-circuits to ``{}`` with no I/O."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state(generate_image=False)
    config = _make_config()

    with patch("app.agent.nodes.generate_image.get_asyncpg_pool") as mock_pool:
        result = await generate_image(state, config)

    assert result == {}
    mock_pool.assert_not_called()


# ── Test 2: no-op when image_prompt is missing ───────────────────────────────


async def test_generate_image_is_noop_when_image_prompt_none() -> None:
    """``state["image_prompt"] = None`` short-circuits to ``{}`` (D13.2)."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state(image_prompt=None)
    config = _make_config()

    with patch("app.agent.nodes.generate_image.get_asyncpg_pool") as mock_pool:
        result = await generate_image(state, config)

    assert result == {}
    mock_pool.assert_not_called()


# ── Test 3: quota exceeded → image_failed ───────────────────────────────────


async def test_generate_image_quota_exceeded_emits_image_failed() -> None:
    """``valkey.get_quota`` returns the daily limit → ``image_failed`` SSE, no DALL-E."""
    from datetime import date as _date

    from app.agent.nodes.generate_image import generate_image

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()
    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=10)  # image_daily_limit default = 10
    config = _make_config(sse_queue=queue, valkey_service=valkey)

    with patch("app.agent.nodes.generate_image.get_asyncpg_pool") as mock_pool:
        result = await generate_image(state, config)

    assert result == {}
    expected_key = f"image_quota:{_USER_ID}:{_date.today().isoformat()}"
    valkey.get_quota.assert_awaited_once_with(expected_key)
    # No DALL-E call, no S3, no DB.
    mock_pool.assert_not_called()

    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "image_failed"
    assert events[0]["payload"]["reason"] == "rate_limit_exceeded"


# ── Test 4: DALL-E success → S3 + DB + Valkey + SSE + return dict ───────────


async def test_generate_image_dalle_success_uploads_to_s3_and_inserts_row() -> None:
    """Full happy path: DALL-E → S3 → DB → Valkey → SSE → return dict."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()

    openai_client = MagicMock()
    fake_dalle_response = MagicMock()
    fake_dalle_response.data = [MagicMock(b64_json=_FAKE_B64)]
    openai_client.images.generate = AsyncMock(return_value=fake_dalle_response)

    s3 = MagicMock()
    s3.build_key = MagicMock(return_value=f"images/{_USER_ID}/{_THREAD_UUID_STR}/1700000000.png")
    s3.aupload_image = AsyncMock(return_value=_FAKE_S3_URL)

    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=0)
    valkey.increment_quota = AsyncMock(return_value=1)

    image_repo = MagicMock()
    image_repo.create = AsyncMock(return_value=None)

    config = _make_config(
        sse_queue=queue,
        openai_client=openai_client,
        s3_service=s3,
        valkey_service=valkey,
    )

    with (
        patch(
            "app.agent.nodes.generate_image.ImageRepository",
            return_value=image_repo,
        ) as mock_repo_cls,
        patch(
            "app.agent.nodes.generate_image.get_asyncpg_pool",
            return_value=MagicMock(),
        ),
    ):
        result = await generate_image(state, config)

    # DALL-E called with the right args.
    openai_client.images.generate.assert_awaited_once()
    dalle_kwargs = cast(Any, openai_client.images.generate.await_args).kwargs
    assert dalle_kwargs["prompt"] == state["image_prompt"]
    assert dalle_kwargs["model"] == "dall-e-3"
    assert dalle_kwargs["size"] == "1024x1024"
    assert dalle_kwargs["response_format"] == "b64_json"

    # S3 key built and upload happened.
    s3.build_key.assert_called_once()
    s3.aupload_image.assert_awaited_once()
    upload_kwargs = s3.aupload_image.await_args.kwargs
    assert upload_kwargs["image_bytes"] == _FAKE_PNG
    assert upload_kwargs["content_type"] == "image/png"

    # DB row inserted with the right fields.
    image_repo.create.assert_awaited_once()
    create_kwargs = image_repo.create.await_args.kwargs
    assert create_kwargs["thread_id"] == _THREAD_UUID
    assert create_kwargs["user_id"] == _USER_ID
    assert create_kwargs["prompt"] == state["image_prompt"]
    assert create_kwargs["s3_url"] == _FAKE_S3_URL
    assert create_kwargs["model"] == "dall-e-3"

    # Quota increment AFTER S3+DB.
    valkey.increment_quota.assert_awaited_once()
    inc_kwargs = valkey.increment_quota.await_args.kwargs
    assert inc_kwargs["ttl"] == 86400
    valkey.get_quota.assert_awaited_once()

    # One ``image_ready`` event on the queue.
    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "image_ready"
    assert events[0]["payload"]["url"] == _FAKE_S3_URL
    assert events[0]["payload"]["prompt"] == state["image_prompt"]

    # Return dict carries the URL and the prompt.
    assert result["image_url"] == _FAKE_S3_URL
    assert result["image_prompt"] == state["image_prompt"]

    # ImageRepository was constructed with the pool.
    mock_repo_cls.assert_called_once()


# ── Test 5: DALL-E failure → image_failed, no S3, no DB ──────────────────────


async def test_generate_image_dalle_failure_emits_image_failed() -> None:
    """``openai_client.images.generate`` raises → ``image_failed``."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()

    openai_client = MagicMock()
    openai_client.images.generate = AsyncMock(
        side_effect=RuntimeError("simulated openai outage"),
    )

    s3 = MagicMock()
    s3.aupload_image = AsyncMock()

    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=0)

    image_repo = MagicMock()
    image_repo.create = AsyncMock()

    config = _make_config(
        sse_queue=queue,
        openai_client=openai_client,
        s3_service=s3,
        valkey_service=valkey,
    )

    with (
        patch(
            "app.agent.nodes.generate_image.ImageRepository",
            return_value=image_repo,
        ),
        patch("app.agent.nodes.generate_image.get_asyncpg_pool", return_value=MagicMock()),
    ):
        result = await generate_image(state, config)

    assert result == {}
    s3.aupload_image.assert_not_called()
    image_repo.create.assert_not_called()
    valkey.increment_quota.assert_not_called()

    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "image_failed"
    assert events[0]["payload"]["reason"] == "generation_failed"


# ── Test 6: S3 upload failure → image_failed, no DB ─────────────────────────


async def test_generate_image_s3_upload_failure_emits_image_failed() -> None:
    """``s3.aupload_image`` raises → ``image_failed``."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()

    openai_client = MagicMock()
    fake_dalle_response = MagicMock()
    fake_dalle_response.data = [MagicMock(b64_json=_FAKE_B64)]
    openai_client.images.generate = AsyncMock(return_value=fake_dalle_response)

    s3 = MagicMock()
    s3.build_key = MagicMock(return_value="images/k.png")
    s3.aupload_image = AsyncMock(side_effect=RuntimeError("simulated s3 outage"))

    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=0)

    image_repo = MagicMock()
    image_repo.create = AsyncMock()

    config = _make_config(
        sse_queue=queue,
        openai_client=openai_client,
        s3_service=s3,
        valkey_service=valkey,
    )

    with (
        patch(
            "app.agent.nodes.generate_image.ImageRepository",
            return_value=image_repo,
        ),
        patch("app.agent.nodes.generate_image.get_asyncpg_pool", return_value=MagicMock()),
    ):
        result = await generate_image(state, config)

    assert result == {}
    image_repo.create.assert_not_called()
    valkey.increment_quota.assert_not_called()

    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "image_failed"
    assert events[0]["payload"]["reason"] == "generation_failed"


# ── Test 7: request_message_id is the last HumanMessage id ──────────────────


async def test_generate_image_request_message_id_is_last_human_message() -> None:
    """``request_message_id`` is the id of the most recent HumanMessage."""
    from app.agent.nodes.generate_image import generate_image

    messages = [
        AIMessage(content="earlier response"),
        HumanMessage(content="first turn", id="h1"),
        AIMessage(content="reply to first turn"),
        HumanMessage(content="second turn", id="h2"),
        AIMessage(content="reply to second turn"),
    ]
    state = _make_state(messages=messages)
    queue: asyncio.Queue = asyncio.Queue()

    openai_client = MagicMock()
    fake_dalle_response = MagicMock()
    fake_dalle_response.data = [MagicMock(b64_json=_FAKE_B64)]
    openai_client.images.generate = AsyncMock(return_value=fake_dalle_response)

    s3 = MagicMock()
    s3.build_key = MagicMock(return_value="images/k.png")
    s3.aupload_image = AsyncMock(return_value=_FAKE_S3_URL)

    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=0)
    valkey.increment_quota = AsyncMock(return_value=1)

    image_repo = MagicMock()
    image_repo.create = AsyncMock(return_value=None)

    config = _make_config(
        sse_queue=queue,
        openai_client=openai_client,
        s3_service=s3,
        valkey_service=valkey,
    )

    with (
        patch(
            "app.agent.nodes.generate_image.ImageRepository",
            return_value=image_repo,
        ),
        patch("app.agent.nodes.generate_image.get_asyncpg_pool", return_value=MagicMock()),
    ):
        await generate_image(state, config)

    image_repo.create.assert_awaited_once()
    create_kwargs = image_repo.create.await_args.kwargs
    assert create_kwargs["request_message_id"] == "h2"


# ── Test 8: Valkey increment failure is swallowed ──────────────────────────


async def test_generate_image_quota_failure_does_not_break_flow() -> None:
    """A failing ``increment_quota`` does NOT roll back the DB row or return dict."""
    from app.agent.nodes.generate_image import generate_image

    state = _make_state()
    queue: asyncio.Queue = asyncio.Queue()

    openai_client = MagicMock()
    fake_dalle_response = MagicMock()
    fake_dalle_response.data = [MagicMock(b64_json=_FAKE_B64)]
    openai_client.images.generate = AsyncMock(return_value=fake_dalle_response)

    s3 = MagicMock()
    s3.build_key = MagicMock(return_value="images/k.png")
    s3.aupload_image = AsyncMock(return_value=_FAKE_S3_URL)

    valkey = MagicMock()
    valkey.get_quota = AsyncMock(return_value=0)
    valkey.increment_quota = AsyncMock(side_effect=RuntimeError("simulated redis down"))

    image_repo = MagicMock()
    image_repo.create = AsyncMock(return_value=None)

    config = _make_config(
        sse_queue=queue,
        openai_client=openai_client,
        s3_service=s3,
        valkey_service=valkey,
    )

    with (
        patch(
            "app.agent.nodes.generate_image.ImageRepository",
            return_value=image_repo,
        ),
        patch("app.agent.nodes.generate_image.get_asyncpg_pool", return_value=MagicMock()),
    ):
        result = await generate_image(state, config)

    # DB row still inserted.
    image_repo.create.assert_awaited_once()
    # Return dict is fully populated — DB row is the source of truth.
    assert result["image_url"] == _FAKE_S3_URL
    assert result["image_prompt"] == state["image_prompt"]
    # The terminal SSE event still fires.
    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "image_ready"
    assert events[0]["payload"]["url"] == _FAKE_S3_URL
