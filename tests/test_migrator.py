"""Migration versioning: apply-once, ordering, and drift detection.

WHY THESE TESTS MATTER MORE THAN MOST
------------------------------------
The bug this module replaced was silent: startup concatenated every *.sql and
re-executed the lot on each boot, which is harmless until a migration isn't
idempotent — then a restart destroys customer data with no error anywhere. So the
properties pinned here are exactly the ones whose failure would be invisible:

  - a file is applied AT MOST once (the actual fix)
  - drift ABORTS rather than guessing, and applies nothing
  - checksums ignore formatting churn but catch real edits
  - unorderable filenames are rejected up front, because ordering is load-bearing
  - startup_check NEVER applies anything (the app SP has no DDL)
"""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import Row, run  # noqa: E402

from server import migrator  # noqa: E402
from server.migrator import MigrationError, checksum, discover  # noqa: E402


class LedgerDB:
    """A fake that actually tracks the ledger, so apply-once is observable.

    FakeDB routes by substring and returns canned rows; that can't express "this
    row exists only after it was inserted", which is the whole property under test.
    This one keeps a dict and records executed SQL.
    """

    def __init__(self, *, ledger: dict[str, str] | None = None,
                 ledger_exists: bool = True, fail_on: str | None = None):
        self.ledger = dict(ledger or {})
        self.ledger_exists = ledger_exists
        self.fail_on = fail_on          # substring of SQL that should raise
        self.executed: list[str] = []   # migration bodies actually run

    # -- db interface ------------------------------------------------------
    async def fetch(self, sql: str, *args):
        if "schema_migrations" in sql and "SELECT" in sql:
            if not self.ledger_exists:
                raise RuntimeError('relation "schema_migrations" does not exist')
            return [Row(filename=name, checksum=digest)
                    for name, digest in self.ledger.items()]
        return []

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def execute(self, sql: str, *args):
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in sql:
            self.ledger_exists = True
            return "OK"
        self.executed.append(sql)
        return "OK"

    async def get_pool(self):
        return _Pool(self)


class _Pool:
    """Mimics the `pool.acquire()` / `conn.transaction()` shape apply() uses."""

    def __init__(self, db):
        self.db = db

    def acquire(self):
        return _Acquire(_Conn(self.db))


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, db):
        self.db = db
        self._pending: list[tuple] = []

    def transaction(self):
        return _Transaction(self)

    async def execute(self, sql: str, *args):
        if "INSERT INTO schema_migrations" in sql:
            # Buffered, then committed by the transaction — so a failed migration
            # cannot leave a ledger row claiming success.
            self._pending.append((args[0], args[1]))
            return "OK"
        if self.db.fail_on and self.db.fail_on in sql:
            raise RuntimeError("syntax error at or near ...")
        self.db.executed.append(sql)
        return "OK"


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, *rest):
        if exc_type is None:
            for name, digest in self.conn._pending:
                self.conn.db.ledger[name] = digest
        self.conn._pending.clear()
        return False


def write_migrations(directory: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (directory / name).write_text(body)


class TestChecksum(unittest.TestCase):
    """Normalization decides what counts as drift, so pin both directions."""

    def test_stable_for_identical_content(self):
        self.assertEqual(checksum("SELECT 1;"), checksum("SELECT 1;"))

    def test_ignores_line_ending_churn(self):
        # A CRLF checkout on Windows must not read as every migration drifting.
        self.assertEqual(checksum("A\nB\n"), checksum("A\r\nB\r\n"))

    def test_ignores_trailing_whitespace_and_surrounding_blank_lines(self):
        self.assertEqual(checksum("A   \nB\t\n"), checksum("\n\nA\nB\n\n"))

    def test_detects_a_real_edit(self):
        # The case that must NOT be normalized away.
        self.assertNotEqual(checksum("ALTER TABLE t ADD c INT"),
                            checksum("ALTER TABLE t DROP COLUMN c"))

    def test_detects_whitespace_inside_a_statement(self):
        # Interior whitespace can be semantic (inside a string literal), so it is
        # deliberately NOT normalized.
        self.assertNotEqual(checksum("INSERT INTO t VALUES ('a b')"),
                            checksum("INSERT INTO t VALUES ('a  b')"))


class TestDiscover(unittest.TestCase):
    def test_returns_lexical_order(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            # Written out of order on purpose; glob order is not guaranteed.
            write_migrations(directory, {"003_c.sql": "C", "001_a.sql": "A",
                                         "002_b.sql": "B"})
            self.assertEqual([p.name for p in discover(directory)],
                             ["001_a.sql", "002_b.sql", "003_c.sql"])

    def test_rejects_unnumbered_filename(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A", "add_thing.sql": "B"})
            with self.assertRaises(MigrationError) as caught:
                discover(directory)
            self.assertIn("add_thing.sql", str(caught.exception))

    def test_rejects_two_digit_prefix(self):
        # "9_x" and "10_y" sort as 10 before 9 — exactly the silent reordering the
        # three-digit rule exists to prevent.
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"9_x.sql": "A", "10_y.sql": "B"})
            with self.assertRaises(MigrationError):
                discover(directory)

    def test_missing_directory_is_an_error_not_an_empty_plan(self):
        # An empty plan would report "up to date" for a database with no tables.
        with self.assertRaises(MigrationError):
            discover(Path("/nonexistent/migrations"))

    def test_real_migrations_directory_is_well_formed(self):
        """The shipped migrations must satisfy the rule the code enforces."""
        root = Path(__file__).parent.parent / "server" / "migrations"
        names = [p.name for p in discover(root)]
        self.assertGreaterEqual(len(names), 6)
        self.assertEqual(names, sorted(names))
        prefixes = [int(n[:3]) for n in names]
        self.assertEqual(prefixes, sorted(prefixes))
        self.assertEqual(len(set(prefixes)), len(prefixes),
                         f"duplicate migration numbers: {names}")


class TestPlan(unittest.TestCase):
    def test_fresh_database_reports_everything_pending(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A", "002_b.sql": "B"})
            state = run(migrator.plan(LedgerDB(ledger_exists=False), directory))
            self.assertFalse(state["ledger_exists"])
            self.assertEqual(state["pending"], ["001_a.sql", "002_b.sql"])
            self.assertFalse(state["up_to_date"])

    def test_fully_applied_reports_up_to_date(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A")})
            state = run(migrator.plan(db, directory))
            self.assertTrue(state["up_to_date"])
            self.assertEqual(state["current"], ["001_a.sql"])
            self.assertEqual(state["pending"], [])

    def test_changed_file_reports_drift_not_pending(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A-edited"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A")})
            state = run(migrator.plan(db, directory))
            self.assertEqual(state["pending"], [])
            self.assertEqual(len(state["drifted"]), 1)
            self.assertEqual(state["drifted"][0]["filename"], "001_a.sql")
            self.assertFalse(state["up_to_date"])

    def test_deleted_file_reports_orphaned(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A"),
                                  "002_gone.sql": "abc123"})
            state = run(migrator.plan(db, directory))
            self.assertEqual(state["orphaned"], ["002_gone.sql"])
            # Orphans describe missing history, not a broken database, so they
            # must not block being "up to date".
            self.assertTrue(state["up_to_date"])


class TestApply(unittest.TestCase):
    def test_applies_pending_and_records_them(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "CREATE TABLE a()",
                                         "002_b.sql": "CREATE TABLE b()"})
            db = LedgerDB(ledger_exists=False)
            result = run(migrator.apply(db, directory, actor="tester"))
            self.assertEqual(result["applied_count"], 2)
            self.assertEqual(db.executed,
                             ["CREATE TABLE a()", "CREATE TABLE b()"])
            self.assertEqual(sorted(db.ledger), ["001_a.sql", "002_b.sql"])

    def test_second_run_applies_nothing(self):
        """THE fix: a non-idempotent migration must never run twice."""
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_seed.sql": "INSERT INTO t VALUES (1)"})
            db = LedgerDB(ledger_exists=False)
            run(migrator.apply(db, directory))
            self.assertEqual(len(db.executed), 1)

            second = run(migrator.apply(db, directory))
            self.assertEqual(second["applied_count"], 0)
            self.assertEqual(len(db.executed), 1,
                             "the migration ran a second time — this is the data-"
                             "corruption bug the ledger exists to prevent")

    def test_applies_in_order(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {
                "002_alter.sql": "ALTER TABLE t ADD c INT",
                "001_create.sql": "CREATE TABLE t()"})
            db = LedgerDB(ledger_exists=False)
            run(migrator.apply(db, directory))
            self.assertEqual(db.executed,
                             ["CREATE TABLE t()", "ALTER TABLE t ADD c INT"])

    def test_drift_aborts_and_applies_nothing(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A-edited",
                                         "002_b.sql": "B"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A")})
            with self.assertRaises(MigrationError) as caught:
                run(migrator.apply(db, directory))
            self.assertIn("001_a.sql", str(caught.exception))
            # 002 was pending and legitimate, but applying it against a schema that
            # doesn't match the code is how you get a half-migrated database.
            self.assertEqual(db.executed, [],
                             "drift must abort before applying anything")

    def test_allow_drift_proceeds_with_pending_only(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A-edited",
                                         "002_b.sql": "CREATE TABLE b()"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A")})
            result = run(migrator.apply(db, directory, allow_drift=True))
            # The drifted file is NOT re-run — overriding the guard means "carry on",
            # not "replay", which would be the destructive reading.
            self.assertEqual(db.executed, ["CREATE TABLE b()"])
            self.assertEqual(result["applied_count"], 1)
            self.assertEqual(len(result["drifted"]), 1)

    def test_failure_stops_and_leaves_no_ledger_row(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_ok.sql": "CREATE TABLE a()",
                                         "002_bad.sql": "SYNTAX ERROR HERE",
                                         "003_later.sql": "CREATE TABLE c()"})
            db = LedgerDB(ledger_exists=False, fail_on="SYNTAX ERROR")
            with self.assertRaises(MigrationError) as caught:
                run(migrator.apply(db, directory))
            message = str(caught.exception)
            self.assertIn("002_bad.sql", message)
            self.assertIn("rolled back", message)
            # 001 succeeded and is recorded; 002 failed so must NOT be; 003 must not
            # have run, because it may assume 002 landed.
            self.assertIn("001_ok.sql", db.ledger)
            self.assertNotIn("002_bad.sql", db.ledger)
            self.assertNotIn("003_later.sql", db.ledger)
            self.assertEqual(db.executed, ["CREATE TABLE a()"])

    def test_resumes_after_a_fixed_failure(self):
        """A failed run must be resumable without re-running what succeeded."""
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_ok.sql": "CREATE TABLE a()",
                                         "002_bad.sql": "SYNTAX ERROR HERE"})
            db = LedgerDB(ledger_exists=False, fail_on="SYNTAX ERROR")
            with self.assertRaises(MigrationError):
                run(migrator.apply(db, directory))

            # Operator fixes the SQL and re-runs.
            (directory / "002_bad.sql").write_text("CREATE TABLE b()")
            db.fail_on = None
            result = run(migrator.apply(db, directory))
            self.assertEqual(result["applied_count"], 1)
            self.assertEqual(db.executed, ["CREATE TABLE a()", "CREATE TABLE b()"])


class TestStartupCheck(unittest.TestCase):
    """The app SP holds DML but not DDL: this path must only ever read."""

    def test_never_applies_anything(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "CREATE TABLE a()"})
            db = LedgerDB(ledger_exists=False)
            result = run(migrator.startup_check(db, directory))
            self.assertFalse(result["ok"])
            self.assertEqual(result["pending"], ["001_a.sql"])
            self.assertEqual(db.executed, [],
                             "startup must not apply migrations — the app SP has "
                             "no DDL privilege and replaying SQL on boot is the "
                             "bug this design removes")

    def test_reports_ok_when_current(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_migrations(directory, {"001_a.sql": "A"})
            db = LedgerDB(ledger={"001_a.sql": checksum("A")})
            result = run(migrator.startup_check(db, directory))
            self.assertTrue(result["ok"])

    def test_bad_directory_returns_an_error_instead_of_raising(self):
        # Startup must survive this: a status check failing is not a reason to
        # refuse to serve a portfolio that works.
        result = run(migrator.startup_check(LedgerDB(), Path("/nonexistent")))
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


class TestAppStartupWiring(unittest.TestCase):
    """app.py's check_schema must be non-fatal and must not apply."""

    def test_check_schema_is_read_only_and_survives_failure(self):
        import app as app_module

        class Exploding:
            async def get_pool(self):
                return object()

            async def fetch(self, *a, **k):
                raise RuntimeError("connection reset")

            async def execute(self, *a, **k):
                raise AssertionError("startup must not execute DDL")

        original = app_module.db
        try:
            app_module.db = Exploding()
            run(app_module.check_schema())  # must not raise
        finally:
            app_module.db = original

    def test_demo_mode_short_circuits(self):
        import app as app_module

        class NoPool:
            async def get_pool(self):
                return None

            async def fetch(self, *a, **k):
                raise AssertionError("must not query without a pool")

        original = app_module.db
        try:
            app_module.db = NoPool()
            run(app_module.check_schema())
        finally:
            app_module.db = original


if __name__ == "__main__":
    unittest.main()
