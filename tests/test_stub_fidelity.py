"""The asyncpg stub must expose everything `server/db.py` resolves at import time.

THE REGRESSION THIS GUARDS
--------------------------
`server/db.py` builds its `except` tuples at MODULE scope, so every `asyncpg.X` it
names is resolved when the module is imported. Adding `asyncpg.PostgresConnectionError`
and `asyncpg.InterfaceError` to `_CONNECTION_ERRORS` without adding them to
`tests/stubs.py` produced an `AttributeError` while importing `server.db`, which
cascaded into an ImportError for every module that transitively imports it: the
stdlib-only suite collapsed from ~1090 collected tests to 709 with 56 `_FailedTest`
errors, reported against unrelated modules (test_limits, test_logging,
test_value_engine, ...) rather than the actual cause.

WHY IT WAS INVISIBLE LOCALLY
---------------------------
`stubs._ensure()` deliberately leaves the real package alone when it is installed, so
a developer with asyncpg in their venv never executes the stub path at all. The
stdlib-only path is what `scripts/check.py` and CI run, and what a customer runs. A
green local suite proved nothing about it.

WHY THIS TEST IS DERIVED FROM SOURCE
------------------------------------
A hand-listed set of expected attributes would have to be updated by the same person
who forgot to update the stub. So this parses the `asyncpg.<Name>` references out of
`server/db.py` and asserts the stub satisfies all of them — it fails on the NEXT
attribute added, without anyone remembering this file exists.
"""
import os
import re
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB_SOURCE = (ROOT / "server" / "db.py").read_text()

# `asyncpg.Something` in real code, ignoring the ones inside comments. Comments are
# stripped first because db.py's own docstrings discuss these class names in prose.
_CODE_LINES = "\n".join(
    line for line in DB_SOURCE.split("\n") if not line.lstrip().startswith("#"))
REFERENCED = set(re.findall(r"asyncpg\.([A-Za-z_][A-Za-z0-9_]*)", _CODE_LINES))


class TestStubCoversWhatDbReferences(unittest.TestCase):
    def setUp(self):
        self.stub = stubs._asyncpg()
        self.assertIsInstance(self.stub, types.ModuleType)

    def test_db_references_at_least_the_known_exception_tuples(self):
        """Sanity-check the regex, so a parse failure cannot make this vacuous."""
        for name in ("PostgresConnectionError", "InterfaceError",
                     "InvalidPasswordError", "create_pool"):
            self.assertIn(name, REFERENCED,
                          f"the source scan missed asyncpg.{name} — bad regex?")

    def test_every_referenced_attribute_exists_on_the_stub(self):
        missing = sorted(name for name in REFERENCED
                         if not hasattr(self.stub, name))
        self.assertEqual(
            missing, [],
            "server/db.py resolves these asyncpg attributes at import time but the "
            f"test stub does not define them: {missing}. Under the stdlib-only path "
            "(scripts/check.py, CI) this is an AttributeError importing server.db, "
            "which cascades into import errors across the whole suite.")

    def test_referenced_exception_attributes_are_usable_in_except_clauses(self):
        """A non-exception in an `except` tuple is a TypeError at raise time."""
        for name in sorted(REFERENCED):
            if not name.endswith("Error"):
                continue
            with self.subTest(attribute=name):
                candidate = getattr(self.stub, name)
                self.assertTrue(
                    isinstance(candidate, type)
                    and issubclass(candidate, BaseException),
                    f"asyncpg.{name} must be a real exception subclass on the stub, "
                    "or `except` rejects it with a TypeError")

    def test_server_db_imports_cleanly_against_the_stub(self):
        """The end-to-end property: importing server.db must not raise."""
        import importlib

        sys.modules.pop("server.db", None)
        try:
            module = importlib.import_module("server.db")
        except AttributeError as exc:      # pragma: no cover - the regression itself
            self.fail(f"server.db failed to import against the stub: {exc}")
        self.assertTrue(module._CONNECTION_ERRORS)
        self.assertTrue(module._AUTH_ERRORS)


class TestStubInheritanceMatchesRealAsyncpg(unittest.TestCase):
    """The stub must agree with production about WHICH handler wins.

    If everything were a bare `Exception`, `except _AUTH_ERRORS` would stop catching
    `InvalidPasswordError` via its base, and the token-refresh retry would be tested
    as dead code while working in production (or vice versa). So the hierarchy is
    asserted, not just the existence of the names.
    """

    def setUp(self):
        self.stub = stubs._asyncpg()

    def test_invalid_password_is_an_authorization_error(self):
        self.assertTrue(issubclass(self.stub.InvalidPasswordError,
                                   self.stub.InvalidAuthorizationSpecificationError))

    def test_connection_error_is_a_postgres_error(self):
        self.assertTrue(issubclass(self.stub.PostgresConnectionError,
                                   self.stub.PostgresError))

    def test_interface_error_is_not_a_postgres_error(self):
        """Matches upstream: InterfaceError is driver-side, not server-reported."""
        self.assertFalse(issubclass(self.stub.InterfaceError,
                                    self.stub.PostgresError))

    def test_undefined_table_carries_sqlstate_42p01(self):
        """`accounts.is_missing_relation` requires the real driver signal.

        It no longer guesses from class names or messages, so a stub without this
        attribute would make every pre-migration test silently assert fail-CLOSED
        while believing it asserted the pre-migration path.
        """
        self.assertEqual(self.stub.UndefinedTableError.sqlstate, "42P01")

    def test_unique_violation_carries_sqlstate_23505(self):
        self.assertEqual(self.stub.UniqueViolationError.sqlstate, "23505")

    def test_connection_and_query_errors_are_disjoint(self):
        """A query error must never be caught as a connection failure.

        That distinction is what keeps a 42P01 from becoming a 503 and breaking the
        pre-migration path — so the stub has to preserve it too, or the test that
        pins it passes for the wrong reason.
        """
        import asyncio

        connection_errors = (OSError, asyncio.TimeoutError,
                             self.stub.PostgresConnectionError,
                             self.stub.InterfaceError)
        for name in ("UndefinedTableError", "UniqueViolationError",
                     "InvalidPasswordError"):
            with self.subTest(error=name):
                self.assertFalse(
                    issubclass(getattr(self.stub, name), connection_errors),
                    f"{name} must not be catchable as a connection failure")


class TestStubStillRefusesToOpenAPool(unittest.TestCase):
    """The stub's original job must survive the additions."""

    def test_create_pool_directs_the_reader_to_fakedb(self):
        import asyncio

        stub = stubs._asyncpg()
        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(stub.create_pool())
        self.assertIn("FakeDB", str(caught.exception))

    def test_pool_type_exists_for_annotations(self):
        self.assertTrue(isinstance(stubs._asyncpg().Pool, type))


class TestRealPackageIsNeverMasked(unittest.TestCase):
    def test_ensure_leaves_an_installed_package_alone(self):
        """Why the regression was invisible locally — and must stay that way.

        `_ensure` must not shadow a real install, or a full environment would stop
        exercising real code paths. That is correct behaviour; it is also exactly why
        the stub needs its own test rather than relying on someone's venv.
        """
        before = sys.modules.get("json")
        stubs._ensure("json", lambda: types.ModuleType("not-json"))
        self.assertIs(sys.modules.get("json"), before)


if __name__ == "__main__":
    unittest.main()
