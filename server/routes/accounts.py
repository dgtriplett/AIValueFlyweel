"""Accounts — which customer this instance is showing.

    GET    /api/accounts            list, with the current one marked
    POST   /api/accounts            create
    PATCH  /api/accounts/{id}       rename, retype, activate, make default
    DELETE /api/accounts/{id}       archive (or ?hard=true to delete)
    GET    /api/accounts/current    the resolved account for this request

WHY DELETE ARCHIVES BY DEFAULT
------------------------------
An account owns a customer's calibrated assumptions, their research, their proposals,
and their source positions — months of someone's work, and the FK cascade would take
all of it. Archiving removes it from the switcher and leaves the record; `?hard=true`
is the explicit escape hatch and says what it will destroy.

WHY THERE IS NO "ALL ACCOUNTS" VIEW
-----------------------------------
A portfolio total summed across tenants is not a number anyone wants: it adds two
utilities' unrelated capital plans. Every read is scoped to exactly one account, and
comparing them is a separate, deliberate feature rather than a default that silently
produces a meaningless figure.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import accounts as acct
from ..common import current_user, rows_to_list, write_audit
from ..db import db
from ..limits import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SEED_DATA = Path(__file__).resolve().parents[2] / "scripts" / "seed_data.json"


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")[:60] or "account"


async def _seed_assumptions_for_account(account_id: int, actor: str) -> int:
    """Give a new account its own baseline assumptions from the shipped seed.

    A new customer's assumptions must not inherit whichever utility happened to be
    the default account, because that account may already be research-calibrated.
    The seed JSON is the product baseline and is deployed with the app.
    """
    try:
        with _SEED_DATA.open("r", encoding="utf-8") as handle:
            assumptions = json.load(handle).get("value_assumptions") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not initialize seeded assumptions for account %s (%s)",
                       account_id, type(exc).__name__)
        return 0

    inserted = 0
    for item in assumptions:
        row = await db.fetchrow("""
            INSERT INTO value_assumptions
                (account_id, key, label, value, unit, category, source, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,'seed',now())
            ON CONFLICT (account_id, key) DO NOTHING
            RETURNING id
        """, account_id, item.get("key"), item.get("label"), item.get("value"),
            item.get("unit"), item.get("category"))
        if row:
            inserted += 1
    await write_audit("account", account_id, "seed_assumptions", actor,
                      {"count": inserted})
    return inserted


class AccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    utility_type: str | None = Field(default=None, max_length=60)
    slug: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    utility_type: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    make_default: bool = False


@router.get("")
async def list_accounts(request: Request, include_inactive: bool = False):
    """The accounts this caller may switch between, with counts for the switcher.

    `include_inactive` enumerates ARCHIVED accounts too, which is a broader view of
    who the operator's customers are than the switcher needs, so it is admin-gated.
    The active list is what the switcher renders and stays available to any
    authenticated user, per the one-instance-per-customer model.
    """
    await acct.authorize_account_read(request, 0)
    if include_inactive:
        acct.require_admin(request, "Listing archived accounts")
    where = "" if include_inactive else "WHERE a.is_active"
    try:
        rows = rows_to_list(await db.fetch(f"""
            SELECT a.id, a.slug, a.name, a.utility_type, a.is_active, a.is_default,
                   a.notes, a.created_at,
                   (SELECT count(*) FROM account_asset_status s
                     WHERE s.account_id = a.id
                       AND s.ingestion_status IN ('curated','governed')) AS sources_ready,
                   (SELECT count(*) FROM account_asset_status s
                     WHERE s.account_id = a.id) AS sources_tracked,
                   EXISTS (SELECT 1 FROM company_profile c
                            WHERE c.account_id = a.id) AS researched,
                   (SELECT count(*) FROM kb_articles k
                     WHERE k.account_id = a.id AND k.status <> 'archived')
                                                        AS kb_articles
            FROM accounts a {where} ORDER BY a.is_default DESC, lower(a.name)
        """))
    except Exception as exc:  # noqa: BLE001
        # ONLY an absent table earns the friendly "run the migration" answer. This
        # used to catch everything, so a real outage was reported as a pre-migration
        # install: the setup screen told the operator to run migration 009 — which was
        # already applied — while the actual cause was that Lakebase was unreachable.
        # Wrong diagnosis on the one endpoint someone checks first.
        if acct.is_missing_relation(exc):
            logger.info("accounts table absent (%s) — reporting pre-migration",
                        type(exc).__name__)
            return {"accounts": [], "current_account_id": None,
                    "note": "Run migration 009 to enable accounts."}
        logger.error("accounts unavailable (%s: %s)", type(exc).__name__, exc)
        raise HTTPException(
            503, "The accounts table could not be read, so the account list is "
                 f"unavailable. This is not a missing migration: {type(exc).__name__}"
                 f": {exc}") from exc
    return {"accounts": rows, "current_account_id": await acct.current()}


@router.get("/current")
async def current_account(request: Request):
    """The account this request resolved to, and how."""
    account_id = await acct.current()
    if account_id is not None:
        await acct.authorize_account_read(request, account_id)
    if account_id is None:
        return {"account": None,
                "note": "No accounts table — run scripts/migrate.py."}
    row = await db.fetchrow(
        "SELECT id, slug, name, utility_type, is_default FROM accounts "
        "WHERE id = $1", account_id)
    return {"account": dict(row) if row else None}


@router.post("", dependencies=[Depends(limiter("write"))])
async def create_account(body: AccountIn, request: Request):
    """Add an account. It starts with the shared reference library and no answers.

    Deliberately NOT copied from an existing account: a new customer's assumptions
    should start at the seeded defaults, not at another utility's calibrated figures.
    Inheriting them would be the kind of error that produces a confident wrong number
    with no visible cause.

    Admin-gated: creating an account onboards a new customer onto this instance and
    seeds their assumption set. It is an operator action, not a user one.
    """
    acct.require_admin(request, "Creating an account")
    actor = current_user(request)
    slug = slugify(body.slug or body.name)
    existing = await db.fetchrow("SELECT id FROM accounts WHERE slug = $1", slug)
    if existing is not None:
        raise HTTPException(
            409, f"An account with the slug {slug!r} already exists. Give a "
                 "different name, or pass an explicit slug.")

    # is_default only when this is the first account, so creating one never silently
    # switches what an existing unscoped request resolves to.
    row = await db.fetchrow("""
        INSERT INTO accounts (slug, name, utility_type, notes, created_by,
                              is_default)
        VALUES ($1,$2,$3,$4,$5, NOT EXISTS (SELECT 1 FROM accounts))
        RETURNING id, slug, name, utility_type, is_default
    """, slug, body.name.strip(), body.utility_type, body.notes, actor)
    if row is None:
        raise HTTPException(503, "Database unavailable")

    acct.invalidate_default()
    seeded_assumptions = await _seed_assumptions_for_account(row["id"], actor)
    await write_audit("account", row["id"], "create", actor,
                      {"slug": slug, "name": body.name})
    return {**dict(row), "note": "Starts on the shared reference library with the "
                                 "seeded assumptions. Run Company research to "
                                 "calibrate them for this utility.",
            "seeded_assumptions": seeded_assumptions}


@router.patch("/{account_id}", dependencies=[Depends(limiter("write"))])
async def update_account(account_id: int, body: AccountPatch, request: Request):
    """Rename, retype, activate/archive, or make default.

    Admin-gated: every field here changes what other users of the instance see.
    Archiving removes an account from everyone's switcher, and `make_default`
    changes which account UNSCOPED requests resolve to — so an unauthorized caller
    could silently repoint the whole instance at a different customer's data.
    """
    acct.require_admin(request, "Modifying an account")
    actor = current_user(request)
    row = await db.fetchrow("SELECT id, is_default FROM accounts WHERE id = $1",
                            account_id)
    if row is None:
        raise HTTPException(404, "Account not found")

    if body.is_active is False and row["is_default"]:
        raise HTTPException(
            422, "This is the default account, so deactivating it would leave "
                 "unscoped requests with nowhere to resolve. Make another account "
                 "the default first.")

    if body.make_default:
        # Two statements, and the clear MUST come first: the partial unique index
        # permits only one row with is_default = true, so setting the new one before
        # clearing the old would violate it.
        #
        # ONE TRANSACTION, because the window between them is a broken state. Run as
        # separate execute() calls each took its own connection, so a failure or a
        # restart after the clear left the table with NO default — and then every
        # unscoped request resolves to whatever `ORDER BY id LIMIT 1` returns, which
        # is a different customer's account. Atomic means the instance is never
        # pointed at the wrong tenant, even briefly.
        async with db.transaction() as conn:
            if conn is None:
                raise HTTPException(503, "Database unavailable")
            await conn.execute(
                "UPDATE accounts SET is_default = false WHERE is_default")
            await conn.execute(
                "UPDATE accounts SET is_default = true, updated_at = now() "
                "WHERE id = $1", account_id)
        acct.invalidate_default()

    await db.execute("""
        UPDATE accounts SET
            name = COALESCE($2, name),
            utility_type = COALESCE($3, utility_type),
            notes = COALESCE($4, notes),
            is_active = COALESCE($5, is_active),
            updated_at = now()
        WHERE id = $1
    """, account_id, body.name.strip() if body.name else None,
        body.utility_type, body.notes, body.is_active)

    await write_audit("account", account_id, "update", actor, body.model_dump())
    updated = await db.fetchrow(
        "SELECT id, slug, name, utility_type, is_active, is_default FROM accounts "
        "WHERE id = $1", account_id)
    return dict(updated)


@router.delete("/{account_id}", dependencies=[Depends(limiter("write"))])
async def delete_account(account_id: int, request: Request, hard: bool = False):
    """Archive by default. `?hard=true` deletes the account and everything it owns.

    Admin-gated on BOTH paths. `?hard=true` cascades away months of a customer's
    calibrated assumptions, research and proposals and is irreversible, and archiving
    still removes the account from every user's switcher. Neither is something an
    ordinary authenticated user of the instance should be able to do with one request.
    """
    # Named distinctly so the refusal log and the 403 say which one was attempted.
    acct.require_admin(
        request,
        "Permanently deleting an account" if hard else "Archiving an account")
    actor = current_user(request)
    row = await db.fetchrow(
        "SELECT id, name, is_default FROM accounts WHERE id = $1", account_id)
    if row is None:
        raise HTTPException(404, "Account not found")
    if row["is_default"]:
        raise HTTPException(
            422, "This is the default account. Make another account the default "
                 "before removing it.")

    if not hard:
        await db.execute(
            "UPDATE accounts SET is_active = false, updated_at = now() "
            "WHERE id = $1", account_id)
        await write_audit("account", account_id, "archive", actor,
                          {"name": row["name"]})
        return {"archived": True, "id": account_id,
                "note": "Archived. Its data is intact; pass hard=true to delete it."}

    # Count what the cascade will take, so the response says what was destroyed
    # rather than just "ok".
    counts = {}
    for table in ("company_profile", "branding", "value_assumptions",
                  "research_runs", "kb_articles", "account_asset_status"):
        try:
            found = await db.fetchrow(
                f"SELECT count(*) AS n FROM {table} WHERE account_id = $1",
                account_id)
            counts[table] = found["n"] if found else 0
        except Exception:  # noqa: BLE001
            pass

    await db.execute("DELETE FROM accounts WHERE id = $1", account_id)
    acct.invalidate_default()
    await write_audit("account", account_id, "delete", actor,
                      {"name": row["name"], "cascaded": counts})
    logger.warning("account %s (%s) hard-deleted by %s; cascaded %s",
                   account_id, row["name"], actor, counts)
    return {"deleted": True, "id": account_id, "cascaded": counts}
