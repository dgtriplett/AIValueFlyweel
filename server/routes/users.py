"""User management — who is a PM / Executive / Admin (admin-portal/roles, Phase B).

    GET    /api/users            list every app_users row + which emails are env admins
    PUT    /api/users/{email}    upsert a user's role (admin|pm|executive)
    DELETE /api/users/{email}    remove a row (the user reverts to the 'pm' default)

WHY EVERY ENDPOINT IS ADMIN-GATED, AND FAILS CLOSED
---------------------------------------------------
Roles decide what the whole app lets a person do — an 'admin' row grants the admin
portal, an 'executive' row locks a user into a read-only persona. Handing that lever
to any authenticated user would let one repoint everyone else's access, so every
handler here calls `acct.require_admin(request, ...)` FIRST. That gate reads only the
platform-attributed identity (X-Forwarded-*, which the browser cannot forge) and
checks it against the GRID_ATLAS_ADMINS allowlist. With no allowlist configured
nobody is an admin and every endpoint 403s — the same fail-closed default the
account-management routes use.

THE ALLOWLIST OUTRANKS THE TABLE
--------------------------------
`resolve_role()` returns 'admin' for any allowlisted email regardless of its
app_users row (bootstrap, lockout-proof). So this router reports `is_env_admin` per
row/email: an allowlist admin's role select is not the app's source of truth for
their admin status, and the UI must not pretend it can revoke it by editing a row.
Deleting an allowlist admin's row can never lock them out for the same reason.

ROLES ARE GLOBAL, NOT PER-ACCOUNT
---------------------------------
app_users is keyed by email alone — a role is a property of a person on this
instance, not of a person-within-an-account. These endpoints are therefore
account/identity-agnostic in their data (no account scoping) but still require admin.

AUDIT
-----
Every mutation writes an audit_log row via write_audit(): entity_type='app_user',
action='role_grant' (PUT) or 'role_revoke' (DELETE), actor=the admin's email, and a
diff of {email, old_role, new_role}. That log is the only record of who changed whose
role, so it is the start of the 'full auditability' ask.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts as acct
from ..common import rows_to_list, write_audit
from ..db import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# The roles a user row may hold. Mirrors the app_users CHECK constraint and
# resolve_role's vocabulary. An admin ROW is a real thing (someone granted admin via
# the table rather than the env allowlist); the allowlist still outranks it.
ALLOWED_ROLES = ("admin", "pm", "executive")


class RoleIn(BaseModel):
    role: str


def _normalize_email(email: str) -> str:
    """Consistent with resolve_role: an email is matched case-insensitively."""
    return (email or "").strip().lower()


@router.get("")
async def list_users(request: Request):
    """Every app_users row, plus which emails are admins by the env allowlist.

    Admin-gated: the role table names who can administer the instance, and enumerating
    it is not something an ordinary user should be able to do.

    `is_env_admin` marks rows whose email is on GRID_ATLAS_ADMINS — those are admin
    regardless of their stored role, so the UI shows them as env-governed and does not
    offer to change them. Allowlisted emails with NO row are appended as synthetic
    admin entries so the operator sees the full picture of who is an admin.
    """
    acct.require_admin(request, "Listing users")

    rows = rows_to_list(await db.fetch("""
        SELECT email, role, granted_by, created_at, updated_at
        FROM app_users
        ORDER BY role, email
    """))

    env_admins = acct._admin_emails()
    seen = set()
    for row in rows:
        email = _normalize_email(row.get("email") or "")
        row["is_env_admin"] = email in env_admins
        seen.add(email)

    # Allowlist admins with no explicit row are still admins — surface them so the
    # operator can see everyone who holds admin, not only those with a table row.
    for email in sorted(env_admins - seen):
        rows.append({
            "email": email,
            "role": "admin",
            "granted_by": None,
            "created_at": None,
            "updated_at": None,
            "is_env_admin": True,
        })

    # Keep the ordering promise (role then email) even after appending env admins.
    rows.sort(key=lambda r: (r.get("role") or "", r.get("email") or ""))
    return {"users": rows}


@router.put("/{email}")
async def upsert_user(email: str, body: RoleIn, request: Request):
    """Grant or change a user's role. Upserts one app_users row.

    Admin-gated. Validates the role against ALLOWED_ROLES (422 otherwise) so a typo
    or a client bug cannot write a role the rest of the app does not understand.
    `granted_by` is the CURRENT admin's trusted identity, not anything the client
    sends — the audit trail must attribute the grant to who actually made it.
    """
    actor = acct.require_admin(request, "Assigning a user role")
    normalized = _normalize_email(email)
    if not normalized:
        raise HTTPException(422, "An email is required to assign a role.")

    role = (body.role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            422,
            f"Role must be one of {', '.join(ALLOWED_ROLES)}; got {body.role!r}.")

    existing = await db.fetchrow(
        "SELECT role FROM app_users WHERE email = $1", normalized)
    old_role = existing["role"] if existing else None

    await db.execute("""
        INSERT INTO app_users (email, role, granted_by, created_at, updated_at)
        VALUES ($1, $2, $3, now(), now())
        ON CONFLICT (email) DO UPDATE
            SET role = EXCLUDED.role,
                granted_by = EXCLUDED.granted_by,
                updated_at = now()
    """, normalized, role, actor)

    await write_audit("app_user", normalized, "role_grant", actor,
                      {"email": normalized, "old_role": old_role, "new_role": role})

    row = await db.fetchrow(
        "SELECT email, role, granted_by, created_at, updated_at "
        "FROM app_users WHERE email = $1", normalized)
    result = dict(row) if row else {"email": normalized, "role": role}
    result["is_env_admin"] = normalized in acct._admin_emails()
    return result


@router.delete("/{email}")
async def remove_user(email: str, request: Request):
    """Remove a user's role row. The user reverts to the 'pm' default.

    Admin-gated. There is a self-demotion guard message, but it is advisory: an
    allowlist admin CANNOT lock themselves out by deleting a row, because
    GRID_ATLAS_ADMINS grants admin regardless of any row (resolve_role checks the
    allowlist first). So we refuse only the case that could actually strip an admin's
    access — removing your OWN admin row when you are NOT an allowlist admin — and
    otherwise proceed.
    """
    actor = acct.require_admin(request, "Removing a user role")
    normalized = _normalize_email(email)
    if not normalized:
        raise HTTPException(422, "An email is required to remove a role.")

    existing = await db.fetchrow(
        "SELECT role FROM app_users WHERE email = $1", normalized)
    if existing is None:
        # Nothing to remove; the user is already on the 'pm' default. Idempotent.
        return {"deleted": False, "email": normalized,
                "note": "No stored role for this user; they already default to 'pm'."}

    old_role = existing["role"]
    env_admins = acct._admin_emails()

    # The only genuine lock-out: deleting your OWN admin row when the allowlist would
    # NOT keep you an admin. An allowlist admin is safe (the allowlist outranks the
    # row), so this guard fires only for a table-granted admin removing themselves.
    if (normalized == _normalize_email(actor or "")
            and old_role == "admin"
            and normalized not in env_admins):
        raise HTTPException(
            422,
            "Refusing to remove your own admin role: you are an admin via this table, "
            "not the GRID_ATLAS_ADMINS allowlist, so deleting this row would lock you "
            "out of the admin portal. Grant admin to another user first, or add your "
            "email to GRID_ATLAS_ADMINS.")

    await db.execute("DELETE FROM app_users WHERE email = $1", normalized)
    await write_audit("app_user", normalized, "role_revoke", actor,
                      {"email": normalized, "old_role": old_role, "new_role": None})

    return {"deleted": True, "email": normalized,
            "note": "Role removed; the user reverts to the 'pm' default."
                    + (" They remain an admin via the GRID_ATLAS_ADMINS allowlist."
                       if normalized in env_admins else "")}
