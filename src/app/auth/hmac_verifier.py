"""HMAC-SHA256 webhook signature verifier for Saleor webhooks.

Saleor signs webhook payloads with HMAC-SHA256 using the shared secret
configured in the dashboard.  The signature is sent in the
``Saleor-Signature`` header as a hex-encoded digest.

Uses ``hmac.compare_digest`` to prevent timing-oracle attacks.
"""

import hashlib
import hmac


def verify_webhook_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """Verify that a webhook payload was signed by Saleor.

    Args:
        payload: Raw request body bytes.
        signature_header: Value of the ``Saleor-Signature`` HTTP header,
            as a hex-encoded HMAC-SHA256 digest.
        secret: The shared webhook secret configured in Saleor dashboard.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)
