"""Destructive operational routes require an explicitly configured admin."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.routes import demo as demo_routes  # noqa: E402
from server.routes import genie as genie_routes  # noqa: E402

ADMIN = "ops@utility.com"
OUTSIDER = "intern@utility.com"


class DestructiveOpsAuthzTestCase(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(genie_routes.router, prefix="/api")
        app.include_router(demo_routes.router, prefix="/api")
        self.client = TestClient(app, raise_server_exceptions=False)

    @staticmethod
    def as_(email: str) -> dict[str, str]:
        return {"X-Forwarded-Email": email}


class TestGenieProvisionAuthz(DestructiveOpsAuthzTestCase):
    def test_non_admin_is_refused(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True):
            response = self.client.post(
                "/api/genie/provision", json={"sync_mirror": False},
                headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_empty_allowlist_fails_closed(self):
        with mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ""}, clear=True):
            response = self.client.post(
                "/api/genie/provision", json={"sync_mirror": False},
                headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 403)

    def test_admin_is_allowed_through(self):
        result = {"space_id": "space-123"}
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch("server.genie_provision.provision",
                       new=mock.AsyncMock(return_value=result)) as provision,
            mock.patch.object(genie_routes, "write_audit", new=mock.AsyncMock()),
        ):
            response = self.client.post(
                "/api/genie/provision",
                json={"title": "Portfolio", "sync_mirror": False},
                headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["space_id"], "space-123")
        provision.assert_awaited_once()


class TestDemoLoadAuthz(DestructiveOpsAuthzTestCase):
    def test_non_admin_is_refused(self):
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
        ):
            response = self.client.post("/api/demo/load", headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_empty_allowlist_fails_closed(self):
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ""}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
        ):
            response = self.client.post("/api/demo/load", headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 403)

    def test_admin_is_allowed_through(self):
        run_demo = mock.AsyncMock(return_value={"ok": True, "clean": False})
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
            mock.patch.object(demo_routes, "_run", new=run_demo),
        ):
            response = self.client.post("/api/demo/load", headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 200)
        run_demo.assert_awaited_once_with(clean=False)


class TestDemoResetAuthz(DestructiveOpsAuthzTestCase):
    def test_non_admin_is_refused(self):
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
        ):
            response = self.client.post("/api/demo/reset", headers=self.as_(OUTSIDER))
        self.assertEqual(response.status_code, 403)

    def test_empty_allowlist_fails_closed(self):
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ""}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
        ):
            response = self.client.post("/api/demo/reset", headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 403)

    def test_admin_is_allowed_through(self):
        run_demo = mock.AsyncMock(return_value={"ok": True, "clean": True})
        with (
            mock.patch.dict(os.environ, {"GRID_ATLAS_ADMINS": ADMIN}, clear=True),
            mock.patch.object(demo_routes, "DEMO_MODE_ENABLED", True),
            mock.patch.object(demo_routes, "_run", new=run_demo),
        ):
            response = self.client.post("/api/demo/reset", headers=self.as_(ADMIN))
        self.assertEqual(response.status_code, 200)
        run_demo.assert_awaited_once_with(clean=True)


if __name__ == "__main__":
    unittest.main()
