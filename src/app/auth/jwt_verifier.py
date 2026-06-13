"""Saleor JWT verifier using RS256 + JWKS.

Fetches the public JWKS from Saleor's ``/.well-known/jwks.json`` endpoint
and caches the result with a configurable TTL.  Incoming tokens are
decoded and verified locally using PyJWT — no round-trip to Saleor per
request.

Typical usage inside a FastAPI dependency:

    from app.auth.jwt_verifier import verify_token

    async def current_user(token: str = Depends(bearer_token)) -> dict:
        return await verify_token(token)
"""

from __future__ import annotations

from typing import Any

import jwt
import structlog
from fastapi import Request
from slowapi.util import get_remote_address

logger = structlog.get_logger(__name__)

# Cache one PyJWKClient per Saleor base URL so JWKS is not re-fetched on
# every request.  PyJWKClient handles its own in-memory key caching.
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _get_jwks_client(saleor_url: str) -> jwt.PyJWKClient:
    """Return or create a cached PyJWKClient for the given Saleor instance.

    Args:
        saleor_url: Base URL of the Saleor instance.

    Returns:
        A ``jwt.PyJWKClient`` configured to fetch from the JWKS endpoint.
    """
    if saleor_url not in _jwks_clients:
        jwks_url = f"{saleor_url}/.well-known/jwks.json"
        _jwks_clients[saleor_url] = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=3600,
        )
        logger.info("PyJWKClient created", saleor_url=saleor_url)
    return _jwks_clients[saleor_url]


async def verify_token(
    token: str,
    saleor_url: str,
    issuer: str | None = None,
) -> dict[str, Any]:
    """Verify a Saleor-issued JWT and return its decoded claims.

    The JWKS is fetched lazily on first call and cached by PyJWKClient.
    Key rotation is handled automatically when a new ``kid`` is seen.

    Saleor compatibility shims applied after a successful decode:

    * ``user_id`` is mapped to ``sub`` when ``sub`` is missing.  Saleor
      issues tokens that carry the user ID in a ``user_id`` claim
      instead of the standard ``sub``; downstream code can read
      ``sub`` uniformly.
    * ``iss`` comparison accepts the issuer URL as set in the token
      (typically the user-facing ``SALEOR_URL`` plus ``/graphql/``)
      even when the application fetched the JWKS from a Docker-internal
      alias.  Pass the externally-visible ``iss`` value via
      ``issuer``; when ``None``, the JWKS URL is reused.

    Args:
        token: Raw JWT string (without ``Bearer `` prefix).
        saleor_url: Base URL used to fetch the JWKS document.
        issuer: Expected ``iss`` claim.  Defaults to ``saleor_url``.

    Returns:
        Decoded JWT payload as a dict.

    Raises:
        jwt.PyJWTError: On any verification failure (expired, invalid
            signature, wrong issuer, etc.).
    """
    jwks_client = _get_jwks_client(saleor_url)
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    payload: dict[str, Any] = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        options={"require": ["exp", "iat", "iss"]},
        issuer=issuer or saleor_url,
    )
    if "sub" not in payload and "user_id" in payload:
        payload["sub"] = payload["user_id"]
    return payload


def get_jwt_user_id_or_ip(request: Request) -> str:
    """Return the JWT ``sub`` claim if a Bearer token is present.

    Used as the slowapi ``key_func`` so each authenticated user has
    their own rate-limit bucket (FR-091). Falls back to the client
    IP address when no token is present or the token cannot be
    decoded — the route's own authentication dependency rejects
    invalid tokens with 401 before the rate limiter matters.

    Security note: the ``sub`` claim is read from an unverified
    decode. This is acceptable for rate-limit bucketing because (a)
    the rate limit is not a security boundary and (b) the route's
    real authentication dependency enforces the actual identity check
    with the full RS256 verification path.

    Args:
        request: Incoming FastAPI request.

    Returns:
        User ID from the JWT ``sub`` claim, or the remote address
        as a fallback.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return get_remote_address(request)
    token = auth.removeprefix("Bearer ").strip()
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        sub = unverified.get("sub")
        if sub:
            return str(sub)
    except Exception as exc:
        # Malformed token: fall back to IP. The route's auth
        # dependency will reject the request with 401.
        logger.debug("Rate-limit key: unverified JWT decode failed", error=str(exc))
    return get_remote_address(request)
