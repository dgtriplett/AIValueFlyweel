"""GET /api/me — trusted identity + role context for persona-aware UI.

This is the LINCHPIN other persona phases build on. It reports:
  * email: the platform-attributed identity (X-Forwarded-Email/User), or null when
    unauthenticated (local dev).
  * is_admin: whether the identity is on the GRID_ATLAS_ADMINS allowlist.
  * is_exec_locked: HARDCODED false for Phase 1. Phase 2+ will add an
    admin-managed 'executive users' table and flip this to read from it. The field
    exists NOW so frontend can consume it immediately; the backend will wire the
    table query when the table arrives.

No auth gate needed: this just reports who you are. Returns email:null,
is_admin:false for unauthenticated requests (must not 500).
"""
from fastapi import APIRouter, Request

from server.accounts import trusted_identity, is_admin

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(request: Request):
    """Return the caller's trusted identity and role flags.

    This endpoint is unauthenticated by design — it reports who the platform says
    you are, including "nobody" (email: null). A 403 here would make it impossible
    to discover you're not authenticated, so the 'unauthorized' state is a valid
    response shape.
    """
    email = trusted_identity(request)
    # TODO(persona-phase-2+): replace `is_exec_locked: false` with a query against
    # the `executive_users` table (to be added by a later PR). The table will map
    # email -> bool, and this should return `email in executive_users`.
    return {
        "email": email,
        "is_admin": is_admin(request),
        "is_exec_locked": False,
    }
