"""Unit tests for app.auth.hmac_verifier."""

from __future__ import annotations

import hashlib
import hmac

from app.auth.hmac_verifier import verify_webhook_signature


def _make_signature(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_verify_webhook_signature_valid() -> None:
    payload = b'{"event": "order.created"}'
    secret = "my-secret"
    sig = _make_signature(payload, secret)

    assert verify_webhook_signature(payload, sig, secret) is True


def test_verify_webhook_signature_wrong_secret() -> None:
    payload = b'{"event": "order.created"}'
    sig = _make_signature(payload, "correct-secret")

    assert verify_webhook_signature(payload, sig, "wrong-secret") is False


def test_verify_webhook_signature_tampered_payload() -> None:
    secret = "my-secret"
    original = b'{"event": "order.created"}'
    sig = _make_signature(original, secret)
    tampered = b'{"event": "order.updated"}'

    assert verify_webhook_signature(tampered, sig, secret) is False


def test_verify_webhook_signature_empty_payload() -> None:
    secret = "my-secret"
    payload = b""
    sig = _make_signature(payload, secret)

    assert verify_webhook_signature(payload, sig, secret) is True
