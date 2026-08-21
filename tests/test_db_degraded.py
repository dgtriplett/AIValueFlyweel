"""A Lakebase outage must not be reported as demo mode, or as healthy.

THE FAILURE THIS GUARDS
-----------------------
`get_pool()` caught every pool-creation error and set `is_demo_mode = True`. Demo
mode makes reads return `[]` and writes no-op, which is correct when nobody
configured a database — and catastrophic when one IS configured and merely
unreachable. A bad OAuth token or a Lakebase outage produced:

  * every screen rendering "no data yet" over a live customer's populated database,
  * /api/health reporting "healthy",
  * no page, no alert, no visible cause.

So the two states have to be distinguishable, and the discriminator is whether
PGHOST is set. These tests pin that discrimination, because the empty-read
behaviour is identical in both cases and only the reporting differs.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
from server.db import DatabasePool  # noqa: E402
from fakedb import run  # noqa: E402


class TestUnconfiguredIsDemoMode(unittest.TestCase):
    """No PGHOST: empty reads are correct and the app is healthy."""

    def test_no_pghost_is_demo_mode_not_degraded(self):
        pool = DatabasePool()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(run(pool.get_pool()))
        self.assertTrue(pool.is_demo_mode)
        self.assertFalse(pool.is_degraded)
        self.assertIsNone(pool.last_error)


class TestConfiguredButUnreachableIsDegraded(unittest.TestCase):
    """PGHOST set and the pool failing: a real outage, and it must say so."""

    def _failing_pool(self, exc):
        pool = DatabasePool()
        env = {"PGHOST": "h", "PGUSER": "u"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("server.db.get_oauth_token", return_value="tok"), \
             mock.patch("asyncpg.create_pool", side_effect=exc):
            self.assertIsNone(run(pool.get_pool()))
        return pool

    def test_connection_failure_is_not_demo_mode(self):
        pool = self._failing_pool(OSError("no route to host"))
        self.assertFalse(
            pool.is_demo_mode,
            "an outage reported as demo mode is an outage nobody investigates")
        self.assertTrue(pool.is_degraded)

    def test_auth_failure_is_not_demo_mode(self):
        pool = self._failing_pool(
            __import__("asyncpg").InvalidPasswordError("bad token"))
        self.assertFalse(pool.is_demo_mode)
        self.assertTrue(pool.is_degraded)

    def test_the_cause_is_recorded_for_the_operator(self):
        pool = self._failing_pool(OSError("no route to host"))
        self.assertIsNotNone(pool.last_error)
        self.assertIn("no route to host", pool.last_error)

    def test_recovering_clears_the_degraded_flag(self):
        """A transient failure must not latch: recovery has to be observable."""
        pool = self._failing_pool(OSError("flap"))
        self.assertTrue(pool.is_degraded)

        class FakePool:
            pass

        async def ok(*args, **kwargs):
            return FakePool()

        with mock.patch.dict(os.environ, {"PGHOST": "h", "PGUSER": "u"}, clear=True), \
             mock.patch("server.db.get_oauth_token", return_value="tok"), \
             mock.patch("asyncpg.create_pool", ok):
            self.assertIsNotNone(run(pool.get_pool()))
        self.assertFalse(pool.is_degraded)
        self.assertIsNone(pool.last_error)

    def test_unsetting_pghost_leaves_degraded(self):
        """Going from configured-and-failing to unconfigured is demo mode again."""
        pool = self._failing_pool(OSError("gone"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(run(pool.get_pool()))
        self.assertTrue(pool.is_demo_mode)
        self.assertFalse(pool.is_degraded)


class TestHealthReportsTheOutage(unittest.TestCase):
    """/api/health is the signal an operator actually watches."""

    def setUp(self):
        self.client = TestClient(appmod.app, raise_server_exceptions=False)

    def test_health_is_unhealthy_when_lakebase_is_unreachable(self):
        class Degraded:
            is_demo_mode = False
            is_degraded = True
            last_error = "OSError: no route to host"

            async def get_pool(self):
                return None

        with mock.patch.object(appmod, "db", Degraded()):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 503,
                         "a masked outage is the whole bug")
        body = response.json()
        self.assertNotEqual(body["status"], "healthy")
        self.assertFalse(body["db_connected"])
        self.assertIn("no route to host", body.get("error", ""))

    def test_health_stays_healthy_when_lakebase_is_simply_unconfigured(self):
        """The fresh-install / demo path must keep working."""
        class Unconfigured:
            is_demo_mode = True
            is_degraded = False
            last_error = None

            async def get_pool(self):
                return None

        with mock.patch.object(appmod, "db", Unconfigured()):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "healthy")
        self.assertTrue(body["demo_mode"])

    def test_health_is_unhealthy_when_the_pool_exists_but_queries_fail(self):
        """A pool that opened but cannot answer is still an outage."""
        class Broken:
            is_demo_mode = False
            is_degraded = False
            last_error = None

            async def get_pool(self):
                class P:
                    def acquire(self):
                        raise OSError("connection reset")
                return P()

        with mock.patch.object(appmod, "db", Broken()):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(response.json()["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
