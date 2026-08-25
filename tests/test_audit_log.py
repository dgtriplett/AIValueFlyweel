"""Audit-log read + export endpoints and broadened audit coverage (Phase D).

WHAT THIS GUARDS
----------------
The audit log names who changed what on this instance, so:

  (a) GET /api/audit is ADMIN-GATED and FAILS CLOSED. A non-admin — or an
      unconfigured GRID_ATLAS_ADMINS allowlist — gets a 403 BEFORE any row is read.
      If this regressed, any authenticated user could read the whole change history.
  (b) For an admin it returns rows, newest-first, and the optional filters
      (entity_type / action / actor / search) actually reach the SQL as bound
      parameters rather than being silently ignored.
  (c) GET /api/audit/export.csv is admin-gated too, and streams the filtered rows as
      text/csv with the documented header row.
  (d) BROADENED COVERAGE: a sensitive action that previously wrote no audit row now
      does. Demo load is the witness here — the handler must call write_audit after a
      successful run so the operation is attributable.

The tests stub the db (FakeDB) and mount only the audit (or demo) router, so nothing
touches a live Lakebase. The admin identity is the trusted X-Forwarded-Email header
the Apps proxy injects; a spoofable header must never grant admin.
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.routes import audit as audit_route  # noqa: E402
from server.routes import demo as demo_route  # noqa: E402
from fakedb import FakeDB, Row  # noqa: E402

ADMIN = "ops@utility.com"
OUTSIDER = "intern@utility.com"


def make_client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def as_(email: str) -> dict[str, str]:
    """The trusted platform identity header the Apps proxy injects."""
    return {"X-Forwarded-Email": email}


def _sample_rows():
    now = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    return [
        Row(id=2, entity_type="use_case", entity_id=7, action="value_override",
            actor=ADMIN, diff_json='{"realized_override_enabled": true}',
            created_at=now),
        Row(id=1, entity_type="demo", entity_id=None, action="reset",
            actor=ADMIN, diff_json='{"clean": true}',
            created_at=now - dt.timedelta(hours=1)),
    ]


class TestAuditAuthz(unittest.TestCase):
    """(a) GET + export are admin-gated and fail closed."""

    def test_get_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit", headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)
        # Fail-closed: it refused BEFORE reading any audit rows.
        self.assertEqual([q for q in db.queries if "FROM audit_log" in q], [])

    def test_export_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit/export.csv", headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual([q for q in db.queries if "FROM audit_log" in q], [])

    def test_empty_allowlist_fails_closed(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ""}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit", headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_no_identity_fails_closed(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get("/api/audit")
        self.assertEqual(resp.status_code, 403, resp.text)


class TestAuditRead(unittest.TestCase):
    """(b) Admin gets rows, newest-first, and filters reach the SQL."""

    def test_admin_gets_rows(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit", headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        rows = resp.json()["audit_log"]
        self.assertEqual(len(rows), 2)
        # The query is ORDER BY created_at DESC — newest first.
        selects = [q for q in db.queries if "FROM audit_log" in q]
        self.assertTrue(selects)
        self.assertIn("ORDER BY created_at DESC", selects[0])
        # diff_json (a *_json column) is parsed out of its JSON string by row_to_dict.
        self.assertEqual(rows[0]["diff_json"], {"realized_override_enabled": True})

    def test_filters_reach_the_sql(self):
        """entity_type / action / actor / search each add a WHERE condition + param."""
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", [])
        captured = {}
        real_fetch = db.fetch

        async def capture(sql, *args):
            if "FROM audit_log" in sql:
                captured["sql"] = sql
                captured["args"] = args
            return await real_fetch(sql, *args)

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
            mock.patch.object(db, "fetch", side_effect=capture),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit",
                params={"entity_type": "use_case", "action": "value_override",
                        "actor": ADMIN, "search": "override", "limit": 25},
                headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        sql = captured["sql"]
        self.assertIn("entity_type = $", sql)
        self.assertIn("action = $", sql)
        self.assertIn("actor = $", sql)
        self.assertIn("diff_json::text ILIKE $", sql)
        # The bound params carry the filter values (search is wrapped in %..%), and
        # the last param is the limit.
        self.assertIn("use_case", captured["args"])
        self.assertIn("value_override", captured["args"])
        self.assertIn("%override%", captured["args"])
        self.assertEqual(captured["args"][-1], 25)

    def test_limit_is_capped(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", [])
        captured = {}
        real_fetch = db.fetch

        async def capture(sql, *args):
            if "FROM audit_log" in sql:
                captured["args"] = args
            return await real_fetch(sql, *args)

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
            mock.patch.object(db, "fetch", side_effect=capture),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit", params={"limit": 100000}, headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        # Cap is 500 — a caller cannot pull an unbounded page.
        self.assertEqual(captured["args"][-1], 500)


class TestAuditExport(unittest.TestCase):
    """(c) Export is admin-gated and streams CSV with the documented header."""

    def test_export_returns_csv(self):
        db = FakeDB(has_pool=True)
        db.on("FROM audit_log", _sample_rows())
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(audit_route, "db", db),
        ):
            resp = make_client(audit_route.router).get(
                "/api/audit/export.csv", headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("text/csv"),
                        resp.headers.get("content-type"))
        body = resp.text
        # Header row exactly as documented.
        self.assertTrue(body.splitlines()[0].startswith(
            "created_at,actor,entity_type,entity_id,action,diff_json"))
        # A data row for the value_override entry is present.
        self.assertIn("value_override", body)
        self.assertIn(ADMIN, body)


class TestBroadenedCoverage(unittest.TestCase):
    """(d) A demo load now writes an audit_log row (broadened coverage)."""

    def test_demo_load_writes_audit(self):
        db = FakeDB(has_pool=True)

        async def fake_run(clean):
            return {"ok": True, "clean": clean, "mode": "demo",
                    "counts": {"use_cases": 12}}

        with (
            mock.patch.dict(os.environ,
                            {"GRID_ATLAS_ADMINS": ADMIN, "DEMO_MODE": "1"},
                            clear=True),
            mock.patch.object(demo_route, "DEMO_MODE_ENABLED", True),
            mock.patch.object(demo_route, "db", db),
            mock.patch("server.common.db", db),
            mock.patch.object(demo_route, "_run", side_effect=fake_run),
        ):
            resp = make_client(demo_route.router).post(
                "/api/demo/load", headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        audits = [q for q in db.queries if "INSERT INTO audit_log" in q]
        self.assertTrue(audits, "demo load must write an audit_log row (broadened coverage)")

    def test_demo_load_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ,
                            {"GRID_ATLAS_ADMINS": ADMIN, "DEMO_MODE": "1"},
                            clear=True),
            mock.patch.object(demo_route, "DEMO_MODE_ENABLED", True),
            mock.patch.object(demo_route, "db", db),
        ):
            resp = make_client(demo_route.router).post(
                "/api/demo/load", headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual([q for q in db.queries if "INSERT INTO audit_log" in q], [])


if __name__ == "__main__":
    unittest.main()
