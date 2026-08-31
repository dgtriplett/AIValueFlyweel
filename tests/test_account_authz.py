"""Destructive account operations must be authorized, and fail closed.

THE FAILURE THIS GUARDS
-----------------------
The account routes checked only that an account EXISTED, never that the caller was
allowed to touch it. So any request reaching the app could:

  * `DELETE /api/accounts/3?hard=true` — cascade away a customer's calibrated
    assumptions, research runs, proposals and source positions, irreversibly;
  * `GET /api/accounts?include_inactive=true` — enumerate every tenant on the
    instance, including archived ones;
  * `PATCH /api/accounts/3 {"make_default": true}` — repoint every unscoped request
    at a different customer's data.

WHAT IS AND IS NOT ENFORCEABLE HERE
-----------------------------------
There is no membership table, so "alice may read account 3 but not 7" cannot be
expressed and is NOT what these tests claim. The documented model is one instance per
customer, and every authenticated user of an instance may read the accounts on it.

What IS enforceable is that the operations which destroy or expose another
customer's data require an operator on an allowlist, keyed on the identity the
Databricks Apps proxy injects. The critical property is the DEFAULT: with
GRID_ATLAS_ADMINS unset, nobody is an admin. An empty allowlist meaning "everyone"
would make an unconfigured deployment maximally permissive — the wrong direction for
an irreversible cascade.

THE HEADER DISTINCTION
----------------------
`current_user()` accepts X-Grid-Atlas-User for local dev. That header is
CLIENT-SETTABLE, so it is fine for audit attribution and must never grant access —
otherwise the gate is bypassed by typing a header. Only the proxy-injected
X-Forwarded-Email / X-Forwarded-User count, and `test_client_settable_header_*` is
the test that pins it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import accounts as acct  # noqa: E402
from server.routes import accounts as accounts_routes  # noqa: E402
from fakedb import FakeDB, Row, run  # noqa: E402

ADMIN = "ops@utility.com"
OUTSIDER = "intern@utility.com"


class FakeRequest:
    """Just the header bag the authz helpers read."""

    def __init__(self, headers: dict | None = None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class TestTrustedIdentity(unittest.TestCase):
    def test_forwarded_email_is_trusted(self):
        self.assertEqual(
            acct.trusted_identity(FakeRequest({"X-Forwarded-Email": ADMIN})), ADMIN)

    def test_identity_is_normalized(self):
        self.assertEqual(
            acct.trusted_identity(FakeRequest({"X-Forwarded-Email": " OPS@Utility.COM "})),
            ADMIN)

    def test_no_headers_means_no_identity(self):
        self.assertIsNone(acct.trusted_identity(FakeRequest()))

    def test_client_settable_header_is_not_an_identity(self):
        """X-Grid-Atlas-User is spoofable; it must not authenticate anyone."""
        for header in ("X-Grid-Atlas-User", "X-GridValue-User"):
            with self.subTest(header=header):
                self.assertIsNone(
                    acct.trusted_identity(FakeRequest({header: ADMIN})))


class TestAdminGateFailsClosed(unittest.TestCase):
    def test_nobody_is_an_admin_when_the_allowlist_is_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(acct.is_admin(FakeRequest({"X-Forwarded-Email": ADMIN})))

    def test_empty_allowlist_does_not_mean_everyone(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": "   "}, clear=True):
            self.assertFalse(acct.is_admin(FakeRequest({"X-Forwarded-Email": ADMIN})))

    def test_listed_admin_is_allowed(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            self.assertTrue(acct.is_admin(FakeRequest({"X-Forwarded-Email": ADMIN})))

    def test_unlisted_identity_is_refused(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            self.assertFalse(
                acct.is_admin(FakeRequest({"X-Forwarded-Email": OUTSIDER})))

    def test_matching_is_case_insensitive_both_ways(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": "OPS@Utility.com"},
                             clear=True):
            self.assertTrue(
                acct.is_admin(FakeRequest({"X-Forwarded-Email": "ops@utility.com"})))

    def test_a_spoofed_header_does_not_grant_admin(self):
        """The bypass that would make the whole gate decorative."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            self.assertFalse(
                acct.is_admin(FakeRequest({"X-Grid-Atlas-User": ADMIN})))

    def test_allowlist_accepts_several_operators(self):
        with mock.patch.dict(
                os.environ, {"GRID_ATLAS_ADMINS": f"{ADMIN}, platform@utility.com"},
                clear=True):
            self.assertTrue(acct.is_admin(
                FakeRequest({"X-Forwarded-Email": "platform@utility.com"})))

    def test_require_admin_raises_403_and_names_the_setting(self):
        from fastapi import HTTPException

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as caught:
                acct.require_admin(FakeRequest(), "Deleting an account")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("GRID_ATLAS_ADMINS", caught.exception.detail)

    def test_require_admin_returns_the_identity_for_the_audit_row(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            self.assertEqual(
                acct.require_admin(FakeRequest({"X-Forwarded-Email": ADMIN}), "x"),
                ADMIN)


class AccountsRouteTestCase(unittest.TestCase):
    """Drives the real router so the gates are tested where they are wired."""

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)

        self.db = FakeDB(has_pool=True)
        # Every account lookup the routes make finds an ordinary non-default row.
        self.db.on("FROM accounts", [Row(id=3, name="Eversource", is_default=False,
                                         slug="eversource", utility_type="iou",
                                         is_active=True)])
        for module in (acct, accounts_routes):
            patcher = mock.patch.object(module, "db", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = FastAPI()
        app.include_router(accounts_routes.router, prefix="/api")
        self.client = TestClient(app, raise_server_exceptions=False)

    def as_(self, email: str | None) -> dict:
        return {"X-Forwarded-Email": email} if email else {}


class TestDestructiveRoutesAreGated(AccountsRouteTestCase):
    def test_hard_delete_is_refused_without_an_admin(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.request(
                "DELETE", "/api/accounts/3", params={"hard": "true"},
                headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(
            "DELETE FROM accounts",
            " ".join(self.db.queries),
            "the cascade ran despite the 403 — the gate must precede the write")

    def test_archive_is_refused_without_an_admin(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.request("DELETE", "/api/accounts/3",
                                           headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_spoofed_header_cannot_hard_delete(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            response = self.client.request(
                "DELETE", "/api/accounts/3", params={"hard": "true"},
                headers={"X-Grid-Atlas-User": ADMIN})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("DELETE FROM accounts", " ".join(self.db.queries))

    def test_admin_may_hard_delete(self):
        """The gate must not be a brick wall — the operation still has to work."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            response = self.client.request(
                "DELETE", "/api/accounts/3", params={"hard": "true"},
                headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 200)
        self.assertIn("DELETE FROM accounts", " ".join(self.db.queries))

    def test_create_is_gated(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/accounts", json={"name": "New Utility"},
                                        headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("INSERT INTO accounts", " ".join(self.db.queries))

    def test_patch_is_gated(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.patch("/api/accounts/3", json={"name": "Renamed"},
                                         headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_make_default_is_gated(self):
        """Repointing every unscoped request at another tenant is a big lever."""
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.patch("/api/accounts/3",
                                         json={"make_default": True},
                                         headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("is_default = true", " ".join(self.db.queries))

    def test_enumerating_archived_accounts_is_gated(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/api/accounts",
                                       params={"include_inactive": "true"},
                                       headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_listing_active_accounts_is_not_gated(self):
        """The switcher must keep working for ordinary users."""
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/api/accounts", headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 200)


class TestDefaultSwitchIsTransactional(AccountsRouteTestCase):
    """The clear-then-set pair must not be interruptible.

    Two separate execute() calls each took their own connection, so a failure
    between them left the table with NO default — and then every unscoped request
    resolves to `ORDER BY id LIMIT 1`, i.e. some other customer's account.
    """

    def test_make_default_runs_in_one_transaction(self):
        used = {}

        class TxDB(FakeDB):
            def transaction(inner_self):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def cm():
                    used["opened"] = True
                    statements = []

                    class Conn:
                        async def execute(self, sql, *args):
                            statements.append(sql)
                            return "OK"
                    yield Conn()
                    used["statements"] = statements
                return cm()

        db = TxDB(has_pool=True)
        db.on("FROM accounts", [Row(id=3, name="E", is_default=False, slug="e",
                                    utility_type=None, is_active=True)])
        with mock.patch.object(accounts_routes, "db", db), \
             mock.patch.object(acct, "db", db), \
             mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            response = self.client.patch("/api/accounts/3",
                                         json={"make_default": True},
                                         headers=self.as_(ADMIN))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(used.get("opened"), "make_default did not use a transaction")
        statements = used.get("statements", [])
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("is_default = false", statements[0])
        self.assertIn("is_default = true", statements[1])

    def test_no_pool_reports_unavailable_rather_than_half_applying(self):
        class NoPool(FakeDB):
            def transaction(inner_self):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def cm():
                    yield None
                return cm()

        db = NoPool(has_pool=True)
        db.on("FROM accounts", [Row(id=3, name="E", is_default=False, slug="e",
                                    utility_type=None, is_active=True)])
        with mock.patch.object(accounts_routes, "db", db), \
             mock.patch.object(acct, "db", db), \
             mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            response = self.client.patch("/api/accounts/3",
                                         json={"make_default": True},
                                         headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 503)


class TestDbTransactionHelper(unittest.TestCase):
    def test_yields_none_without_a_pool(self):
        from server.db import DatabasePool

        pool = DatabasePool()

        async def check():
            with mock.patch.dict(os.environ, {}, clear=True):
                async with pool.transaction() as conn:
                    return conn
        self.assertIsNone(run(check()))


if __name__ == "__main__":
    unittest.main()
