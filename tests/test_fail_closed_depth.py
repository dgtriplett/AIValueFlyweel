"""The fail-open behaviour that survived one layer beneath the first round of fixes.

An independent cross-vendor review found that the surface fixes were correct but the
degrade-to-unscoped path was still reachable underneath them. Each class below pins
one of those findings, and each was confirmed to FAIL against the code as it stood
after the first round.

WHY THE FIRST ROUND MISSED THESE
--------------------------------
Round one made `default_account_id()` and `scope_clause()` raise on a resolution
FAILURE instead of returning None/`true`. That is necessary but not sufficient,
because it assumed a failure would arrive as an exception. It did not:
`DatabasePool.get_pool()` returned None for BOTH "unconfigured" and "configured but
unreachable", and `fetch()` turned that None into `[]`. So during a real outage no
exception ever reached the account layer — it just saw "no rows", concluded "no
accounts exist", and returned `WHERE true`.

The lesson the tests encode: fail-closed is a property of the whole call chain, not
of the topmost function. Testing `scope_clause()` with a raising fake proved the
top layer worked while the real bottom layer stayed silent.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import accounts as acct  # noqa: E402
from server import portfolio  # noqa: E402
from server.db import DatabasePool, DatabaseUnavailable  # noqa: E402
from server.routes import flow, knowledge as kbr  # noqa: E402
from fakedb import FakeDB, Row, run  # noqa: E402

CONFIGURED = {"PGHOST": "lakebase.internal", "PGUSER": "app"}


def configured_outage_pool(exc=None):
    """A pool whose PGHOST is set but which cannot connect — a real outage."""
    pool = DatabasePool()
    ctx = [
        mock.patch.dict(os.environ, CONFIGURED, clear=True),
        mock.patch("server.db.get_oauth_token", return_value="token"),
        mock.patch("asyncpg.create_pool",
                   side_effect=exc or OSError("no route to host")),
    ]
    for patcher in ctx:
        patcher.start()
    return pool, ctx


class OutageTestCase(unittest.TestCase):
    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)

    def outage(self, exc=None):
        pool, ctx = configured_outage_pool(exc)
        for patcher in ctx:
            self.addCleanup(patcher.stop)
        return pool


# ---------------------------------------------------------------------------
# BLOCKING 1
# ---------------------------------------------------------------------------
class TestConfiguredOutageDoesNotLookLikeNoData(OutageTestCase):
    """A configured-but-unreachable Lakebase must raise, not return benign empties.

    This is the root cause of the whole class of defects: `[]` and `None` are
    indistinguishable from "there is genuinely nothing here", so every layer above
    drew the wrong conclusion — and drew it silently, with a 200.
    """

    def test_fetch_raises_instead_of_returning_empty(self):
        pool = self.outage()
        with self.assertRaises(DatabaseUnavailable):
            run(pool.fetch("SELECT 1"))

    def test_fetchrow_raises_instead_of_returning_none(self):
        pool = self.outage()
        with self.assertRaises(DatabaseUnavailable):
            run(pool.fetchrow("SELECT 1"))

    def test_execute_raises_instead_of_reporting_phantom_success(self):
        """The worst of the three: a write that persisted nothing, reported as 200."""
        pool = self.outage()
        with self.assertRaises(DatabaseUnavailable):
            run(pool.execute("INSERT INTO kb_articles (title) VALUES ('x')"))

    def test_transaction_raises_instead_of_yielding_none(self):
        pool = self.outage()

        async def use():
            async with pool.transaction() as conn:
                return conn

        with self.assertRaises(DatabaseUnavailable):
            run(use())

    def test_auth_error_retry_path_also_raises(self):
        """The token-refresh retry re-checks the pool and must not fall back to []."""
        import asyncpg

        pool = self.outage(asyncpg.InvalidPasswordError("stale token"))
        with self.assertRaises(DatabaseUnavailable):
            run(pool.fetch("SELECT 1"))


class TestUnconfiguredStaysBenign(unittest.TestCase):
    """The demo / fresh-install path must NOT be broken by the fix above.

    With no PGHOST, empty reads and no-op writes are the documented, correct
    behaviour. A fix that raised here would make every local checkout and every
    pre-deploy install look like a hard failure.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        self.pool = DatabasePool()
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fetch_returns_empty(self):
        self.assertEqual(run(self.pool.fetch("SELECT 1")), [])

    def test_execute_returns_none(self):
        self.assertIsNone(run(self.pool.execute("INSERT INTO x VALUES (1)")))

    def test_transaction_yields_none(self):
        async def use():
            async with self.pool.transaction() as conn:
                return conn
        self.assertIsNone(run(use()))

    def test_flags_say_demo_not_degraded(self):
        run(self.pool.fetch("SELECT 1"))
        self.assertTrue(self.pool.is_demo_mode)
        self.assertFalse(self.pool.is_degraded)


class TestOutageCannotUnscopeAQuery(OutageTestCase):
    """End of the chain: the outage must never produce `WHERE true`.

    Reproduced before the fix: resolved=None, scope="true", so a single tenant's
    request read every tenant's rows during a database blip.
    """

    def test_default_account_id_raises(self):
        pool = self.outage()
        with mock.patch.object(acct, "db", pool):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.default_account_id())

    def test_scope_clause_does_not_return_true(self):
        pool = self.outage()
        with mock.patch.object(acct, "db", pool):
            with self.assertRaises(acct.AccountResolutionError):
                clause, _ = run(acct.scope_clause("a"))
                self.fail(f"scope_clause returned {clause!r} during an outage")

    def test_write_clause_does_not_return_true(self):
        pool = self.outage()
        with mock.patch.object(acct, "db", pool):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.write_clause_at(2))

    def test_owned_clause_does_not_return_true(self):
        pool = self.outage()
        with mock.patch.object(acct, "db", pool):
            with self.assertRaises(acct.AccountResolutionError):
                run(acct.owned_clause())


# ---------------------------------------------------------------------------
# BLOCKING 2
# ---------------------------------------------------------------------------
class TestSqlstateClassification(unittest.TestCase):
    """Only 42P01 means "the table was never created"."""

    def _exc(self, code):
        class E(Exception):
            sqlstate = code
        return E("boom")

    def test_undefined_table_is_pre_migration(self):
        self.assertTrue(acct.is_missing_relation(self._exc("42P01")))

    def test_invalid_schema_name_is_a_failure(self):
        """3F000 = bad search_path: a CONFIG error, not an absent table.

        The rows and the tables exist; the deployment is looking in the wrong
        database. Treating it as pre-migration let a misconfiguration operate
        unscoped, which is the exact failure this module exists to prevent.
        """
        self.assertFalse(
            acct.is_missing_relation(self._exc("3F000")),
            "3F000 must fail closed, not enable unscoped operation")

    def test_database_unavailable_is_never_pre_migration(self):
        self.assertFalse(acct.is_missing_relation(
            DatabaseUnavailable("configured but unreachable")))

    def test_other_sqlstates_are_failures(self):
        for code in ("08006", "42501", "57014", "53300"):
            with self.subTest(sqlstate=code):
                self.assertFalse(acct.is_missing_relation(self._exc(code)))


# ---------------------------------------------------------------------------
# BLOCKING 3
# ---------------------------------------------------------------------------
class TestSharedRowsAreReadOnlyToTenants(unittest.TestCase):
    """`account_id IS NULL` must be readable but NOT writable.

    Reusing the read predicate for UPDATE/DELETE meant `OR account_id IS NULL`
    matched every shared row, so ONE tenant could rename, re-path, archive or
    hard-delete the shipped reference library FOR EVERY OTHER CUSTOMER.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        self.db = FakeDB(has_pool=True).on("FROM accounts", [Row(id=5)])
        patcher = mock.patch.object(acct, "db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_clause_includes_shared_rows(self):
        clause, params = run(acct.scope_clause_at(2, "a"))
        self.assertIn("a.account_id IS NULL", clause,
                      "the shipped library must stay visible to every tenant")
        self.assertEqual(params, [5])

    def test_write_clause_excludes_shared_rows(self):
        clause, params = run(acct.write_clause_at(2, "a"))
        self.assertNotIn(
            "IS NULL", clause,
            "a NULL-matching write predicate lets one tenant mutate the shared "
            "library for everyone")
        self.assertIn("a.account_id = $2", clause)
        self.assertEqual(params, [5])

    def test_write_clause_honours_the_placeholder_index(self):
        clause, _ = run(acct.write_clause_at(7))
        self.assertIn("account_id = $7", clause)

    def test_write_clause_is_unscoped_only_before_any_account_exists(self):
        empty = FakeDB(has_pool=True)
        with mock.patch.object(acct, "db", empty):
            acct.invalidate_default()
            clause, params = run(acct.write_clause_at(2))
        self.assertEqual((clause, params), ("true", []))


class TestNoMutationUsesTheReadPredicate(unittest.TestCase):
    """Drive every mutating handler and assert no UPDATE/DELETE matches NULL.

    A source-text check would miss a handler that builds SQL at runtime, so this runs
    the real handlers and inspects the statements they actually issue.
    """

    MUTATORS = {
        "update_folder": lambda: kbr.update_folder(
            1, kbr.FolderPatch(name="G"), _Req()),
        "delete_folder": lambda: kbr.delete_folder(1, _Req()),
        "update_article": lambda: kbr.update_article(
            "t", kbr.ArticleUpdate(title="T2"), _Req()),
        "delete_article_hard": lambda: kbr.delete_article("t", _Req(), hard=True),
        "delete_article_soft": lambda: kbr.delete_article("t", _Req()),
        "restore_version": lambda: kbr.restore_version("t", 1, _Req()),
        "remove_link": lambda: kbr.remove_link(1, _Req()),
        "delete_attachment": lambda: kbr.delete_attachment(1, _Req()),
        "update_term": lambda: flow.update_term(
            9, flow.GlossaryTermIn(term="F", definition="d"), _Req()),
        "delete_term": lambda: flow.delete_term(9, _Req()),
    }

    # Handlers whose only statement is an INSERT, so there is no UPDATE/DELETE
    # predicate to check. They are guarded instead by resolving the parent article
    # with `for_write=True`, which
    # `test_insert_only_handlers_resolve_the_parent_for_write` pins.
    INSERT_ONLY = ("add_link", "upload_attachment")

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        self.db = _RecordingDB()
        for target in (acct, kbr, flow, portfolio):
            patcher = mock.patch.object(target, "db", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        common = mock.patch("server.common.db", self.db)
        common.start()
        self.addCleanup(common.stop)

    def test_mutations_never_match_null_account_id(self):
        for label, factory in self.MUTATORS.items():
            with self.subTest(handler=label):
                self.db.calls.clear()
                try:
                    run(factory())
                except Exception:  # noqa: BLE001 - the STATEMENTS are under test
                    pass
                mutations = [
                    " ".join(sql.split())
                    for sql, _ in self.db.calls
                    if sql.strip().upper().startswith(("UPDATE", "DELETE"))]
                self.assertTrue(mutations, f"{label} issued no mutation")
                for statement in mutations:
                    self.assertNotIn(
                        "IS NULL", statement.upper(),
                        f"{label} mutates rows matching account_id IS NULL, so one "
                        f"tenant can change shared content for all: {statement[:150]}")

    def test_insert_only_handlers_resolve_the_parent_for_write(self):
        """add_link / upload_attachment attach TO an article, so they mutate it.

        Their INSERT carries no account predicate — kb_links and kb_attachments have
        no account_id — so the guard has to be the parent lookup. If that lookup were
        NULL-inclusive, a tenant could bolt links and uploaded files onto the shared
        reference library and every other account would see them.
        """
        self.db.calls.clear()
        try:
            run(kbr.add_link(
                "t", kbr.LinkIn(entity_type="use_case", entity_id=1), _Req()))
        except Exception:  # noqa: BLE001
            pass
        lookups = [" ".join(sql.split()) for sql, _ in self.db.calls
                   if "FROM kb_articles" in sql and "slug = $1" in sql]
        self.assertTrue(lookups, "add_link did not resolve the parent article")
        self.assertNotIn(
            "IS NULL", lookups[0].upper(),
            "add_link resolved the parent article with the READ predicate, so a "
            "shared article can be linked-to by one tenant for everyone")

    def test_article_helper_has_a_write_mode_that_excludes_shared(self):
        """`_article_by_slug(for_write=True)` is what the mutating handlers rely on."""
        self.db.calls.clear()
        run(kbr._article_by_slug("t"))
        read_sql = " ".join(self.db.calls[-1][0].split())
        self.db.calls.clear()
        run(kbr._article_by_slug("t", for_write=True))
        write_sql = " ".join(self.db.calls[-1][0].split())

        self.assertIn("IS NULL", read_sql.upper(),
                      "shared articles must remain readable")
        self.assertNotIn("IS NULL", write_sql.upper(),
                         "shared articles must not be mutable")


# ---------------------------------------------------------------------------
# BLOCKING 4
# ---------------------------------------------------------------------------
class TestGlossaryUniquenessMatchesTheConstraint(unittest.TestCase):
    """The duplicate check must have the same scope as the DB constraint.

    `glossary_terms.term` is `UNIQUE` across ALL accounts (migration 005). Scoping the
    pre-check to one account made it useless in the case that matters: a collision
    with another tenant's term passed the check, then hit the unique index at INSERT
    and surfaced as a 500 instead of a 409.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)

    def _run_create(self, db):
        for target in (acct, flow, portfolio):
            patcher = mock.patch.object(target, "db", db)
            patcher.start()
            self.addCleanup(patcher.stop)
        common = mock.patch("server.common.db", db)
        common.start()
        self.addCleanup(common.stop)
        return flow.create_term(
            flow.GlossaryTermIn(term="Feeder", definition="d"), _Req())

    def test_duplicate_check_is_global(self):
        db = _RecordingDB()
        try:
            run(self._run_create(db))
        except Exception:  # noqa: BLE001
            pass
        probes = [" ".join(sql.split()) for sql, _ in db.calls
                  if "glossary_terms" in sql
                  and sql.strip().upper().startswith("SELECT")]
        self.assertTrue(probes, "no duplicate-detection read happened")
        self.assertNotIn(
            "ACCOUNT_ID", probes[0].upper(),
            "the pre-check must match the GLOBAL unique constraint, or a collision "
            "with another tenant's term becomes an unhandled 500 at INSERT")

    def test_collision_with_another_account_returns_409_not_500(self):
        """The case the scoped check let through."""
        class OtherAccountHasIt(_RecordingDB):
            async def fetchrow(self, sql, *args):
                self.calls.append((sql, args))
                if "FROM accounts" in sql:
                    return Row(id=5)
                if "glossary_terms" in sql and sql.strip().upper().startswith("SELECT"):
                    return Row(id=99)      # owned by a DIFFERENT account
                return Row(id=1, term="Feeder")

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            run(self._run_create(OtherAccountHasIt()))
        self.assertEqual(caught.exception.status_code, 409)

    def test_unique_violation_at_insert_becomes_409(self):
        """Belt and braces for the race two concurrent creates can still lose."""
        class Racy(_RecordingDB):
            async def fetchrow(self, sql, *args):
                self.calls.append((sql, args))
                if "FROM accounts" in sql:
                    return Row(id=5)
                if "glossary_terms" in sql and sql.strip().upper().startswith("SELECT"):
                    return None                      # pre-check passes
                if "INSERT INTO glossary_terms" in sql:
                    class Unique(Exception):
                        sqlstate = "23505"
                    raise Unique("duplicate key value violates unique constraint")
                return Row(id=1)

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            run(self._run_create(Racy()))
        self.assertEqual(caught.exception.status_code, 409)

    def test_unique_helper_does_not_swallow_an_outage(self):
        self.assertFalse(flow._is_unique_violation(
            DatabaseUnavailable("configured but unreachable")))
        self.assertTrue(flow._is_unique_violation(
            Exception("duplicate key value violates unique constraint")))


# ---------------------------------------------------------------------------
# BLOCKING 5
# ---------------------------------------------------------------------------
class TestEntityReadsAreScoped(unittest.TestCase):
    """Link validation and label resolution must not span accounts.

    `roadmap_items` and `funding_requests` are account-owned, so an unscoped
    existence probe let a caller walk ids and learn which of another tenant's items
    exist from the 404-vs-200 difference; the label resolution went further and
    returned their titles.

    `use_cases` / `data_assets` / `data_domains` / `lobs` are the SHARED catalog
    (migration 009: "the library the product ships"), so a plain existence check on
    those is correct. For use_cases the account-specific part is portfolio
    MEMBERSHIP, which is why that case is scoped through
    account_portfolio_use_cases rather than a column comparison.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        self.db = _RecordingDB()
        for target in (acct, kbr, portfolio):
            patcher = mock.patch.object(target, "db", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _sql_for(self, entity_type):
        self.db.calls.clear()
        run(kbr._entity_exists(entity_type, 7))
        return " ".join(self.db.calls[-1][0].split())

    def test_account_owned_entities_are_scoped(self):
        for entity_type in ("roadmap_item", "funding_request"):
            with self.subTest(entity_type=entity_type):
                self.assertIn(
                    "account_id", self._sql_for(entity_type),
                    f"an unscoped {entity_type} probe is a cross-account existence "
                    f"oracle")

    def test_use_case_is_scoped_through_portfolio_membership(self):
        sql = self._sql_for("use_case")
        self.assertIn("account_portfolio_use_cases", sql,
                      "use_cases has no account_id; membership is the scope")

    def test_shared_catalog_entities_need_no_account_predicate(self):
        """Documented exception: identical for every tenant, so not a disclosure."""
        for entity_type in ("data_asset", "data_domain", "lob"):
            with self.subTest(entity_type=entity_type):
                self.assertNotIn("account_id", self._sql_for(entity_type))

    def test_unknown_entity_type_is_rejected_without_a_query(self):
        self.db.calls.clear()
        self.assertFalse(run(kbr._entity_exists("not_a_thing", 1)))
        self.assertEqual(self.db.calls, [])

    def test_label_resolution_scopes_account_owned_tables(self):
        """The stronger leak: labels are rendered into the UI, not just probed."""
        self.assertEqual(
            kbr._ACCOUNT_OWNED_ENTITY_TABLES, {"roadmap_items", "funding_requests"},
            "if a table becomes account-owned it must be added here, or its titles "
            "leak through link label resolution")


class TestAccountsEndpointDoesNotMisdiagnoseAnOutage(unittest.TestCase):
    """`/api/accounts` must not tell an operator to run an applied migration.

    It caught every DB error and answered "Run migration 009 to enable accounts."
    During a real outage that is a confidently wrong diagnosis on the endpoint someone
    checks first — the migration was already applied; Lakebase was unreachable.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        from server.routes import accounts as routes
        self.routes = routes

    def _list(self, exc):
        class Boom(FakeDB):
            async def fetch(self, sql, *args):
                raise exc

            async def fetchrow(self, sql, *args):
                raise exc

        db = Boom(has_pool=True)
        patches = [mock.patch.object(self.routes, "db", db),
                   mock.patch.object(acct, "db", db)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return run(self.routes.list_accounts(_Req()))

    def test_absent_table_still_reports_the_migration(self):
        class Missing(Exception):
            sqlstate = "42P01"

        result = self._list(Missing("no relation"))
        self.assertIn("migration 009", result["note"])

    def test_real_outage_surfaces_as_503(self):
        from fastapi import HTTPException

        class Lost(Exception):
            sqlstate = "08006"

        with self.assertRaises(HTTPException) as caught:
            self._list(Lost("connection reset"))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("not a missing migration", caught.exception.detail)


class TestFolderDeleteCountIsScoped(unittest.TestCase):
    """The unfiled-article count must not include other tenants' articles.

    The inner folder-path subquery was unscoped, so same-path folders in other
    accounts matched and the confirmation dialog reported a number derived from rows
    the caller cannot see.
    """

    def setUp(self):
        acct.invalidate_default()
        self.addCleanup(acct.invalidate_default)
        self.db = _RecordingDB()
        for target in (acct, kbr):
            patcher = mock.patch.object(target, "db", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        common = mock.patch("server.common.db", self.db)
        common.start()
        self.addCleanup(common.stop)

    def test_both_halves_of_the_count_query_are_scoped(self):
        self.db.calls.clear()
        try:
            run(kbr.delete_folder(1, _Req()))
        except Exception:  # noqa: BLE001
            pass
        counts = [" ".join(sql.split()) for sql, _ in self.db.calls
                  if "count(*)" in sql and "kb_articles" in sql]
        self.assertTrue(counts, "no unfiled count was computed")
        statement = counts[0]
        # The outer article predicate AND the inner folder subquery both need one.
        self.assertGreaterEqual(
            statement.count("account_id"), 2,
            "the inner kb_folders subquery is unscoped, so the count spans "
            f"accounts: {statement[:200]}")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
class _Req:
    headers = {"x-forwarded-email": "user@utility.com"}


class _RecordingDB(FakeDB):
    """Answers plausibly for every table these handlers touch, recording each call."""

    def __init__(self):
        super().__init__(has_pool=True)
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM accounts" in sql:
            return [Row(id=5)]
        if "SELECT slug FROM kb_articles" in sql:
            return [Row(slug="taken")]
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM accounts" in sql:
            return Row(id=5)
        if "kb_folders" in sql and ("WHERE path = $1" in sql or "id <> $2" in sql):
            return None
        if "kb_folders" in sql:
            return Row(id=1, name="f", parent_id=None, path="/f/")
        if "kb_article_versions" in sql:
            return Row(title="T", body_md="b", summary="s", version=1)
        if "kb_attachments" in sql:
            return Row(id=1, filename="a.pdf", mime_type="application/pdf",
                       storage="lakebase", volume_path=None, content=b"x",
                       article_id=1)
        if "glossary_terms" in sql and sql.strip().upper().startswith("SELECT"):
            return None
        if "kb_articles" in sql or "kb_links" in sql or "glossary_terms" in sql:
            return Row(id=1, title="T", body_md="b", summary="s", version=1,
                       slug="t", status="draft", created_at=None, updated_at=None,
                       folder_id=None, article_id=1, entity_type="use_case",
                       entity_id=1, relation="related", term="Feeder")
        return Row(n=0, id=1)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


if __name__ == "__main__":
    unittest.main()
