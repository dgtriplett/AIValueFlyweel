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

WHAT THIS IS NOT
----------------
This is scoping, not authorization. It decides WHICH account's rows a request reads;
it does not decide whether the caller is allowed to. The app runs behind Databricks
Apps auth and every user of one instance is trusted with that instance's data. If
that ever stops being true — a shared instance across unrelated customers — this
module is where the check belongs, and the ContextVar already carries the identity
needed to make it.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

from .db import db

logger = logging.getLogger(__name__)

# The account for the current request. None means "not yet resolved" — reads fall
# back to the default account, which is what an unscoped background task
# (token refresh, a migration check) should see.
current_account_id: ContextVar[int | None] = ContextVar(
    "current_account_id", default=None)

ACCOUNT_HEADER = "x-grid-atlas-account"

# Cached default so the common path is not a query per request. Invalidated whenever
# an account is created, deleted, or made default.
_default_cache: dict[str, int | None] = {"id": None}


def invalidate_default() -> None:
    """Drop the cached default. Called after any write to `accounts`."""
    _default_cache["id"] = None


async def default_account_id() -> int | None:
    """The default account's id, or None on an un-migrated / empty database."""
    if _default_cache["id"] is not None:
        return _default_cache["id"]
    try:
        row = await db.fetchrow(
            "SELECT id FROM accounts WHERE is_default AND is_active LIMIT 1")
    except Exception:  # noqa: BLE001 - table absent before migration 009
        return None
    if row is None:
        # An install where the default was archived. Fall back to the lowest active
        # id rather than returning None, which would make every scoped read empty.
        try:
            row = await db.fetchrow(
                "SELECT id FROM accounts WHERE is_active ORDER BY id LIMIT 1")
        except Exception:  # noqa: BLE001
            return None
    if row is None:
        return None
    _default_cache["id"] = row["id"]
    return row["id"]


async def resolve(request) -> int | None:
    """Resolve the account for a request. Returns None only pre-migration."""
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

    class AccountContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = None
            try:
                # Never fail a request on account resolution: a broken accounts
                # table should degrade to the default, not return 500 for the whole
                # app. The portfolio is still readable either way.
                account_id = await resolve(request)
                token = current_account_id.set(account_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("account resolution failed (%s) — unscoped",
                               type(exc).__name__)
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
    """
    prefix = f"{alias}." if alias else ""
    account_id = await current()
    if account_id is None:
        # Pre-migration: no accounts table, so nothing is scoped.
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
