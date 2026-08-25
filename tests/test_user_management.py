"""Admin user-management endpoints (admin-portal/roles, Phase B).

WHAT THIS GUARDS
----------------
`/api/users` grants and revokes the roles that decide what the whole app lets a
person do — an 'admin' row unlocks the admin portal, an 'executive' row locks a user
into a read-only persona. So the invariants worth a test are:

  (a) EVERY method (GET/PUT/DELETE) requires an admin. A non-admin — or an
      unconfigured allowlist — gets a 403 from `require_admin` BEFORE any row is
      read or written. This is the fail-closed gate; if it regressed, any
      authenticated user could rewrite everyone else's access.
  (b) PUT upserts the role AND writes an audit_log row. The audit trail is the only
      record of who changed whose role, so a grant that skips it is a silent one.
  (c) PUT rejects a role outside {admin, pm, executive} with 422, so a typo or a
      client bug can never store a role the rest of the app does not understand.
  (d) An allowlist-admin email is reported `is_env_admin`, because the allowlist
      outranks the table and the UI must not offer to change it via a row.

The tests stub the db (FakeDB) and mount only the users router, so nothing touches a
live Lakebase or LLM. The admin identity is the trusted X-Forwarded-Email header the
Apps proxy injects; a spoofable header must never grant admin.
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

from server.routes import users as users_route  # noqa: E402
from fakedb import FakeDB, Row  # noqa: E402

ADMIN = "ops@utility.com"
OUTSIDER = "intern@utility.com"


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(users_route.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def as_(email: str) -> dict[str, str]:
    """The trusted platform identity header the Apps proxy injects."""
    return {"X-Forwarded-Email": email}


class TestUsersAuthz(unittest.TestCase):
    """(a) Every method requires an admin; the gate fails closed."""

    def test_get_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().get("/api/users", headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_put_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().put(
                "/api/users/someone@utility.com", json={"role": "pm"},
                headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)
        # Fail-closed means it refused BEFORE writing anything.
        self.assertEqual([q for q in db.queries if "INSERT" in q], [])

    def test_delete_refuses_non_admin(self):
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().delete(
                "/api/users/someone@utility.com", headers=as_(OUTSIDER))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual([q for q in db.queries if "DELETE" in q], [])

    def test_empty_allowlist_fails_closed(self):
        """With no allowlist configured, even a would-be admin is refused."""
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ""}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().get("/api/users", headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_no_identity_fails_closed(self):
        """No trusted header at all -> 403 (not a 500, not a pass)."""
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().get("/api/users")
        self.assertEqual(resp.status_code, 403, resp.text)


class TestPutUpsertsAndAudits(unittest.TestCase):
    """(b) PUT upserts the role AND writes an audit_log row."""

    def test_put_upserts_and_writes_audit(self):
        db = FakeDB(has_pool=True)
        # No prior row (old_role is None), then the row read back after upsert.
        db.on("SELECT role FROM app_users WHERE email = $1", [])
        db.on("SELECT email, role, granted_by, created_at, updated_at",
              [Row(email="pm@utility.com", role="executive",
                   granted_by=ADMIN, created_at=None, updated_at=None)])

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
            mock.patch("server.common.db", db),
        ):
            resp = make_client().put(
                "/api/users/PM@Utility.com", json={"role": "executive"},
                headers=as_(ADMIN))

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["role"], "executive")

        # The upsert ran (INSERT ... ON CONFLICT) with the normalized email.
        upserts = [q for q in db.queries if "INSERT INTO app_users" in q
                   and "ON CONFLICT" in q]
        self.assertTrue(upserts, "expected an upsert INSERT ... ON CONFLICT")

        # The audit row was written: write_audit issues an INSERT INTO audit_log.
        audits = [q for q in db.queries if "INSERT INTO audit_log" in q]
        self.assertTrue(audits, "PUT must write an audit_log row")

    def test_put_normalizes_email(self):
        """A mixed-case/whitespace email is stored lowercased and trimmed."""
        db = FakeDB(has_pool=True)
        db.on("SELECT role FROM app_users WHERE email = $1", [])
        db.on("SELECT email, role, granted_by, created_at, updated_at",
              [Row(email="pm@utility.com", role="pm",
                   granted_by=ADMIN, created_at=None, updated_at=None)])

        captured = {}
        real_execute = db.execute

        async def capture(sql, *args):
            if "INSERT INTO app_users" in sql:
                captured["email"] = args[0]
            return await real_execute(sql, *args)

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
            mock.patch.object(db, "execute", side_effect=capture),
        ):
            resp = make_client().put(
                "/api/users/  PM@Utility.COM  ", json={"role": "pm"},
                headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(captured.get("email"), "pm@utility.com")


class TestPutValidatesRole(unittest.TestCase):
    """(c) PUT with an invalid role -> 422, and nothing is written."""

    def test_invalid_role_rejected(self):
        db = FakeDB(has_pool=True)
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().put(
                "/api/users/someone@utility.com", json={"role": "superadmin"},
                headers=as_(ADMIN))
        self.assertEqual(resp.status_code, 422, resp.text)
        # Rejected before any write.
        self.assertEqual([q for q in db.queries if "INSERT INTO app_users" in q], [])


class TestListReportsEnvAdmin(unittest.TestCase):
    """(d) An allowlist-admin email is reported is_env_admin."""

    def test_env_admin_flagged_on_row(self):
        db = FakeDB(has_pool=True)
        db.on("SELECT email, role, granted_by, created_at, updated_at",
              [Row(email=ADMIN, role="pm", granted_by="seed",
                   created_at=None, updated_at=None),
               Row(email="pm@utility.com", role="pm", granted_by=ADMIN,
                   created_at=None, updated_at=None)])

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().get("/api/users", headers=as_(ADMIN))

        self.assertEqual(resp.status_code, 200, resp.text)
        users = {u["email"]: u for u in resp.json()["users"]}
        self.assertIn(ADMIN, users)
        self.assertTrue(users[ADMIN]["is_env_admin"],
                        "the allowlisted email must be reported is_env_admin")
        self.assertFalse(users["pm@utility.com"]["is_env_admin"])

    def test_env_admin_with_no_row_is_still_listed(self):
        """An allowlist admin with no app_users row still appears as an env admin."""
        db = FakeDB(has_pool=True)
        db.on("SELECT email, role, granted_by, created_at, updated_at", [])

        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(users_route, "db", db),
        ):
            resp = make_client().get("/api/users", headers=as_(ADMIN))

        self.assertEqual(resp.status_code, 200, resp.text)
        users = {u["email"]: u for u in resp.json()["users"]}
        self.assertIn(ADMIN, users)
        self.assertTrue(users[ADMIN]["is_env_admin"])
        self.assertEqual(users[ADMIN]["role"], "admin")


if __name__ == "__main__":
    unittest.main()
