"""Account resolution must fail CLOSED, and only when it genuinely fails.

THE FAILURE THIS GUARDS
-----------------------
Two places turned "I don't know whose data this is" into "show all of it":

  * the middleware caught every resolution error and continued with the ContextVar
    unset, logging "unscoped" as though that were a degraded-but-safe state;
  * `scope_clause()` returned the literal `true` whenever no account resolved.

Together, a transient Lakebase error while reading the `accounts` table produced a
WHERE clause of `true` on every scoped query — one tenant's request serving every
tenant's rows, with a 200 and nothing in the response to indicate it.

THE DISTINCTION THAT MATTERS
----------------------------
Failing closed is only correct if "no accounts exist yet" is still open. A
pre-migration install and a fresh deploy have no accounts table and no rows, and
the app has to work so an operator can run the migration and create the first
account. That is not a failure and must not 503.

So these tests pin BOTH directions: a real error blocks, and an empty install does
not. A fix that 503s on both would be as broken as the original, just in the other
direction — it would make every fresh install look dead on arrival.
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
from fakedb import FakeDB, Row, run  # noqa: E402


class MissingTable(Exception):
    """Stands in for asyncpg.UndefinedTableError (SQLSTATE 42P01)."""
    sqlstate = "42P01"


class ConnectionLost(Exception):
    """A real database failure: the table exists, we just couldn't reach it."""
    sqlstate = "08006"      # connection_failure


class ExplodingDB(FakeDB):
    def __init__(self, exc):
        super().__init__(has_pool=True)
        self._exc = exc

    async def fetch(self, sql, *args):
        raise self._exc

    async def fetchrow(self, sql, *args):
        raise self._exc


class AccountTestCase(unittest.TestCase):
    """Each test gets a clean default cache — it is process-global."""

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)


class TestErrorClassification(AccountTestCase):
    def test_missing_table_is_not_a_failure(self):
        self.assertTrue(acct.is_missing_relation(MissingTable("no relation")))

    def test_connection_loss_is_a_failure(self):
        self.assertFalse(acct.is_missing_relation(ConnectionLost("gone")))

    def test_permission_error_is_a_failure(self):
        class NoPermission(Exception):
            sqlstate = "42501"
        self.assertFalse(acct.is_missing_relation(NoPermission("denied")))

    def test_message_fallback_when_there_is_no_sqlstate(self):
        """The stubbed asyncpg in tests raises plain exceptions."""
        self.assertTrue(acct.is_missing_relation(
            Exception('relation "accounts" does not exist')))
        self.assertFalse(acct.is_missing_relation(Exception("timeout")))


class TestDefaultAccountIdDistinguishesTheCases(AccountTestCase):
    def test_missing_table_returns_none(self):
        with mock.patch.object(acct, "db", ExplodingDB(MissingTable("nope"))):
            self.assertIsNone(run(acct.default_account_id()))

    def test_real_error_raises(self):
        with mock.patch.object(acct, "db", ExplodingDB(ConnectionLost("gone"))):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.default_account_id())

    def test_empty_accounts_table_returns_none(self):
        """Fresh install: the table is there, nothing in it. Not a failure."""
        with mock.patch.object(acct, "db", FakeDB(has_pool=True)):
            self.assertIsNone(run(acct.default_account_id()))


class TestScopeClauseNeverSilentlyUnscopes(AccountTestCase):
    def test_scope_clause_is_true_only_when_no_accounts_exist(self):
        with mock.patch.object(acct, "db", FakeDB(has_pool=True)):
            clause, params = run(acct.scope_clause())
        self.assertEqual(clause, "true")
        self.assertEqual(params, [])

    def test_scope_clause_raises_on_a_resolution_failure(self):
        """The bug: this returned `true`, reading every tenant's rows."""
        with mock.patch.object(acct, "db", ExplodingDB(ConnectionLost("gone"))):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.scope_clause())

    def test_scope_clause_binds_the_account_when_one_resolves(self):
        db = FakeDB(has_pool=True).on("FROM accounts", [Row(id=7)])
        with mock.patch.object(acct, "db", db):
            clause, params = run(acct.scope_clause("a"))
        self.assertIn("a.account_id = $1", clause)
        self.assertEqual(params, [7])

    def test_owned_clause_raises_on_a_resolution_failure(self):
        with mock.patch.object(acct, "db", ExplodingDB(ConnectionLost("gone"))):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.owned_clause())


class TestMiddlewareFailsClosed(AccountTestCase):
    """End-to-end: a scoped request must not be answered with unscoped data."""

    def _client(self, db):
        app = FastAPI()
        acct.install_middleware(app)

        @app.get("/api/values")
        async def values():
            return {"served": True}

        @app.get("/api/health")
        async def health():
            return {"status": "healthy"}

        @app.get("/api/accounts")
        async def accounts():
            return {"accounts": []}

        @app.get("/{path:path}")
        async def spa(path: str):
            return {"spa": True}

        self._patch = mock.patch.object(acct, "db", db)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        return TestClient(app, raise_server_exceptions=False)

    def test_scoped_api_request_gets_503_on_resolution_failure(self):
        client = self._client(ExplodingDB(ConnectionLost("gone")))
        response = client.get("/api/values")
        self.assertEqual(
            response.status_code, 503,
            "serving this request would have spanned every tenant")
        self.assertNotIn("served", response.json())

    def test_health_still_answers_so_the_outage_is_diagnosable(self):
        client = self._client(ExplodingDB(ConnectionLost("gone")))
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_accounts_still_answers_so_an_operator_can_fix_it(self):
        client = self._client(ExplodingDB(ConnectionLost("gone")))
        self.assertEqual(client.get("/api/accounts").status_code, 200)

    def test_spa_shell_still_loads(self):
        """A blank tab is a worse diagnosis than an error page."""
        client = self._client(ExplodingDB(ConnectionLost("gone")))
        self.assertEqual(client.get("/index.html").status_code, 200)

    def test_fresh_install_is_not_blocked(self):
        """No accounts yet must keep working, or setup is impossible."""
        client = self._client(FakeDB(has_pool=True))
        response = client.get("/api/values")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["served"])

    def test_premigration_install_is_not_blocked(self):
        client = self._client(ExplodingDB(MissingTable("no accounts table")))
        response = client.get("/api/values")
        self.assertEqual(response.status_code, 200)

    def test_resolved_account_is_echoed_on_a_normal_request(self):
        db = FakeDB(has_pool=True).on("FROM accounts", [Row(id=3)])
        client = self._client(db)
        response = client.get("/api/values")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Grid-Atlas-Account"), "3")


if __name__ == "__main__":
    unittest.main()
