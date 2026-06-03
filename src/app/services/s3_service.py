"""S3 service — upload generated images to AWS S3.

Used by the Celery image-generation task after the image bytes are
returned from the OpenAI Images API.
"""

from __future__ import annotations

import uuid

import boto3
import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)


class S3Service:
    """Wrapper around boto3 S3 client for image uploads.

    boto3's S3 client is thread-safe and can be shared across Celery
    tasks running in the same worker process.

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

    def upload_image(
        self,
        image_bytes: bytes,
        content_type: str = "image/png",
        prefix: str = "generated",
    ) -> str:
        """Upload image bytes to S3 and return the public URL.

        Args:
            image_bytes: Raw image data.
            content_type: MIME type of the image.
            prefix: S3 key prefix (folder path without trailing slash).

        Returns:
            Public HTTPS URL of the uploaded object.
        """
        key = f"{prefix}/{uuid.uuid4()}.png"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=image_bytes,
            ContentType=content_type,
        )
        url = f"https://{self._bucket}.s3.amazonaws.com/{key}"
        logger.info("Image uploaded to S3", key=key, bucket=self._bucket)
        return url
