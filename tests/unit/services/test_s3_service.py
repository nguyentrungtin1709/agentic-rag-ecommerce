"""Unit tests — S3Service methods and error paths.

All boto3 client calls are mocked; we never hit a real S3 endpoint in
unit tests.  The tests verify:

- ``build_key`` produces the FR-042 key pattern.
- ``aupload_image`` calls ``put_object`` with the right arguments and
  returns the expected public URL.
- ``delete`` calls ``delete_object`` with the correct bucket + key.
- ``ensure_bucket`` propagates ``BucketNotFoundError`` on 404 and
  leaves other ``ClientError``s untouched.
- ``close`` calls ``client.close()`` and does not block the event loop.
- The Infrastructure Ownership rule is enforced — ``create_bucket`` is
  never called.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.services.errors import BucketNotFoundError
from app.services.s3_service import S3Service


@pytest.fixture
def mock_settings() -> Settings:
    """Return a Settings instance with the S3 fields populated."""
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        openai_api_key="sk-test",
        saleor_webhook_secret="a" * 40,
        aws_s3_bucket="my-bucket",
        aws_access_key_id="AKIA-test",
        aws_secret_access_key="secret",
        aws_region="ap-southeast-1",
    )


@pytest.fixture
def s3_service(mock_settings: Settings) -> S3Service:
    """Return an ``S3Service`` with a mock boto3 client injected."""
    with patch("app.services.s3_service.boto3.client") as mock_boto_client:
        mock_boto_client.return_value = MagicMock(name="boto3.s3.client")
        service = S3Service(mock_settings)
        return service


# ---------------------------------------------------------------------------
# build_key
# ---------------------------------------------------------------------------


def test_build_key_returns_expected_pattern(s3_service: S3Service) -> None:
    """``build_key`` produces the FR-042 key with the default extension."""
    thread_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    key = s3_service.build_key("user-1", thread_id, 1_700_000_000)
    assert key == "images/user-1/11111111-2222-3333-4444-555555555555/1700000000.png"


def test_build_key_custom_extension(s3_service: S3Service) -> None:
    """A non-default extension replaces the trailing ``.png``."""
    thread_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    key = s3_service.build_key("u", thread_id, 1, extension="webp")
    assert key.endswith(".webp")
    assert ".png" not in key


# ---------------------------------------------------------------------------
# aupload_image
# ---------------------------------------------------------------------------


async def test_aupload_image_calls_put_object_with_correct_args(
    s3_service: S3Service,
) -> None:
    """``aupload_image`` must call ``put_object`` with the right kwargs."""
    thread_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    payload = b"fake-png-bytes"

    url = await s3_service.aupload_image("u1", thread_id, 123, payload)

    s3_service._client.put_object.assert_called_once_with(  # type: ignore[attr-defined]
        Bucket="my-bucket",
        Key="images/u1/11111111-2222-3333-4444-555555555555/123.png",
        Body=payload,
        ContentType="image/png",
    )
    assert url == (
        "https://my-bucket.s3.amazonaws.com/images/u1/11111111-2222-3333-4444-555555555555/123.png"
    )


async def test_aupload_image_uses_custom_content_type(
    s3_service: S3Service,
) -> None:
    """The ContentType kwarg is forwarded to ``put_object``."""
    thread_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    await s3_service.aupload_image("u1", thread_id, 1, b"data", content_type="image/jpeg")
    kwargs = s3_service._client.put_object.call_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["ContentType"] == "image/jpeg"


async def test_aupload_image_runs_in_to_thread(
    s3_service: S3Service,
) -> None:
    """The boto3 call must run via ``asyncio.to_thread`` to avoid blocking."""
    with patch("app.services.s3_service.asyncio.to_thread") as mock_to_thread:
        mock_to_thread.return_value = None
        thread_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        await s3_service.aupload_image("u", thread_id, 1, b"x")
        assert mock_to_thread.await_count == 1


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_calls_delete_object_with_correct_args(
    s3_service: S3Service,
) -> None:
    """``delete`` must call ``delete_object`` with bucket + key."""
    await s3_service.delete("images/u1/t/1.png")
    s3_service._client.delete_object.assert_called_once_with(  # type: ignore[attr-defined]
        Bucket="my-bucket",
        Key="images/u1/t/1.png",
    )


# ---------------------------------------------------------------------------
# ensure_bucket
# ---------------------------------------------------------------------------


async def test_ensure_bucket_succeeds_when_head_bucket_returns(
    s3_service: S3Service,
) -> None:
    """A successful ``head_bucket`` is silent — no exception raised."""
    s3_service._client.head_bucket.return_value = {}  # type: ignore[attr-defined]
    await s3_service.ensure_bucket()  # must not raise


async def test_ensure_bucket_raises_bucket_not_found_on_404(
    s3_service: S3Service,
) -> None:
    """A 404 from ``head_bucket`` becomes ``BucketNotFoundError``."""
    s3_service._client.head_bucket.side_effect = ClientError(  # type: ignore[attr-defined]
        {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {}},
        operation_name="HeadBucket",
    )
    with pytest.raises(BucketNotFoundError) as exc_info:
        await s3_service.ensure_bucket()
    assert exc_info.value.bucket == "my-bucket"


async def test_ensure_bucket_raises_bucket_not_found_on_nosuchbucket_code(
    s3_service: S3Service,
) -> None:
    """Some AWS regions return ``NoSuchBucket`` instead of ``404``."""
    s3_service._client.head_bucket.side_effect = ClientError(  # type: ignore[attr-defined]
        {"Error": {"Code": "NoSuchBucket"}, "ResponseMetadata": {}},
        operation_name="HeadBucket",
    )
    with pytest.raises(BucketNotFoundError):
        await s3_service.ensure_bucket()


async def test_ensure_bucket_propagates_other_client_errors(
    s3_service: S3Service,
) -> None:
    """Non-404 ``ClientError``s must propagate unchanged."""
    s3_service._client.head_bucket.side_effect = ClientError(  # type: ignore[attr-defined]
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {}},
        operation_name="HeadBucket",
    )
    with pytest.raises(ClientError) as exc_info:
        await s3_service.ensure_bucket()
    assert exc_info.value.response["Error"]["Code"] == "AccessDenied"


async def test_ensure_bucket_does_not_call_create_bucket(
    s3_service: S3Service,
) -> None:
    """The Infrastructure Ownership rule: never call ``create_bucket``."""
    s3_service._client.head_bucket.return_value = {}  # type: ignore[attr-defined]
    await s3_service.ensure_bucket()
    s3_service._client.create_bucket.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


async def test_close_releases_boto3_connections(
    s3_service: S3Service,
) -> None:
    """``close`` calls ``client.close()`` and runs it in a thread."""
    await s3_service.close()
    s3_service._client.close.assert_called_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_client_property_returns_boto3_client(s3_service: S3Service) -> None:
    """The ``.client`` property returns the underlying boto3 client."""
    assert s3_service.client is s3_service._client  # type: ignore[attr-defined]


def test_bucket_property_returns_configured_name(
    s3_service: S3Service,
) -> None:
    """The ``.bucket`` property returns the configured bucket name."""
    assert s3_service.bucket == "my-bucket"
