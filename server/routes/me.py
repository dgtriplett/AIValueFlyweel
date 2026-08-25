"""GET /api/me — trusted identity + role context for persona-aware UI.

This is the LINCHPIN other persona phases build on. It reports:
  * email: the platform-attributed identity (X-Forwarded-Email/User), or null when
    unauthenticated (local dev).
  * is_admin: whether the identity is on the GRID_ATLAS_ADMINS allowlist.
  * role: the resolved role ('admin'|'pm'|'executive') from the allowlist or
    app_users table.
  * is_exec_locked: whether the user is forced into 'executive' persona (true when
    role='executive' AND not is_admin; executives are locked, admins never are).

No auth gate needed: this just reports who you are. Returns email:null,
is_admin:false, role:'pm' for unauthenticated requests (must not 500).
"""
from fastapi import APIRouter, Request

from server.accounts import trusted_identity, is_admin, resolve_role

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
    admin = is_admin(request)
    role = await resolve_role(email, request)
    
    # is_exec_locked: role is 'executive' AND not an admin (admins are never locked)
    exec_locked = (role == 'executive' and not admin)
    
    return {
        "email": email,
        "is_admin": admin,
        "role": role,
        "is_exec_locked": exec_locked,
    }
