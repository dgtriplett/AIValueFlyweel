"""User role resolution must be fail-closed and respect the allowlist.

THE FAILURE THIS GUARDS
-----------------------
A role-resolution failure (DB error, missing table) must NEVER escalate to admin.
The documented trust boundary:
  * GRID_ATLAS_ADMINS allowlist ALWAYS grants 'admin' (bootstrap, lockout-proof).
  * app_users table holds granted roles for everyone else.
  * Unknown users default to 'pm'.
  * Any DB error -> 'pm' (never admin).

WHAT THIS TESTS
---------------
  (a) resolve_role returns 'admin' for an allowlisted email even with no app_users row.
  (b) Returns the stored app_users.role for a non-allowlisted email.
  (c) Defaults 'pm' for unknown users.
  (d) /api/me returns role + correct is_exec_locked.
  (e) Fail-closed: DB errors never grant admin.
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
from server.routes import me as me_route  # noqa: E402
from fakedb import FakeDB, Row, run  # noqa: E402

ADMIN = "ops@utility.com"
PM_USER = "pm@utility.com"
EXEC_USER = "exec@utility.com"


class FakeRequest:
    """Just the header bag the authz helpers read."""
    def __init__(self, headers: dict | None = None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class TestRoleResolution(unittest.TestCase):
    """Test resolve_role logic in isolation."""
    
    def setUp(self):
        """Reset DB state and set up allowlist."""
        run("DROP TABLE IF EXISTS app_users")
        run("""
            CREATE TABLE app_users (
              email        TEXT PRIMARY KEY,
              role         TEXT NOT NULL DEFAULT 'pm' CHECK (role IN ('admin','pm','executive')),
              granted_by   TEXT,
              created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_allowlisted_email_always_admin(self):
        """Allowlisted email returns 'admin' even with no app_users row."""
        req = FakeRequest({"X-Forwarded-Email": ADMIN})
        role = run(acct.resolve_role(ADMIN, req))
        self.assertEqual(role, 'admin')
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_stored_role_for_non_allowlisted(self):
        """Non-allowlisted email returns stored app_users.role."""
        run("INSERT INTO app_users (email, role) VALUES ($1, $2)", PM_USER, 'pm')
        req = FakeRequest({"X-Forwarded-Email": PM_USER})
        role = run(acct.resolve_role(PM_USER, req))
        self.assertEqual(role, 'pm')
        
        # Try executive too
        run("INSERT INTO app_users (email, role) VALUES ($1, $2)", EXEC_USER, 'executive')
        req_exec = FakeRequest({"X-Forwarded-Email": EXEC_USER})
        role_exec = run(acct.resolve_role(EXEC_USER, req_exec))
        self.assertEqual(role_exec, 'executive')
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_unknown_user_defaults_pm(self):
        """Unknown user (no app_users row) defaults to 'pm'."""
        req = FakeRequest({"X-Forwarded-Email": "unknown@utility.com"})
        role = run(acct.resolve_role("unknown@utility.com", req))
        self.assertEqual(role, 'pm')
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_unauthenticated_defaults_pm(self):
        """Unauthenticated (email=None) defaults to 'pm'."""
        req = FakeRequest({})
        role = run(acct.resolve_role(None, req))
        self.assertEqual(role, 'pm')
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_db_error_fails_closed(self):
        """DB error during role resolution must NOT grant admin (fall back to 'pm')."""
        # Drop the table to simulate a pre-migration or error state
        run("DROP TABLE IF EXISTS app_users")
        req = FakeRequest({"X-Forwarded-Email": PM_USER})
        role = run(acct.resolve_role(PM_USER, req))
        # Must NOT be 'admin' — fail closed to 'pm'
        self.assertEqual(role, 'pm')


class TestMeEndpoint(unittest.TestCase):
    """Test GET /api/me returns role + correct is_exec_locked."""
    
    def setUp(self):
        """Set up test client and DB."""
        run("DROP TABLE IF EXISTS app_users")
        run("""
            CREATE TABLE app_users (
              email        TEXT PRIMARY KEY,
              role         TEXT NOT NULL DEFAULT 'pm' CHECK (role IN ('admin','pm','executive')),
              granted_by   TEXT,
              created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        
        app = FastAPI()
        app.include_router(me_route.router, prefix="/api")
        self.client = TestClient(app)
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_me_returns_role_admin(self):
        """GET /api/me returns role='admin' for allowlisted email."""
        resp = self.client.get("/api/me", headers={"X-Forwarded-Email": ADMIN})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], ADMIN)
        self.assertTrue(data["is_admin"])
        self.assertEqual(data["role"], "admin")
        self.assertFalse(data["is_exec_locked"])  # admins are never locked
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_me_returns_role_pm(self):
        """GET /api/me returns role='pm' for a PM user."""
        run("INSERT INTO app_users (email, role) VALUES ($1, $2)", PM_USER, 'pm')
        resp = self.client.get("/api/me", headers={"X-Forwarded-Email": PM_USER})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], PM_USER)
        self.assertFalse(data["is_admin"])
        self.assertEqual(data["role"], "pm")
        self.assertFalse(data["is_exec_locked"])
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_me_returns_role_executive_locked(self):
        """GET /api/me returns role='executive' and is_exec_locked=true."""
        run("INSERT INTO app_users (email, role) VALUES ($1, $2)", EXEC_USER, 'executive')
        resp = self.client.get("/api/me", headers={"X-Forwarded-Email": EXEC_USER})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], EXEC_USER)
        self.assertFalse(data["is_admin"])
        self.assertEqual(data["role"], "executive")
        self.assertTrue(data["is_exec_locked"])  # executive + not admin = locked
    
    @mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True)
    def test_me_admin_with_executive_role_not_locked(self):
        """Admin with executive role in app_users is NOT locked (allowlist wins)."""
        # Edge case: an admin has a 'executive' row in app_users
        run("INSERT INTO app_users (email, role) VALUES ($1, $2)", ADMIN, 'executive')
        resp = self.client.get("/api/me", headers={"X-Forwarded-Email": ADMIN})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["is_admin"])
        self.assertEqual(data["role"], "admin")  # allowlist wins over stored role
        self.assertFalse(data["is_exec_locked"])  # admins are never locked
    
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_me_unauthenticated(self):
        """GET /api/me with no identity returns email=null, role='pm'."""
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["email"])
        self.assertFalse(data["is_admin"])
        self.assertEqual(data["role"], "pm")
        self.assertFalse(data["is_exec_locked"])


if __name__ == '__main__':
    unittest.main()
