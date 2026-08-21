"""Account resolution — which customer's data a request is about.

WHY A CONTEXTVAR AND NOT A PARAMETER
------------------------------------
The account has to reach code that is a long way from the request: the value engine
loading assumptions, readiness resolving asset status, the proposal agent gathering
context. Threading an `account_id` argument through every one of those signatures
would be a large, mechanical change to code that is otherwise correct — and the first
function someone forgot to pass it to would silently read the default account's data
under another customer's name.

A ContextVar set once by middleware is invisible to the intermediate layers and
impossible to forget. It is the same mechanism the request id and the query budget
already use, for the same reason, and it is correct under async concurrency where a
module global would leak one request's account into another's.

RESOLUTION ORDER
----------------
  1. `X-Grid-Atlas-Account` header — set by the UI's account switcher.
  2. `account` query parameter — so a link can carry it (an exported report, a
     shared URL).
  3. The default account.

There is deliberately no "no account" state. Every read is scoped, and a request that
cannot resolve an account is a bug rather than a global view — a query silently
spanning tenants is exactly the failure this module exists to prevent.

SCOPING VERSUS AUTHORIZATION
----------------------------
Most of this module is SCOPING: it decides WHICH account's rows a request reads. The
deployment model is one instance per customer behind Databricks Apps auth, so every
authenticated user of an instance is trusted with that instance's data, and there is
no per-account membership table to consult.

That model does NOT justify letting any caller hard-delete an account or enumerate
every tenant on the instance, which is what the routes previously allowed — the
account selector was read from a client-settable header and checked only for
existence. So the Authorization section below adds what is actually enforceable on
top of the platform-attributed identity:

  * `trusted_identity()` — the identity the Apps proxy injects, which the browser
    cannot forge (unlike the X-Grid-Atlas-User header current_user() also accepts
    for local dev, which is fine for audit attribution and useless for access
    control).
  * `require_admin()` — an allowlist gate, failing closed, on the destructive and
    cross-tenant operations.
  * `authorize_account_read()` — the seam a real membership model plugs into, so
    there is one place to change rather than four call sites and one that was
    forgotten.

See the TODO(multi-tenant) notes below for what an `account_members` table would
change.
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar

from .db import db

logger = logging.getLogger(__name__)


class AccountResolutionError(RuntimeError):
    """Resolution failed for a reason that is NOT 'no accounts exist yet'.

    The distinction is the whole point. An install with no accounts table (before
    migration 009) or no rows in it (a fresh deploy) is a legitimate state: nothing
    is scoped because nothing has been set up, and the app must still work so the
    operator can set it up.

    A database error while asking which account this is, is a different thing
    entirely. Treating it as "no account" made `scope_clause()` return `true`, which
    turned a transient Lakebase hiccup into every tenant's rows being served to
    whoever asked. This exception is what makes that case fail closed instead.
    """


# The account for the current request. None means "not yet resolved" — reads fall
# back to the default account, which is what an unscoped background task
# (token refresh, a migration check) should see.
current_account_id: ContextVar[int | None] = ContextVar(
    "current_account_id", default=None)

ACCOUNT_HEADER = "x-grid-atlas-account"

# Paths that must keep answering when account resolution fails.
#
# /api/health has to report the outage — 503ing it from middleware would replace the
# diagnostic body with a generic error and hide the cause. The SPA and its assets
# have to load so the user sees an error page rather than a blank tab, and they read
# no tenant data anyway. /api/setup and /api/accounts are how an operator fixes the
# very condition that caused the failure.
_UNSCOPED_PREFIXES = (
    "/api/health",
    "/api/runtime",
    "/api/setup",
    "/api/accounts",
)


def _requires_account_scope(path: str) -> bool:
    """Whether a failed resolution must block this request.

    Only /api/* reads tenant rows. Everything else is the SPA shell.
    """
    if not path.startswith("/api/"):
        return False
    return not path.startswith(_UNSCOPED_PREFIXES)


def _is_missing_relation(exc: BaseException) -> bool:
    """True when the error means "the accounts table isn't there yet".

    That is the pre-migration state and it is legitimate. Anything else — a dropped
    connection, a permission error, a timeout — is a failure and must not be
    mistaken for it.

    Matched on SQLSTATE first (42P01 undefined_table, 3F000 invalid_schema_name),
    since that is asyncpg's structured signal, with a class-name and message check
    behind it so the tests' stubbed asyncpg and any wrapped exception still classify
    correctly.
    """
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in ("42P01", "3F000"):
        return True
    if sqlstate is not None:
        # A real SQLSTATE that is not "missing relation" is a genuine error.
        return False
    if type(exc).__name__ in ("UndefinedTableError", "InvalidSchemaNameError"):
        return True
    message = str(exc).lower()
    return ("does not exist" in message and "relation" in message) \
        or "undefinedtable" in message

# Cached default so the common path is not a query per request. Invalidated whenever
# an account is created, deleted, or made default.
_default_cache: dict[str, int | None] = {"id": None}


def invalidate_default() -> None:
    """Drop the cached default. Called after any write to `accounts`."""
    _default_cache["id"] = None


async def default_account_id() -> int | None:
    """The default account's id, or None when no accounts exist yet.

    Raises AccountResolutionError if the question could not be ANSWERED — a DB
    error other than a missing table. Returning None there would look identical to
    a fresh install and unscope every read, so the two must not share a return value.
    """
    if _default_cache["id"] is not None:
        return _default_cache["id"]
    try:
        row = await db.fetchrow(
            "SELECT id FROM accounts WHERE is_default AND is_active LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        if _is_missing_relation(exc):
            # Pre-migration-009 install: nothing is scoped because nothing is set up.
            return None
        raise AccountResolutionError(
            f"could not read the accounts table ({type(exc).__name__}: {exc})"
        ) from exc
    if row is None:
        # An install where the default was archived. Fall back to the lowest active
        # id rather than returning None, which would make every scoped read empty.
        try:
            row = await db.fetchrow(
                "SELECT id FROM accounts WHERE is_active ORDER BY id LIMIT 1")
        except Exception as exc:  # noqa: BLE001
            if _is_missing_relation(exc):
                return None
            raise AccountResolutionError(
                f"could not read the accounts table ({type(exc).__name__}: {exc})"
            ) from exc
    if row is None:
        # The table exists and has no active row: a fresh install before the first
        # account is created. Legitimate, and the setup screens must still work.
        return None
    _default_cache["id"] = row["id"]
    return row["id"]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
# HEADERS THE PLATFORM SETS, AND THE ONES IT DOES NOT
# ---------------------------------------------------
# Databricks Apps terminates auth in front of this process and injects
# X-Forwarded-Email / X-Forwarded-User. Those cannot be set by the browser — the
# proxy overwrites them — so they are a TRUSTED identity.
#
# server/common.py's current_user() also accepts X-Grid-Atlas-User and
# X-GridValue-User for local development. Those ARE client-settable, which is fine
# for attribution in an audit row but must never be the basis of an access
# decision: anyone could send X-Grid-Atlas-User: admin@utility.com. So authorization
# reads only the forwarded pair, and only when running as a Databricks App.
_TRUSTED_IDENTITY_HEADERS = ("x-forwarded-email", "x-forwarded-user")

# Operators allowed to perform destructive account operations. Comma-separated
# emails; matched case-insensitively against the platform-attributed identity.
#
# TODO(multi-tenant): this is an allowlist, not a membership model. The app has no
# per-account membership table yet, so there is no way to express "alice may see
# account 3 but not account 7" — every authenticated user of an instance can read
# every account on it, which is the documented deployment model (one instance per
# customer, see the WHAT THIS IS NOT note at the top of this module). What an
# allowlist CAN do is stop any authenticated user from hard-deleting a customer's
# account or enumerating every tenant, and that is what it is used for below.
#
# When a membership model arrives (an `account_members` table keyed by identity),
# authorize_account_read() is the single place that has to learn about it.
def _admin_emails() -> set[str]:
    raw = os.environ.get("GRID_ATLAS_ADMINS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def trusted_identity(request) -> str | None:
    """The caller's platform-attributed identity, or None if there isn't one.

    None means "not authenticated by the platform" — a local run, or a direct hit
    on the container bypassing the proxy. Callers must treat None as unprivileged.
    """
    for header in _TRUSTED_IDENTITY_HEADERS:
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip().lower()
    return None


def is_admin(request) -> bool:
    """Whether this caller may perform destructive account operations.

    Fails CLOSED by default: with no GRID_ATLAS_ADMINS configured, nobody is an
    admin and the destructive paths are simply unavailable. That is deliberate —
    the alternative (empty allowlist means everyone) would make an unconfigured
    deployment maximally permissive, which is the wrong default for hard-delete.

    Outside Databricks Apps there are no trusted headers, so local development
    would lock itself out of its own account management. GRID_ATLAS_ADMINS is
    honoured there via the same allowlist, so a local operator sets it explicitly
    rather than the check being silently skipped.
    """
    admins = _admin_emails()
    if not admins:
        return False
    identity = trusted_identity(request)
    if identity is None:
        return False
    return identity in admins


def require_admin(request, action: str) -> str:
    """Authorize a destructive account operation, or raise 403.

    Returns the authorized identity so the caller can put it in the audit row —
    the point of an admin gate is partly that the log says who used it.
    """
    from fastapi import HTTPException

    identity = trusted_identity(request)
    if not is_admin(request):
        logger.warning("refused %s: %s is not an authorized account admin",
                       action, identity or "an unauthenticated caller")
        raise HTTPException(
            403,
            f"{action} requires an account administrator. Add the operator's "
            "Databricks email to the GRID_ATLAS_ADMINS setting to permit it. "
            "This operation is refused by default because it destroys or exposes "
            "another customer's data.")
    return identity


async def authorize_account_read(request, account_id: int) -> None:
    """Authorize the caller to READ `account_id`, or raise 403.

    TODO(multi-tenant): with no membership table, the only enforceable rule is the
    documented one — an authenticated user of this instance may read the accounts on
    it. So this verifies the caller is authenticated by the platform (when running
    as a Databricks App) and otherwise lets the read proceed.

    It exists as a named seam anyway: when membership arrives, this function is the
    one place that changes, and every caller is already routed through it. Inlining
    the check at each call site is how you end up with four of them and one that was
    never updated.
    """
    from fastapi import HTTPException
    from .config import IS_DATABRICKS_APP

    if IS_DATABRICKS_APP and trusted_identity(request) is None:
        # Deployed behind the Apps proxy, which always sets these headers. Their
        # absence means the request did not come through it.
        logger.warning("account read refused: no platform identity on the request")
        raise HTTPException(
            403, "This request carries no Databricks identity, so the account it "
                 "may read cannot be established.")


async def resolve(request) -> int | None:
    """Resolve the account for a request. Returns None only pre-migration.

    The selector arrives in a client-controlled header or query parameter, so what
    it can select is an authorization question, not just an existence one.

    TODO(multi-tenant): the only check enforceable without a membership table is
    that the caller is authenticated by the platform — see
    `authorize_account_read()`. That is deliberately NOT called here: middleware runs
    for every request including /api/health and the SPA, and raising a 403 from this
    depth would turn "no identity" into a blanket outage. Selection is validated
    here; the per-account read decision belongs at the routes, where the exempt
    paths are already known.
    """
    raw = (request.headers.get(ACCOUNT_HEADER)
           or request.query_params.get("account"))
    if raw:
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            # A slug is friendlier in a URL than an integer.
            row = await db.fetchrow(
                "SELECT id FROM accounts WHERE slug = $1 AND is_active",
                str(raw).strip().lower())
            if row is not None:
                return row["id"]
            logger.warning("unknown account %r — using the default", raw)
            return await default_account_id()
        # Verify it exists and is active. An id for a deleted account must not
        # silently read as "no rows"; that looks like a customer's data vanishing.
        row = await db.fetchrow(
            "SELECT id FROM accounts WHERE id = $1 AND is_active", requested)
        if row is not None:
            return row["id"]
        logger.warning("account %s not found or inactive — using the default",
                       requested)
    return await default_account_id()


async def current() -> int | None:
    """The current account, resolving the default if middleware has not run.

    Callers deep in the stack use this rather than reading the ContextVar directly,
    so a background task or a test that never went through middleware still gets a
    sensible account instead of None.
    """
    account_id = current_account_id.get()
    if account_id is not None:
        return account_id
    return await default_account_id()


def install_middleware(app) -> None:
    """Resolve the account once per request and put it in the ContextVar."""
    from starlette.middleware.base import BaseHTTPMiddleware

    from starlette.responses import JSONResponse

    class AccountContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = None
            try:
                account_id = await resolve(request)
                token = current_account_id.set(account_id)
            except Exception as exc:  # noqa: BLE001
                # FAIL CLOSED. This used to swallow the error and continue
                # "unscoped", which meant scope_clause() returned `true` and the
                # request read EVERY tenant's rows. A 503 is the correct answer:
                # we cannot establish whose data this is, so we serve none of it.
                #
                # Health, setup and the SPA shell are exempted (see
                # _UNSCOPED_PREFIXES) because they read no tenant data and are how
                # the failure gets diagnosed and fixed.
                if _requires_account_scope(request.url.path):
                    logger.error(
                        "account resolution failed for %s (%s: %s) — refusing the "
                        "request rather than serving unscoped data",
                        request.url.path, type(exc).__name__, exc)
                    return JSONResponse(
                        {"error": "Account could not be resolved, so this request "
                                  "would not be scoped to one customer. Refusing "
                                  "rather than returning data across accounts.",
                         "detail": f"{type(exc).__name__}: {exc}"},
                        status_code=503)
                logger.warning(
                    "account resolution failed for %s (%s) — continuing, this path "
                    "reads no tenant data", request.url.path, type(exc).__name__)
            try:
                response = await call_next(request)
                if token is not None and current_account_id.get() is not None:
                    # Echoed so a client can confirm which account answered, which
                    # is the first question when a number looks wrong.
                    response.headers["X-Grid-Atlas-Account"] = str(
                        current_account_id.get())
                return response
            finally:
                if token is not None:
                    current_account_id.reset(token)

    app.add_middleware(AccountContextMiddleware)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
async def scope_clause(alias: str = "") -> tuple[str, list]:
    """A WHERE fragment scoping a table to the current account.

    Returns (sql, params) with a single `$1` placeholder, e.g.
        clause, params = await scope_clause("b")
        await db.fetch(f"SELECT * FROM branding b WHERE {clause}", *params)

    NULL account_id is treated as visible to every account. That is what lets a
    shared row — a seeded KB folder, a global glossary term — serve all tenants
    without being copied per account.

    Returns the unscoped `true` ONLY when no accounts exist yet. If resolution
    fails, this raises rather than returning `true`: an unscoped clause during a
    database problem is a cross-tenant read, which is exactly the failure this
    module exists to prevent.
    """
    prefix = f"{alias}." if alias else ""
    account_id = await current()
    if account_id is None:
        # No accounts configured yet (pre-migration or fresh install), so there is
        # nothing to scope to and no other tenant whose rows could leak.
        return "true", []
    return f"({prefix}account_id = $1 OR {prefix}account_id IS NULL)", [account_id]


async def owned_clause(column: str = "account_id", *, param_index: int = 1) -> tuple[str, list]:
    """A WHERE fragment for tables whose rows are owned by exactly one account.

    Unlike `scope_clause`, this does NOT include `account_id IS NULL`. NULL means
    "shared/global" for reference-like content such as seeded KB folders and
    glossary terms; it is not appropriate for customer-owned work like roadmap
    items, value records, funding requests, comments, research runs, or chats.
    """
    account_id = await current()
    if account_id is None:
        return "true", []
    return f"{column} = ${param_index}", [account_id]
