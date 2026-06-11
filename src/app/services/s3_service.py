"""S3 service — upload generated images to AWS S3.

Used by the Celery image-generation task (Phase 13) and the chat SSE
endpoint (Phase 14) to publish DALL-E output as a public HTTPS URL.

boto3's S3 client is thread-safe and can be shared across Celery tasks
and FastAPI handlers in the same process.  All async helpers wrap the
sync boto3 calls in ``asyncio.to_thread`` so they do not block the
event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Final

import boto3
import structlog
from botocore.exceptions import ClientError

from app.config import Settings
from app.services.errors import BucketNotFoundError

logger = structlog.get_logger(__name__)

# FR-042 — S3 key pattern for generated images.
_KEY_PATTERN: Final[str] = "images/{user_id}/{thread_id}/{timestamp}.png"

# boto3 error codes that mean the bucket does not exist.  The exact
# code varies between regions / API versions, so we match several.
_BUCKET_MISSING_CODES: Final[frozenset[str]] = frozenset({"404", "NoSuchBucket", "NotFound"})


class S3Service:
    """Wrapper around boto3 S3 client for image uploads and bucket ops.

    boto3's S3 client is thread-safe and can be shared across Celery
    tasks and FastAPI handlers in the same process.  The same instance
    is stored on ``app.state.s3`` and consumed by Phase 13/14 code.

    Args:
        settings: Application settings providing AWS credentials and
            bucket name.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.aws_s3_bucket
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    @property
    def client(self):  # noqa: ANN201 — boto3.client return type is dynamic
        """Expose the raw boto3 client for advanced operations."""
        return self._client

    @property
    def bucket(self) -> str:
        """Configured bucket name."""
        return self._bucket

    def build_key(
        self,
        user_id: str,
        thread_id: uuid.UUID,
        timestamp: int,
        extension: str = "png",
    ) -> str:
        """Build the S3 object key per FR-042.

        Args:
            user_id: Saleor user ID.
            thread_id: Parent thread UUID.
            timestamp: Unix epoch seconds; used to make each key unique.
            extension: File extension without leading dot (default ``"png"``).

        Returns:
            ``images/{user_id}/{thread_id}/{timestamp}.{extension}``
        """
        return _KEY_PATTERN.format(
            user_id=user_id,
            thread_id=thread_id,
            timestamp=timestamp,
        ).replace(".png", f".{extension}")

    async def aupload_image(
        self,
        user_id: str,
        thread_id: uuid.UUID,
        timestamp: int,
        image_bytes: bytes,
        content_type: str = "image/png",
    ) -> str:
        """Upload image bytes to S3 asynchronously and return the public URL.

        boto3 is sync; the ``put_object`` call runs in a worker thread
        so the event loop is not blocked.

        Args:
            user_id: Saleor user ID.
            thread_id: Parent thread UUID.
            timestamp: Unix epoch seconds; used to make the key unique.
            image_bytes: Raw image data.
            content_type: MIME type of the image.

        Returns:
            Public HTTPS URL of the uploaded object.
        """
        key = self.build_key(user_id, thread_id, timestamp)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=image_bytes,
            ContentType=content_type,
        )
        logger.info("Image uploaded to S3", key=key, bucket=self._bucket)
        return f"https://{self._bucket}.s3.amazonaws.com/{key}"

    async def delete(self, key: str) -> None:
        """Delete an object from S3.

        Args:
            key: Full S3 object key.
        """
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        logger.info("S3 object deleted", key=key, bucket=self._bucket)

    async def ensure_bucket(self) -> None:
        """Verify the configured S3 bucket exists and is reachable.

        Per the Infrastructure Ownership rule, this is a ``head_bucket``
        check only.  The bucket itself is provisioned by Terraform; the
        application must NOT create it.  If the bucket is missing, this
        raises ``BucketNotFoundError`` and ``lifespan`` propagates the
        error so the pod fails readiness and the operator is notified.

        Raises:
            BucketNotFoundError: When the bucket does not exist (404).
            ClientError: For other AWS errors (propagated unchanged).
        """
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _BUCKET_MISSING_CODES:
                raise BucketNotFoundError(
                    bucket=self._bucket,
                    reason="S3 bucket does not exist — Terraform must provision it",
                ) from exc
            raise
        logger.info("S3 bucket verified", bucket=self._bucket)

    async def close(self) -> None:
        """Release boto3's underlying HTTP connections.

        boto3's ``client.close()`` is sync; we run it in a worker thread
        so the event loop is not blocked.
        """
        await asyncio.to_thread(self._client.close)
