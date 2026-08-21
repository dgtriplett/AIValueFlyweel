"""The SPA catch-all must not serve files from outside frontend/dist.

WHY THIS FILE EXISTS
--------------------
`@app.get("/{full_path:path}")` matches every unmatched URL and captures `..`
happily. Joining that capture onto FRONTEND_DIST and returning a FileResponse was
an UNAUTHENTICATED arbitrary file read: `GET /..%2f..%2fapp.py` returned this
app's source, and the same request shape reaches anything the app process can read
— server/config.py, .git/config, /etc/passwd.

It is pre-auth because the catch-all is the app's front door: it is matched before
any account resolution or Databricks identity check runs. So this is the one route
where a containment bug is exploitable by anyone who can reach the URL at all.

The encoded variants matter as much as the plain one. `/../../app.py` is collapsed
by the HTTP client and by Starlette's router before the handler ever sees it, so it
never reproduced the bug — but `%2f` survives that normalization and arrives in
`full_path` intact. A test that only covered the plain spelling would have passed
against the vulnerable code.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Strings that appear in files the app must never serve. Checked against the
# response BODY, because the status code alone does not prove containment: the SPA
# fallback is a legitimate 200, and a leak is also a 200.
SECRET_MARKERS = (
    "FastAPI entry point",      # app.py docstring
    "Lakebase Postgres",        # server/db.py docstring
    "root:x:",                  # /etc/passwd
    "[core]",                   # .git/config
)

# Every spelling of "escape the directory" that survives some layer of
# normalization. %2f is the one that actually reproduced the original bug.
TRAVERSALS = (
    "/../../app.py",
    "/..%2f..%2fapp.py",
    "/%2e%2e/%2e%2e/app.py",
    "/%2e%2e%2f%2e%2e%2fapp.py",
    "/....//....//app.py",
    "/..%2f..%2f..%2fapp.py",
    "/..%2fserver%2fdb.py",
    "/..%2f..%2fserver%2fdb.py",
    "/..%2f..%2f.git%2fconfig",
    "/..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
    "/assets/..%2f..%2fapp.py",
    "/console/..%2f..%2f..%2fapp.py",
    "/..%5c..%5capp.py",        # backslash, in case of a Windows-style join
)


class TestSpaCatchAllContainment(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(appmod.app)

    def test_traversal_never_returns_file_contents(self):
        """The response may be the SPA or a 404 — it may never be a file."""
        for path in TRAVERSALS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (200, 404), path)
                for marker in SECRET_MARKERS:
                    self.assertNotIn(
                        marker, response.text,
                        f"{path} leaked a file outside frontend/dist "
                        f"(matched {marker!r})")

    def test_escape_attempts_return_404(self):
        """A rejected traversal is a 404, not a 200 with the SPA.

        Originally these fell through to the SPA index, which meant a probe got the
        same 200 as a real page: nothing in the response said "rejected", and access
        logs could not distinguish an attack from a deep link. The containment
        decision was already correct; only the reporting was misleading.
        """
        for path in ("/..%2f..%2fapp.py", "/%2e%2e%2f%2e%2e%2fapp.py",
                     "/..%2fserver%2fdb.py"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_escaping_paths_do_not_reveal_what_exists_outside(self):
        """No filesystem oracle AMONG escapes: real and fake targets look identical.

        This is the property that actually matters. `escapes_root` decides on the
        resolved path's SHAPE and never asks whether the target exists, so a path
        naming a real file outside the root is indistinguishable from one naming
        nothing. A caller learns "that was rejected", never "that file is there".
        """
        real_target = self.client.get("/..%2f..%2fapp.py")
        fake_target = self.client.get("/..%2f..%2fno-such-file-at-all.py")
        self.assertEqual(real_target.status_code, fake_target.status_code)
        self.assertEqual(real_target.text, fake_target.text)

    def test_client_side_routes_are_not_treated_as_escapes(self):
        """The 404 must apply to escapes only — deep links still render the SPA."""
        for path in ("/portfolio", "/knowledge/some-article", "/no-such-page"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200,
                                 "a normal client route must not 404")

    def test_real_spa_assets_are_still_served(self):
        """The fix must not break the thing the route is for."""
        dist = ROOT / "frontend" / "dist"
        if not (dist / "index.html").exists():
            self.skipTest("frontend/dist not built in this checkout")

        index = self.client.get("/index.html")
        self.assertEqual(index.status_code, 200)
        self.assertIn("<", index.text)

        assets = sorted((dist / "assets").glob("*.js")) if (dist / "assets").exists() else []
        if assets:
            served = self.client.get(f"/assets/{assets[0].name}")
            self.assertEqual(served.status_code, 200,
                             "a real built asset stopped being served")

    def test_client_routes_still_fall_through_to_the_spa(self):
        """A deep link the SPA owns must render the app, not 404."""
        response = self.client.get("/portfolio")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<", response.text)

    def test_api_paths_are_not_swallowed_by_the_catch_all(self):
        response = self.client.get("/api/definitely-not-a-route")
        self.assertEqual(response.status_code, 404)


class TestSafeStaticPath(unittest.TestCase):
    """Unit-level checks on the helper, including cases hard to send over HTTP."""

    def test_escaping_paths_return_none(self):
        root = ROOT / "frontend" / "dist"
        for requested in ("../app.py", "../../app.py", "../server/db.py",
                          "/etc/passwd", "..", "", "\x00app.py",
                          "subdir/../../../app.py"):
            with self.subTest(requested=requested):
                self.assertIsNone(appmod.safe_static_path(root, requested))

    def test_absolute_path_does_not_escape_the_root(self):
        """`Path("/a") / "/etc/passwd"` is `/etc/passwd` — the join discards the root."""
        self.assertIsNone(
            appmod.safe_static_path(ROOT / "frontend" / "dist", "/etc/passwd"))

    def test_directory_is_not_served_as_a_file(self):
        dist = ROOT / "frontend" / "dist"
        if not (dist / "assets").is_dir():
            self.skipTest("frontend/dist/assets not present")
        self.assertIsNone(appmod.safe_static_path(dist, "assets"))

    def test_contained_file_resolves(self):
        dist = ROOT / "frontend" / "dist"
        if not (dist / "index.html").exists():
            self.skipTest("frontend/dist not built in this checkout")
        resolved = appmod.safe_static_path(dist, "index.html")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "index.html")

    def test_symlink_out_of_the_root_is_rejected(self):
        """Collapsing `..` is not enough — a symlink escapes without any dots."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "dist"
            root.mkdir()
            secret = tmp_path / "secret.txt"
            secret.write_text("not for you")
            link = root / "escape.txt"
            try:
                link.symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported here")
            self.assertIsNone(appmod.safe_static_path(root, "escape.txt"))


if __name__ == "__main__":
    unittest.main()
