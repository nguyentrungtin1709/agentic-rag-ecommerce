"""Cache key builders for the thread list endpoint.

Why custom key builders:
    The default ``fastapi_cache.key_builder.default_key_builder`` hashes
    the function name, args, and kwargs. It does NOT consider the
    authenticated user — so users A and B would share the same cache
    entry for ``GET /api/v1/threads``, which is both a correctness
    bug and a privacy leak.

This module defines builders that incorporate the request's JWT
identity, query parameters, and route. Use them with
``@cache(namespace="threads", expire=..., key_builder=...)``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import Response


def _user_id_from_request(request: Request) -> str:
    """Return the authenticated user ID, or ``"anonymous"`` as a fallback.

    The thread-list endpoint always requires a JWT (Phase 8); the
    fallback is a defensive default in case the route is ever opened
    up. Returning a non-empty string avoids collapsing distinct users
    into a single cache key.
    """
    user = getattr(request.state, "current_user", None)
    if isinstance(user, dict):
        sub = user.get("sub")
        if sub:
            return str(sub)
    return "anonymous"


def thread_list_key_builder(
    func: Callable[..., Any],
    namespace: str = "",
    *,
    request: Request | None = None,
    response: Response | None = None,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> str:
    """Build a cache key for ``GET /api/v1/threads``.

    Key shape: ``{namespace}:threads:{user_id}:{before}:{limit}``

    Args:
        func: The route handler being cached. Unused, but required by
            the ``KeyBuilder`` protocol.
        namespace: Optional namespace prefix; the fastapi-cache2
            default key builder also prepends it. We rely on the
            caller's ``namespace=`` argument to keep this builder
            composable.
        request: The incoming FastAPI request — used to read the
            authenticated user and query parameters.
        response: Unused; required by the protocol.
        args: Unused; required by the protocol.
        kwargs: Unused; required by the protocol.

    Returns:
        A deterministic, user-scoped cache key string.

    Raises:
        RuntimeError: If ``request`` is ``None`` — the builder cannot
            build a meaningful key without it.
    """
    if request is None:
        raise RuntimeError(
            "thread_list_key_builder requires request; "
            "ensure the route is wrapped with @cache(..., key_builder=...)",
        )

    user_id = _user_id_from_request(request)
    before = request.query_params.get("before", "head")
    limit = request.query_params.get("limit", "20")

    prefix = f"{namespace}:" if namespace else ""
    return f"{prefix}threads:{user_id}:{before}:{limit}"
