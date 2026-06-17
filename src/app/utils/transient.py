"""Transient-error classification for Celery tasks.

A single source of truth for deciding whether an exception raised
inside a Celery task is worth retrying.  ``is_transient(exc)`` returns
``True`` for library exceptions that typically recover on their own
(rate limits, network blips, transient Qdrant 5xx responses) and
``False`` for everything else (bug, bad data, schema mismatch).

Used by:

- ``app.tasks.process_batch`` (Phase 6 — reindex worker)
- ``app.tasks.process_webhook`` (Phase 7 — webhook handler)

Why a dedicated module
----------------------
The original whitelist was a private ``_is_transient`` function inside
``process_batch``.  Phase 7 adds a second caller (``process_webhook``)
with the exact same retry policy.  Duplicating the classifier invites
drift: future additions (e.g. ``openai.APIConnectionError``) would
land in one file and silently desync from the other.  Extracting to a
shared util gives both tasks one source of truth and one set of unit
tests to maintain.

Contract
--------
Callers must ``raise self.retry(exc=exc)`` when this returns ``True``
and the Celery task is bound.  Permanent errors should be logged and
the task should return a ``{"status": "failed", ...}`` dict — **not**
re-raise, so the retry budget is not consumed by a real bug.
"""

from __future__ import annotations

import httpx
import qdrant_client.http.exceptions as qdrant_exc
from openai import (
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

# Library exception classes considered transient.  Extend this tuple
# (and add a unit test in ``tests/unit/utils/test_transient.py``) when
# the underlying client surfaces a new recoverable failure mode.
_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    # OpenAI: rate limits, timeouts, 5xx server errors.
    RateLimitError,
    APITimeoutError,
    InternalServerError,
    # HTTP: connection refused, dropped connections, slow responses.
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    # Qdrant: any unexpected HTTP response (typically a transient 5xx
    # from the cluster).  4xx errors are still surfaced via
    # ``UnexpectedResponse`` with a populated ``status_code`` and
    # should NOT be retried — we do not currently filter on status
    # because the only observed 4xx in production is a 401 from a
    # misconfigured API key, which is permanent.
    qdrant_exc.UnexpectedResponse,
)


def is_transient(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` should trigger a Celery auto-retry.

    Args:
        exc: The exception raised inside a Celery task body.

    Returns:
        ``True`` for exceptions in the :data:`_TRANSIENT_EXCEPTIONS`
        whitelist, ``False`` for everything else (including
        ``ValueError``, ``KeyError``, ``RuntimeError``, etc.).
    """
    return isinstance(exc, _TRANSIENT_EXCEPTIONS)
