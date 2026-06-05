"""User profile endpoint — stub implementation.

Full implementation is in Phase 6.  Requires ``is_staff: true`` in the
JWT claims (admin only, FR-032, FR-085).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.dependencies import AdminDep

router = APIRouter()


@router.get(
    "/{user_id}/profile",
    summary="Get customer style profile (admin only)",
)
async def get_user_profile(
    user_id: str,
    _admin: AdminDep,
) -> dict:
    """Return the long-term style profile for a given user (FR-032).

    Reads the profile from the LangGraph ``AsyncPostgresStore`` under
    namespace ``("profiles", user_id)``.  Requires ``is_staff: true``
    in the JWT claims (FR-085).

    Stub — Phase 6.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented — Phase 6.",
    )
