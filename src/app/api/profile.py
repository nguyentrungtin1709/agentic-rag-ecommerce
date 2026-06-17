"""User profile endpoint — admin read access to the long-term style profile (Phase 9).

Returns the profile stored by the ``profiler`` LangGraph node under
namespace ``("profiles", user_id)``, key ``"profile"`` (FR-032).
Requires ``is_staff: true`` in the JWT claims (FR-085).

See ``history/9_0_0_PROFILE_AND_ADMIN_API.md`` for the design
decisions (D9.3 envelope, D9.6 404, D9.7 no-cache, D9.8 rate limit).
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Path, Request, status

from app.dependencies import AdminDep, StoreDep
from app.models.profile import UserProfile
from app.rate_limit import get_limiter
from app.schemas.profile import ProfileEnvelope, UserProfileResponse

logger = structlog.get_logger(__name__)

router = APIRouter()
_limiter = get_limiter()


@_limiter.limit("60/minute")
@router.get(
    "/{user_id}/profile",
    response_model=ProfileEnvelope,
    summary="Get customer style profile (admin only)",
)
async def get_user_profile(
    request: Request,  # noqa: ARG001  -- slowapi needs request in signature
    user_id: Annotated[str, Path(min_length=1, description="Saleor user ID")],
    _admin: AdminDep,
    store: StoreDep,
) -> ProfileEnvelope:
    """Return the long-term style profile for a given user (FR-032, D9.3).

    Reads ``("profiles", user_id)["profile"]`` from the
    ``AsyncPostgresStore``.  Returns 404 if the user has never had
    a profile written (i.e. has not chatted yet, or the profiler
    has not yet run on any of their threads) — see D9.6.

    Args:
        user_id: Saleor user ID from the URL path (non-empty).
        _admin: JWT-validated admin claim (enforces FR-085).
        store: LangGraph ``BaseStore`` instance from app state (D9.2).

    Returns:
        :class:`ProfileEnvelope` carrying the profile fields and
        the store-level ``updated_at`` timestamp.

    Raises:
        HTTPException: 404 if no profile exists for this user
            (D9.6), 500 if the stored payload fails Pydantic
            validation (corrupt JSON in the store).
    """
    namespace = ("profiles", user_id)
    item = await store.aget(namespace, "profile")

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for user {user_id}",
        )

    try:
        profile_model = UserProfile.model_validate(item.value)
    except Exception as exc:
        logger.error(
            "Profile payload failed validation",
            user_id=user_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored profile is corrupt.",
        ) from exc

    logger.info(
        "Profile read",
        admin_id=_admin.get("sub"),
        target_user_id=user_id,
    )

    return ProfileEnvelope(
        profile=UserProfileResponse(**profile_model.model_dump()),
        updated_at=item.updated_at,
    )
