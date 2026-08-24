"""Tests for GET /api/me — the persona/role endpoint.

Phase 1 establishes the identity + role structure other phases build on:
  * email: the trusted platform identity (X-Forwarded-*), or null when local.
  * is_admin: whether email is in GRID_ATLAS_ADMINS.
  * is_exec_locked: HARDCODED false in Phase 1; Phase 2+ reads from a table.

The endpoint is unauthenticated by design — it reports who you are, including
"nobody" (email: null). A 403 would make it impossible to discover you're not
authenticated.
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

ADMIN = "ops@utility.com"
NON_ADMIN = "reader@utility.com"


def client():
    """A test client that surfaces exceptions as 500s."""
    return TestClient(appmod.app, raise_server_exceptions=False)


class TestMeEndpoint(unittest.TestCase):
    """The /api/me endpoint reports identity + role flags."""

    def test_admin_identity_is_recognized(self):
        """Admin on the allowlist -> is_admin: true."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            resp = client().get("/api/me", headers={"X-Forwarded-Email": ADMIN})
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["email"], ADMIN)
            self.assertTrue(body["is_admin"])
            self.assertFalse(body["is_exec_locked"])

    def test_non_admin_identity_is_not_admin(self):
        """Authenticated but not on the allowlist -> is_admin: false."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            resp = client().get("/api/me", headers={"X-Forwarded-Email": NON_ADMIN})
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["email"], NON_ADMIN)
            self.assertFalse(body["is_admin"])
            self.assertFalse(body["is_exec_locked"])

    def test_no_identity_returns_null_not_500(self):
        """No trusted header -> email: null, is_admin: false, no error."""
        with mock.patch.dict(os.environ, {}, clear=True):
            resp = client().get("/api/me")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertIsNone(body["email"])
            self.assertFalse(body["is_admin"])
            self.assertFalse(body["is_exec_locked"])

    def test_client_settable_header_is_not_an_identity(self):
        """X-Grid-Atlas-User is spoofable; it must not grant admin."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            resp = client().get("/api/me", headers={"X-Grid-Atlas-User": ADMIN})
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            # The client-settable header is ignored for identity.
            self.assertIsNone(body["email"])
            self.assertFalse(body["is_admin"])

    def test_is_exec_locked_is_false_in_phase_1(self):
        """Phase 1 hardcodes is_exec_locked: false; Phase 2+ reads from a table."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            resp = client().get("/api/me", headers={"X-Forwarded-Email": ADMIN})
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            # The field exists NOW so frontend can consume it; the backend will
            # wire the table query when the table arrives (Phase 2+).
            self.assertFalse(body["is_exec_locked"])

    def test_identity_is_normalized(self):
        """Email is lowercased and trimmed."""
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            resp = client().get("/api/me", headers={"X-Forwarded-Email": " OPS@Utility.COM "})
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["email"], ADMIN)
            self.assertTrue(body["is_admin"])


if __name__ == "__main__":
    unittest.main()
