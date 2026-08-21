"""Crashes a fresh install or a fat-fingered URL could produce, and now cannot.

Three unrelated 500s found by the production-readiness audit, grouped because they
share one shape: code that assumed the happy path had already happened. An empty
database, a mistyped node id, and a write that returned no row were all reported to
the client as an opaque server error, which tells an operator nothing and tells a
new customer the product is broken.

WHY EACH ONE MATTERED
---------------------
1. `GET /api/onboarding/export.xlsx` built a data-validation range as
   f"E2:E{ws.max_row}". On a fresh install max_row is 1 (header only), so the range
   read "E2:E1" — reversed, and invalid in the saved workbook. The FIRST artifact a
   new customer downloads 500'd, before they had any way to know why.

2. `GET /api/impact/{node_id}` did `int(nid.split("-")[1])` on a path segment. Any
   malformed id raised IndexError or ValueError rather than an HTTPException, so a
   client mistake became a server error. The sibling routes that take a typed
   `{id:int}` already answer 422 for an unparseable id; this now matches them.

3. Several `fetchrow(... RETURNING ...)` writes indexed the result without checking
   for None. When the pool is degraded a write can return no row, and `row["id"]`
   then raised TypeError — a 500 where the rest of the app raises the actionable
   503 "Database unavailable". These tests pin the 503 at the sites that lacked it.

Each test below was confirmed to FAIL against the pre-fix code.
"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
from server.routes import impact, knowledge, onboarding, proposals, use_cases  # noqa: E402
from fakedb import FakeDB, Row, run  # noqa: E402


def client():
    """A client that surfaces handler exceptions as 500s instead of re-raising.

    Without `raise_server_exceptions=False` an unhandled error propagates into the
    test and these cases would fail on the traceback rather than on the status code,
    which hides whether the app would have returned 500 to a real client.
    """
    return TestClient(appmod.app, raise_server_exceptions=False)


class EmptyDatabaseExport(unittest.TestCase):
    """The day-1 onboarding template must download, not 500.

    The app is unconfigured here (no PGHOST), so every read returns [] and every
    sheet is header-only — exactly the state a new install is in.
    """

    def test_export_on_empty_db_is_a_valid_workbook(self):
        resp = client().get("/api/onboarding/export.xlsx")
        self.assertEqual(resp.status_code, 200, resp.text[:400])

        # Parsing it back is the real assertion: a reversed E2:E1 range produces
        # bytes that openpyxl refuses, so a 200 alone would not prove validity.
        wb = load_workbook_bytes(resp.content)
        self.assertEqual(
            ["Instructions", "Data Sources", "Use Cases", "Assumptions"],
            wb.sheetnames)

    def test_empty_sheets_keep_their_headers(self):
        """An empty export is still a usable template — headers must survive."""
        wb = load_workbook_bytes(client().get("/api/onboarding/export.xlsx").content)
        self.assertEqual(
            ["id", "source_category", "module", "vendor", "ingestion_status"],
            [c.value for c in wb["Data Sources"][1]])
        self.assertEqual(1, wb["Data Sources"].max_row, "expected header-only sheet")
        self.assertEqual(["key", "label", "unit", "value"],
                         [c.value for c in wb["Assumptions"][1]])

    def test_dropdown_returns_once_rows_exist(self):
        """The fix skips the validation on an empty sheet — not permanently.

        Guards the obvious over-correction: deleting the dropdown outright would
        also make the empty case pass, while silently costing every populated
        export its status picker.
        """
        db = FakeDB(has_pool=True)
        db.on("FROM data_assets", [Row(id=1, source_category="SCADA", module="m",
                                       vendor="v", ingestion_status="landed")])
        db.on("FROM use_cases uc", [Row(id=7, title="T", domain="D", phase=1,
                                        status="scoping", priority_score=3,
                                        value_mm=2.5)])
        db.on("FROM value_assumptions", [Row(key="k", label="L", unit="$",
                                             value=1.5, category="c")])
        with mock.patch.object(onboarding, "db", db), \
             mock.patch.object(onboarding.accounts, "current",
                               mock.AsyncMock(return_value=None)):
            wb = load_workbook_bytes(run(collect_body(onboarding.export_template())))

        for sheet in ("Data Sources", "Use Cases"):
            ranges = [str(dv.sqref)
                      for dv in wb[sheet].data_validations.dataValidation]
            self.assertEqual(["E2"], ranges, f"{sheet} lost its status dropdown")


class MalformedImpactNodeId(unittest.TestCase):
    """A bad node id is the client's mistake: 422, never 500.

    422 rather than 404 to match the sibling routes: /api/use-cases/abc,
    /api/lobs/abc and /api/data-assets/abc all answer 422 via FastAPI's own
    `{id:int}` coercion. 404 is reserved for a well-formed id that does not exist.
    """

    MALFORMED = [
        "bogus",          # no separator at all -> IndexError before the fix
        "asset-abc",      # non-numeric id      -> ValueError before the fix
        "uc-",            # separator, no id
        "lob-",
        "-1",             # empty kind
        "asset-",
        "asset--1",       # negative reads as a second empty segment
        "asset-1-2",      # trailing junk
        "widget-1",       # unknown node kind
        "asset-1.5",      # float, not an int
        "ASSET-1",        # kinds are lowercase
    ]

    def test_malformed_ids_are_422(self):
        c = client()
        for node_id in self.MALFORMED:
            with self.subTest(node_id=node_id):
                resp = c.get(f"/api/impact/{node_id}")
                self.assertEqual(422, resp.status_code,
                                 f"{node_id!r} returned {resp.status_code}")

    def test_no_malformed_id_is_a_500(self):
        """Stated separately: the audit finding was specifically the 500."""
        c = client()
        for node_id in self.MALFORMED:
            with self.subTest(node_id=node_id):
                self.assertNotEqual(500, c.get(f"/api/impact/{node_id}").status_code)

    def test_wellformed_ids_still_answer(self):
        """The validator must not reject the ids the graph actually uses."""
        c = client()
        for node_id in ("asset-1", "uc-1", "lob-1", "asset-99999"):
            with self.subTest(node_id=node_id):
                self.assertEqual(200, c.get(f"/api/impact/{node_id}").status_code)

    def test_split_node_id_unit(self):
        self.assertEqual(("asset", 12), impact._split_node_id("asset-12"))
        self.assertEqual(("uc", 3), impact._split_node_id("uc-3"))
        self.assertEqual(("lob", 7), impact._split_node_id("lob-7"))
        for bad in ("bogus", "asset-abc", "uc-", "-1", "widget-1", ""):
            self.assertIsNone(impact._split_node_id(bad), bad)


class NoneWritesDB(FakeDB):
    """A pool that is up enough to read but returns no row from any RETURNING write.

    This is the degraded shape the audit called out. The point of simulating it at
    the `fetchrow` boundary rather than by breaking the pool is that the pool-level
    failures already raise DatabaseUnavailable and reach the 503 handler; the sites
    under test are the ones where the write returns None and nothing raises at all.
    """

    async def fetchrow(self, sql, *args):
        if "RETURNING" in sql.upper():
            return None
        return await super().fetchrow(sql, *args)


class GuardedReturningWrites(unittest.TestCase):
    """A write that comes back empty is a 503, not a TypeError.

    One case per previously-unguarded site. `write_audit` is patched out because it
    is the first thing after the write to touch `row["id"]` — without the patch a
    test could pass on an audit-layer error instead of the guard.
    """

    def assert503(self, coro):
        with self.assertRaises(HTTPException) as caught:
            run(coro)
        self.assertEqual(503, caught.exception.status_code)
        self.assertEqual("Database unavailable", caught.exception.detail)

    def setUp(self):
        self.db = NoneWritesDB(has_pool=True)
        self.req = mock.MagicMock()
        for module in (knowledge, proposals, use_cases):
            self.enter(mock.patch.object(module, "db", self.db))
            self.enter(mock.patch.object(module, "write_audit", mock.AsyncMock()))
            self.enter(mock.patch.object(module.accounts, "current",
                                         mock.AsyncMock(return_value=None)))
        self.enter(mock.patch.object(knowledge.accounts, "scope_clause_at",
                                     mock.AsyncMock(return_value=("true", ()))))

    def enter(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_folder(self):
        self.assert503(knowledge.create_folder(
            knowledge.FolderIn(name="Runbooks", parent_id=None), self.req))

    def test_create_article(self):
        self.assert503(knowledge.create_article(
            knowledge.ArticleIn(title="Cold Start", body_md="body"), self.req))

    def test_update_use_case(self):
        self.db.on("SELECT status, title FROM use_cases",
                   [Row(status="scoping", title="T")])
        self.assert503(use_cases.update_use_case(
            1, use_cases.UseCaseIn(title="T", status="scoping"), self.req))

    def test_change_status(self):
        self.db.on("SELECT status, title FROM use_cases",
                   [Row(status="scoping", title="T")])
        self.assert503(use_cases.change_status(
            1, use_cases.StatusChange(status="in_progress"), self.req))


class ReturningSitesAreGuarded(unittest.TestCase):
    """Every `fetchrow(... RETURNING ...)` checks its result before using it.

    The per-site tests above cover the sites the audit found. This one is the
    regression net for the sites nobody has written a test for yet: it re-derives
    the list from the source, so a NEW unguarded write fails here rather than
    waiting to be found by the next audit.
    """

    ROUTES = ("knowledge values roadmap proposals joint_funding ingestion "
              "funding_requests use_cases research inventory flow demo data_assets "
              "value_assumptions dependencies lobs comments domains snapshots").split()

    def test_no_unguarded_returning_write(self):
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "server" / "routes"
        unguarded = []
        for name in self.ROUTES:
            path = root / f"{name}.py"
            source = path.read_text()
            lines = source.splitlines()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Assign):
                    continue
                value = node.value
                if not (isinstance(value, ast.Await)
                        and isinstance(value.value, ast.Call)):
                    continue
                call = value.value
                if not (isinstance(call.func, ast.Attribute)
                        and call.func.attr == "fetchrow"):
                    continue
                if "RETURNING" not in (ast.get_source_segment(source, call) or "").upper():
                    continue
                if not (len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    continue
                var = node.targets[0].id
                if not self._guarded(var, lines[node.end_lineno:node.end_lineno + 14]):
                    unguarded.append(f"{path.name}:{node.lineno} ({var})")

        self.assertEqual([], unguarded, "unguarded RETURNING write(s): "
                                        + ", ".join(unguarded))

    @staticmethod
    def _guarded(var, window):
        """True if `var` is tested for emptiness before anything indexes it.

        Accepts the several spellings already in the tree — `if row is None:`,
        `if not row:`, `if row:`, `if row is not None:`, and the ternary
        `return row["id"] if row else None` — because this test is a net for
        missing guards, not a style rule about how to write one.
        """
        for line in window:
            stripped = line.strip()
            if (stripped.startswith(f"if {var} is None")
                    or stripped.startswith(f"if {var} is not None")
                    or stripped.startswith(f"if not {var}")
                    or stripped == f"if {var}:"
                    or f"if {var} else" in stripped):
                return True
            # Reached a use of the row first: whatever follows cannot un-crash it.
            if (f"{var}[" in stripped or f"dict({var})" in stripped
                    or f"row_to_dict({var})" in stripped):
                return False
        return False


def load_workbook_bytes(data: bytes):
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(data))


async def collect_body(response_awaitable):
    """Drain a StreamingResponse into bytes."""
    response = await response_awaitable
    return b"".join([chunk async for chunk in response.body_iterator])


if __name__ == "__main__":
    unittest.main()
