"""Rate limiting configuration.

Defines the process-wide ``Limiter`` instance used by slowapi to
enforce per-user rate limits (FR-091, FR-094). The limiter is keyed
on the JWT ``sub`` claim when a Bearer token is present, falling
back to the client IP address (slowapi default).

The Limiter is exposed via :func:`get_limiter` so route modules can
import the same instance for ``@limiter.limit(...)`` decorators
without creating duplicate limiters per process.
"""

from __future__ import annotations

from slowapi import Limiter

from app.auth.jwt_verifier import get_jwt_user_id_or_ip

# Limiter is constructed lazily so tests can clear and rebuild it
# after monkey-patching the storage URI.
_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """Return the process-wide ``Limiter`` singleton.

    The first call constructs the Limiter using the application
    settings (``valkey_rate_limit_url`` for storage). Tests should
    call :func:`_reset_limiter_for_tests` after monkey-patching the
    storage URI to force a fresh instance bound to the new URI.
    """
    global _limiter
    if _limiter is None:
        from app.config import get_settings

        settings = get_settings()
        _limiter = Limiter(
            key_func=get_jwt_user_id_or_ip,
            storage_uri=settings.valkey_rate_limit_url,
            headers_enabled=True,
        )
    return _limiter


def _reset_limiter_for_tests() -> None:
    """Reset the limiter singleton. Test fixtures only."""
    global _limiter
    _limiter = None
