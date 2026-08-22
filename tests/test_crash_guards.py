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
import ast
import io
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
from server.routes import accounts as accounts_route  # noqa: E402
from server.routes import (impact, knowledge, onboarding, proposals,  # noqa: E402
                           sync_packages, use_cases)
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


class AccountScopedDataSourceExport(unittest.TestCase):
    """Data Sources export must use the selected account's status position."""

    ASSET = dict(id=1, source_category="SCADA", module="m", vendor="v")

    def _export(self, db, account_id):
        with mock.patch.object(onboarding, "db", db), \
             mock.patch.object(onboarding.accounts, "current",
                               mock.AsyncMock(return_value=account_id)):
            return load_workbook_bytes(run(collect_body(onboarding.export_template())))

    def test_selected_account_status_overrides_default_fallback(self):
        class AccountAwareDB(FakeDB):
            async def fetch(self, sql, *args):
                self.queries.append(sql)
                if "FROM data_assets da" in sql:
                    self.assert_account(args)
                    return [Row(**AccountScopedDataSourceExport.ASSET,
                                ingestion_status="governed")]
                return []

            @staticmethod
            def assert_account(args):
                if args != (22,):
                    raise AssertionError(f"expected selected account 22, got {args}")

        db = AccountAwareDB(has_pool=True)
        wb = self._export(db, account_id=22)

        self.assertEqual("governed", wb["Data Sources"]["E2"].value)
        scoped_query = next(q for q in db.queries if "FROM data_assets da" in q)
        self.assertIn("LEFT JOIN asset_status_by_account", scoped_query)
        self.assertIn("s.account_id = $1", scoped_query)
        self.assertIn("COALESCE(s.ingestion_status, 'not_started')", scoped_query)

    def test_none_account_uses_raw_default_fallback(self):
        db = FakeDB(has_pool=True).on(
            "FROM data_assets ORDER BY",
            [Row(**self.ASSET, ingestion_status="landed")])

        wb = self._export(db, account_id=None)

        self.assertEqual("landed", wb["Data Sources"]["E2"].value)
        asset_query = next(q for q in db.queries if "FROM data_assets" in q)
        self.assertNotIn("asset_status_by_account", asset_query)
        self.assertIn("ingestion_status FROM data_assets", asset_query)


class AccountScopedUseCaseWorkbook(unittest.TestCase):
    """Catalog rows are shared; custom use cases stay inside their portfolio."""

    CATALOG = Row(id=1, title="Shared Catalog", domain="Operations", phase=1,
                  status="not_started", priority_score=1, value_mm=1.0)
    ACCOUNT_A = Row(id=2, title="Project Nightingale (Tenant A confidential)",
                    domain="Operations", phase=1, status="scoping",
                    priority_score=2, value_mm=2.0)
    ACCOUNT_B = Row(id=3, title="Tenant B Custom", domain="Operations", phase=1,
                    status="in_progress", priority_score=3, value_mm=3.0)
    ALL_ROWS = [CATALOG, ACCOUNT_A, ACCOUNT_B]

    class VisibilityDB(FakeDB):
        def __init__(self):
            super().__init__(has_pool=True)
            self.calls = []

        async def fetch(self, sql, *args):
            self.queries.append(sql)
            self.calls.append((sql, args))
            if "FROM use_cases" not in sql:
                return []
            scoped = ("origin = 'catalog'" in sql
                      and "account_portfolio_use_cases" in sql
                      and args == (22,))
            if scoped:
                return [AccountScopedUseCaseWorkbook.CATALOG,
                        AccountScopedUseCaseWorkbook.ACCOUNT_B]
            return AccountScopedUseCaseWorkbook.ALL_ROWS

    @staticmethod
    def _parsed_use_cases():
        return {
            "data_sources": [],
            "use_cases": [
                {"id": row["id"], "status": row["status"], "priority": None,
                 "value_base_mm": None, "notes": None}
                for row in AccountScopedUseCaseWorkbook.ALL_ROWS
            ],
            "assumptions": [],
        }

    def _run_for_account(self, account_id):
        db = self.VisibilityDB()
        with mock.patch.object(onboarding, "db", db), \
             mock.patch.object(onboarding.accounts, "current",
                               mock.AsyncMock(return_value=account_id)):
            wb = load_workbook_bytes(run(collect_body(onboarding.export_template())))
            changes, errors = run(onboarding._compute_import(self._parsed_use_cases()))
        return db, wb, changes, errors

    def test_account_sees_catalog_and_own_custom_in_export_and_import(self):
        db, wb, changes, errors = self._run_for_account(22)

        titles = [row[1].value for row in wb["Use Cases"].iter_rows(min_row=2)]
        self.assertEqual(["Shared Catalog", "Tenant B Custom"], titles)
        self.assertNotIn("Project Nightingale (Tenant A confidential)", titles)
        self.assertEqual([], changes["use_cases"])
        self.assertEqual(["Use case id 2 not found"], errors)

        use_case_calls = [(sql, args) for sql, args in db.calls
                          if "FROM use_cases" in sql]
        self.assertEqual(2, len(use_case_calls), "expected export + import queries")
        for sql, args in use_case_calls:
            self.assertIn("origin = 'catalog'", sql)
            self.assertIn("account_portfolio_use_cases", sql)
            self.assertIn("ap.account_id = $1", sql)
            self.assertEqual((22,), args)

    def test_none_account_keeps_export_and_import_unscoped(self):
        db, wb, changes, errors = self._run_for_account(None)

        titles = [row[1].value for row in wb["Use Cases"].iter_rows(min_row=2)]
        self.assertEqual([row["title"] for row in self.ALL_ROWS], titles)
        self.assertEqual([], changes["use_cases"])
        self.assertEqual([], errors)

        use_case_calls = [(sql, args) for sql, args in db.calls
                          if "FROM use_cases" in sql]
        self.assertEqual(2, len(use_case_calls), "expected export + import queries")
        for sql, args in use_case_calls:
            self.assertNotIn("account_portfolio_use_cases", sql)
            self.assertNotIn("origin = 'catalog'", sql)
            self.assertEqual((), args)


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
        # Unicode digits: `isdigit()` is True for these, so the first version of
        # the fix let them reach int() — see UnicodeDigitNodeIds below.
        "asset-²",   # superscript two: int() raises ValueError -> was a 500
        "uc-٣",      # Arabic-Indic three: int() ACCEPTS it -> was a silent 200
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


class UnicodeDigitNodeIds(unittest.TestCase):
    """`str.isdigit()` is not the question "does int() accept this".

    Found by cross-review after the first fix, which gated on `raw.isdigit()` and
    then called `int(raw)` believing the check made it safe. The two cases fail in
    OPPOSITE directions, which is why neither a bare isdigit() nor a try/except
    around int() is sufficient on its own:

      * '²'.isdigit() is True and int('²') raises ValueError — still a 500.
      * '٣'.isdigit() is True and int('٣') returns 3 — no crash, but `uc-٣` would
        silently resolve to use case 3. An id the app never generates should be
        refused, not reinterpreted, so try/except alone would trade a crash for a
        wrong answer.

    Requiring ASCII digits answers both.
    """

    def test_isdigit_alone_would_not_have_been_safe(self):
        """The premise, asserted so this test explains itself if it ever fails."""
        self.assertTrue("²".isdigit())
        with self.assertRaises(ValueError):
            int("²")
        self.assertTrue("٣".isdigit())
        self.assertEqual(3, int("٣"))  # accepted — hence the isascii() requirement

    def test_superscript_digit_is_422_not_500(self):
        resp = client().get("/api/impact/asset-²")
        self.assertEqual(422, resp.status_code, resp.text[:200])

    def test_arabic_indic_digit_is_refused_not_reinterpreted(self):
        resp = client().get("/api/impact/uc-٣")
        self.assertEqual(422, resp.status_code, resp.text[:200])

    def test_parser_rejects_non_ascii_digits(self):
        for raw in ("asset-²", "uc-٣", "lob-٣", "asset-１"):  # incl. fullwidth one
            self.assertIsNone(impact._split_node_id(raw), raw)

    def test_ascii_digits_still_parse(self):
        self.assertEqual(("asset", 12), impact._split_node_id("asset-12"))


class OverLongDigitNodeIds(unittest.TestCase):
    """A digit run can be all-ASCII, all-digits, and still not convertible.

    The third thing `int()` refuses, after non-digits and non-ASCII digits: CPython
    caps integer string conversion at `sys.get_int_max_str_digits()` — 4300 by
    default since 3.11, itself a DoS guard — so a 5000-digit id passed
    `isascii() and isdigit()` and raised ValueError anyway. `GET /api/impact/uc-<5000
    nines>` was a 500 that any unauthenticated caller could produce at will.

    Two layers close it: a length bound (no real bigint serial has 20 digits) and a
    try/except around the conversion. The bound alone would not be correct — the
    limit is settable at runtime via `sys.set_int_max_str_digits()`, so a bound
    picked against today's default is a heuristic. `test_try_except_is_load_bearing`
    below proves the net actually carries weight rather than documenting intent.
    """

    LENGTHS = (20, 4301, 5000)

    def test_over_long_ids_are_422_not_500(self):
        c = client()
        for kind in ("uc", "asset", "lob"):
            for length in self.LENGTHS:
                with self.subTest(kind=kind, digits=length):
                    resp = c.get(f"/api/impact/{kind}-" + "9" * length)
                    self.assertEqual(422, resp.status_code,
                                     f"{kind}- with {length} digits returned "
                                     f"{resp.status_code}")

    def test_the_premise(self):
        """int() really does reject what isascii()+isdigit() accepted."""
        raw = "9" * 5000
        self.assertTrue(raw.isascii() and raw.isdigit())
        with self.assertRaises(ValueError):
            int(raw)

    def test_plausible_ids_still_parse(self):
        """The bound must not reject an id the database could actually hold.

        19 digits is the width of a bigint, so it has to survive; a real id never
        gets near it, but a bound that clipped valid ids would be a worse bug than
        the one being fixed.
        """
        self.assertEqual(("uc", int("9" * 19)),
                         impact._split_node_id("uc-" + "9" * 19))
        self.assertEqual(("asset", 12), impact._split_node_id("asset-12"))

    def test_try_except_is_load_bearing(self):
        """With the limit lowered under it, the parser still must not raise.

        This is the case the length bound cannot cover: the conversion limit is
        settable at runtime, so lower it and an id that clears the bound becomes
        unconvertible anyway. With the try/except removed and only the bound kept,
        this raises ValueError instead of returning None — so the test fails if the
        net is ever dropped as redundant.

        640 is the smallest value CPython accepts, which is still far above
        `_MAX_ID_DIGITS`. So the id below is raised past the *interpreter* limit
        while the bound is temporarily widened past it too, isolating the
        conversion as the thing that fails.
        """
        original = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(640)
        self.addCleanup(sys.set_int_max_str_digits, original)

        raw = "9" * 700
        self.assertTrue(raw.isascii() and raw.isdigit())
        with self.assertRaises(ValueError):
            int(raw)

        with mock.patch.object(impact, "_MAX_ID_DIGITS", 1000):
            # Clears the (widened) bound, so only the try/except can catch it.
            self.assertIsNone(impact._split_node_id("uc-" + raw))


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

    def test_create_account(self):
        """Missed by the first sweep's hard-coded module list.

        `require_admin` is patched because this asserts the write guard, not the
        admin gate — which is tested for real in test_account_authz.py, and which
        the guard deliberately sits AFTER.
        """
        with mock.patch.object(accounts_route, "db", self.db), \
             mock.patch.object(accounts_route, "write_audit", mock.AsyncMock()), \
             mock.patch.object(accounts_route.acct, "require_admin",
                               return_value="admin@example.com"), \
             mock.patch.object(accounts_route.acct, "invalidate_default"):
            self.assert503(accounts_route.create_account(
                accounts_route.AccountIn(name="Northern Grid"), self.req))

    def test_sync_packages_lookup_or_create_use_case(self):
        """Also missed by the first sweep — an import that silently 500'd."""
        with mock.patch.object(sync_packages, "db", self.db), \
             mock.patch.object(sync_packages.accounts, "current",
                               mock.AsyncMock(return_value=None)):
            self.assert503(sync_packages._lookup_or_create_use_case(
                {"title": "Outage Prediction", "id": "ext-1"}, "maturity", "actor"))


class ReturningSitesAreGuarded(unittest.TestCase):
    """Every `fetchrow(... RETURNING ...)` checks its result before using it.

    The per-site tests above cover the sites the audit named. This is the net for
    the ones nobody has written a test for, re-derived from the source so a NEW
    unguarded write fails here rather than in the next audit.

    It GLOBS the source tree. The first version of this sweep took a hard-coded
    list of module names copied from the audit's grep, which made it worth almost
    nothing: `accounts.py` and `sync_packages.py` were not on that list, both had
    an unguarded write, and the sweep reported a clean tree while cross-review
    found them by globbing. A completeness check that needs a human to remember to
    extend it is a check that silently narrows as the codebase grows.
    """

    def source_files(self):
        """Every module that could contain a write, discovered not enumerated."""
        server = pathlib.Path(__file__).resolve().parent.parent / "server"
        return sorted(set(server.glob("*.py")) | set(server.glob("routes/*.py")))

    def unguarded_sites(self):
        sites, scanned = [], 0
        for path in self.source_files():
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
                scanned += 1
                var = node.targets[0].id
                if not self._guarded(var, lines[node.end_lineno:node.end_lineno + 14]):
                    sites.append(f"{path.name}:{node.lineno} ({var})")
        return sites, scanned

    def test_no_unguarded_returning_write(self):
        unguarded, _ = self.unguarded_sites()
        self.assertEqual([], unguarded, "unguarded RETURNING write(s): "
                                        + ", ".join(unguarded))

    def test_sweep_actually_reaches_the_writes(self):
        """The sweep must be scanning a real corpus, not silently matching nothing.

        `test_no_unguarded_returning_write` passes just as happily on zero sites as
        on all of them, so a glob typo or an AST-shape change would turn it green
        and useless. This pins the floor and names the two files whose omission was
        the actual defect.
        """
        _, scanned = self.unguarded_sites()
        self.assertGreater(scanned, 50,
                           f"sweep found only {scanned} RETURNING writes — the "
                           f"tree has ~60, so the glob or the AST match is broken")

        names = {path.name for path in self.source_files()}
        for required in ("accounts.py", "sync_packages.py"):
            self.assertIn(required, names,
                          f"{required} is outside the sweep — it was missed once "
                          f"already and had an unguarded write")

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
