"""Service-layer error types.

Custom exceptions raised by service classes to express domain-specific
failure modes that callers can catch without parsing string messages.
"""

from __future__ import annotations


class BucketNotFoundError(RuntimeError):
    """Raised when the configured S3 bucket does not exist.

    Per the Infrastructure Ownership rule, this is raised by
    ``S3Service.ensure_bucket`` when ``head_bucket`` returns 404.
    The application must NOT create the bucket — Terraform owns it.
    """

    def __init__(self, bucket: str, reason: str) -> None:
        super().__init__(f"S3 bucket '{bucket}' not found: {reason}")
        self.bucket = bucket
        self.reason = reason
