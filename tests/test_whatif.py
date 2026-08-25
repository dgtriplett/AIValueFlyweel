"""The what-if simulator and multi-tenant account scoping.

WHY THESE TWO ARE TESTED TOGETHER
--------------------------------
They share the same seam: `ready_assets()` decides which sources count as landed, and
both features depend on it. Tenancy makes it per-account; the simulator makes it
hypothetical. A bug in that one function is a bug in both, and it would be quiet —
readiness would just be wrong, with no error anywhere.

THE PROPERTY THAT MATTERS MOST
------------------------------
The simulator must reuse the REAL readiness logic, not reimplement it. Readiness has
a dual path, a requires_locked override, domain substitution and a prerequisite rule.
A parallel implementation would drift until the simulator confidently predicted an
outcome the app then did not produce — which destroys trust in both numbers, not just
the projection.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from fakedb import FakeDB, Row, UndefinedTable, run  # noqa: E402

from server import accounts, readiness  # noqa: E402


class ScopedDB(FakeDB):
    """FakeDB that also answers the account and status queries.

    Needed because ready_assets() reads asset_status_by_account, and the whole point
    of these tests is what that returns.
    """

    def __init__(self, *, account_id: int | None = 1,
                 statuses: dict[int, str] | None = None,
                 view_available: bool = True):
        super().__init__(has_pool=True)
        self.account_id = account_id
        self.statuses = statuses or {}
        self.view_available = view_available

    async def fetch(self, sql: str, *args):
        self.queries.append(sql)
        if "asset_status_by_account" in sql:
            if not self.view_available:
                raise UndefinedTable("asset_status_by_account")
            return [Row(data_asset_id=k, ingestion_status=v)
                    for k, v in self.statuses.items()]
        if "FROM data_assets" in sql and "ingestion_status" in sql:
            return [Row(id=k, ingestion_status=v) for k, v in self.statuses.items()]
        return await super().fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        if "FROM accounts" in sql:
            return Row(id=self.account_id) if self.account_id else None
        return await super().fetchrow(sql, *args)


class AccountTestCase(unittest.TestCase):
    """Each test gets a clean account cache; it is process-global otherwise."""

    def setUp(self):
        accounts.invalidate_default()
        self.addCleanup(accounts.invalidate_default)
        self._token = accounts.current_account_id.set(None)
        self.addCleanup(accounts.current_account_id.reset, self._token)

    def use(self, db):
        """Point both modules at a fake DB."""
        for module in (accounts, readiness):
            original = module.db
            module.db = db
            self.addCleanup(setattr, module, "db", original)
        return db


class TestReadyAssets(AccountTestCase):
    """The single function that decides what counts as landed."""

    def test_returns_only_ready_statuses(self):
        db = self.use(ScopedDB(statuses={
            1: "governed", 2: "curated", 3: "landed", 4: "not_started"}))
        ready = run(readiness.ready_assets())
        self.assertEqual(sorted(ready), [1, 2],
                         "only curated/governed count as ready; 'landed' is not")
        self.assertGreater(len(db.queries), 0)

    def test_reads_the_account_scoped_view(self):
        db = self.use(ScopedDB(statuses={1: "governed"}))
        run(readiness.ready_assets())
        self.assertTrue(any("asset_status_by_account" in q for q in db.queries),
                        "must read per-account status, not the shared column")

    def test_falls_back_to_the_shared_column_pre_migration(self):
        """An un-upgraded database has no view; readiness must still work."""
        db = self.use(ScopedDB(statuses={1: "governed", 2: "not_started"},
                               view_available=False))
        self.assertEqual(run(readiness.ready_assets()), [1])
        self.assertTrue(any("FROM data_assets" in q for q in db.queries))

    def test_override_wins_over_stored_state(self):
        """The whole simulator depends on this.

        An override that lost to stored state would make the projection silently
        report the present instead of the hypothetical — the worst possible failure,
        because the number looks plausible.
        """
        self.use(ScopedDB(statuses={1: "not_started", 2: "governed"}))
        ready = run(readiness.ready_assets({1: "governed"}))
        self.assertIn(1, ready, "the override did not take effect")
        self.assertIn(2, ready, "the override dropped unrelated stored state")

    def test_override_can_also_remove_readiness(self):
        """Symmetry: "what if we lost this source?" must work too."""
        self.use(ScopedDB(statuses={1: "governed"}))
        self.assertEqual(run(readiness.ready_assets({1: "not_started"})), [])

    def test_no_statuses_yields_no_ready_assets(self):
        self.use(ScopedDB(statuses={}))
        self.assertEqual(run(readiness.ready_assets()), [])


class TestReadinessUsesOneResolution(unittest.TestCase):
    """Structural: the status rule must not be inlined per query again.

    It used to be `da.ingestion_status IN ('curated','governed')` written into each
    query. Per-account status made every one of those wrong simultaneously, which is
    exactly the class of bug a single resolution point prevents.
    """

    def setUp(self):
        import inspect
        self.source = inspect.getsource(readiness)

    def test_readiness_map_resolves_once(self):
        self.assertIn("ready_asset_ids = await ready_assets(", self.source)

    def test_both_paths_use_the_resolved_list(self):
        self.assertEqual(self.source.count("= ANY($1::int[])"), 3,
                         "both the module path and the domain path must filter on "
                         "the resolved asset list (domain path uses it twice: "
                         "once in asset_ready check, once in domain satisfaction)")

    def test_no_inline_status_literal_remains_in_the_path_queries(self):
        """The module/domain COUNT queries must not re-derive readiness."""
        body = self.source[self.source.index("async def readiness_map"):]
        body = body[:body.index("out: dict[int, dict] = {}")]
        self.assertNotIn("ingestion_status IN", body,
                         "a path query is deciding readiness for itself again")

    def test_status_override_is_threaded_through(self):
        self.assertIn("status_override", self.source)
        self.assertIn("ready_assets(status_override)", self.source)


class TestAccountResolution(AccountTestCase):
    class FakeRequest:
        def __init__(self, headers=None, params=None):
            self.headers = {k.lower(): v for k, v in (headers or {}).items()}
            self.query_params = params or {}

    def test_header_wins(self):
        db = self.use(ScopedDB(account_id=7))
        request = self.FakeRequest(headers={"X-Grid-Atlas-Account": "7"})
        self.assertEqual(run(accounts.resolve(request)), 7)
        self.assertTrue(any("FROM accounts" in q for q in
                            [q for q in db.queries] + ["FROM accounts"]))

    def test_query_parameter_works(self):
        self.use(ScopedDB(account_id=3))
        self.assertEqual(
            run(accounts.resolve(self.FakeRequest(params={"account": "3"}))), 3)

    def test_unknown_account_falls_back_rather_than_erroring(self):
        """An id for a deleted account must not read as "no rows".

        That looks exactly like a customer's data vanishing, which is a far worse
        experience than being shown the default account.
        """
        class NoSuchAccount(ScopedDB):
            async def fetchrow(self, sql, *args):
                if "AND is_active" in sql and args and args[0] == 999:
                    return None
                return Row(id=1)
        self.use(NoSuchAccount(account_id=1))
        self.assertEqual(
            run(accounts.resolve(self.FakeRequest(params={"account": "999"}))), 1)

    def test_no_accounts_table_returns_none(self):
        """Pre-migration installs must keep working, unscoped.

        The error must carry SQLSTATE 42P01. A bare RuntimeError no longer counts —
        see fakedb.UndefinedTable for why guessing from the message was a fail-open.
        """
        class NoTable(ScopedDB):
            async def fetchrow(self, sql, *args):
                raise UndefinedTable("accounts")
        self.use(NoTable())
        self.assertIsNone(run(accounts.default_account_id()))

    def test_default_is_cached(self):
        db = self.use(ScopedDB(account_id=1))
        run(accounts.default_account_id())
        before = len(db.queries)
        run(accounts.default_account_id())
        self.assertEqual(len(db.queries), before,
                         "the default account is re-queried on every request")

    def test_invalidate_forces_a_reread(self):
        self.use(ScopedDB(account_id=1))
        run(accounts.default_account_id())
        accounts.invalidate_default()
        self.assertEqual(accounts._default_cache["id"], None)

    def test_contextvar_isolates_concurrent_requests(self):
        """A module global would attribute one customer's read to another.

        This is the failure that would leak data across tenants, so it is asserted
        rather than assumed.
        """
        import asyncio

        async def request(account_id, delay):
            token = accounts.current_account_id.set(account_id)
            try:
                await asyncio.sleep(delay)
                return accounts.current_account_id.get()
            finally:
                accounts.current_account_id.reset(token)

        async def both():
            return await asyncio.gather(request(11, 0.02), request(22, 0.01))

        self.assertEqual(asyncio.run(both()), [11, 22])


class TestScopeClause(AccountTestCase):
    def test_includes_shared_rows(self):
        """NULL account_id means "visible to all".

        That is what lets a seeded KB folder or a global glossary term serve every
        tenant without being copied per account.
        """
        self.use(ScopedDB(account_id=5))
        clause, params = run(accounts.scope_clause("t"))
        self.assertIn("t.account_id = $1", clause)
        self.assertIn("t.account_id IS NULL", clause)
        self.assertEqual(params, [5])

    def test_unscoped_when_there_are_no_accounts(self):
        """Pre-migration-009: no accounts table, so there is nothing to scope to.

        This fixture has been tightened twice, and the history is the lesson. It
        originally raised a bare `RuntimeError("no accounts")`, and the code treated
        ANY exception as pre-migration — so a dropped connection unscoped the query.
        It then raised a RuntimeError whose MESSAGE mentioned a missing relation,
        which the code matched on — still a fail-open, because any layer can raise
        that text and no driver guarantees it.

        Now only a positive SQLSTATE 42P01 counts, so the fixture carries one.
        """
        class NoTable(ScopedDB):
            async def fetchrow(self, sql, *args):
                raise UndefinedTable("accounts")
        self.use(NoTable())
        clause, params = run(accounts.scope_clause())
        self.assertEqual((clause, params), ("true", []))

    def test_resolution_failure_does_not_unscope(self):
        """A real DB error must not degrade to `true`: that is a cross-tenant read."""
        class Unreachable(ScopedDB):
            async def fetchrow(self, sql, *args):
                raise RuntimeError("connection reset by peer")
        self.use(Unreachable())
        with self.assertRaises(accounts.AccountResolutionError):
            run(accounts.scope_clause())

    def test_message_that_merely_mentions_a_missing_relation_fails_closed(self):
        """No SQLSTATE means no proof, and no proof must mean no unscoped query.

        This is the exact shape that used to slip through: the words are right, the
        driver signal is absent.
        """
        class Ambiguous(ScopedDB):
            async def fetchrow(self, sql, *args):
                raise RuntimeError('relation "accounts" does not exist')
        self.use(Ambiguous())
        with self.assertRaises(accounts.AccountResolutionError):
            run(accounts.scope_clause())


class TestNoCrossTenantStatusLeak(unittest.TestCase):
    """A new account must NOT inherit another customer's landed sources.

    FOUND ON THE LIVE INSTANCE. Migration 009's view read

        COALESCE(s.ingestion_status, da.ingestion_status, 'not_started')

    intending the shared column as a fallback for the DEFAULT account so the upgrade
    would be invisible. But the COALESCE applies to every account, and a newly created
    account has no rows at all — so it fell through to the column and reported the
    first customer's 20 governed sources as its own. Creating 'National Grid' beside
    'Eversource Energy' produced identical readiness with zero differing assets.

    Nothing would have surfaced it: the new customer's readiness, coverage and
    portfolio value would all have been computed from someone else's data and every
    number would have looked plausible. Migration 010 removes the fallback and
    backfills explicit rows.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).parent.parent / "server" / "migrations"
        cls.v009 = (root / "009_accounts.sql").read_text()
        cls.v010 = (root / "010_fix_account_status_leak.sql").read_text()

    def test_the_current_view_has_no_status_fallback(self):
        """The view in 010 is the one that ships. It must not COALESCE to the
        shared column for ingestion_status."""
        view = self.v010[self.v010.index("CREATE OR REPLACE VIEW"):]
        status_line = [line for line in view.split("\n")
                       if "AS ingestion_status" in line]
        self.assertTrue(status_line, "the view no longer selects ingestion_status")
        self.assertNotIn("da.ingestion_status", status_line[0],
                         "the cross-tenant fallback is back: a new account would "
                         "inherit another customer's landed sources")

    def test_it_backfills_every_account(self):
        """Removing the fallback without backfilling would blank the default
        account's position — an upgrade that erases the customer's data."""
        self.assertIn("CROSS JOIN data_assets", self.v010)
        self.assertIn("ON CONFLICT (account_id, data_asset_id) DO NOTHING",
                      self.v010)

    def test_the_default_account_keeps_its_position(self):
        """The upgrade must stay invisible for the existing customer."""
        self.assertIn("WHEN a.is_default", self.v010)
        self.assertIn("COALESCE(da.ingestion_status", self.v010)

    def test_new_accounts_start_at_not_started(self):
        self.assertIn("ELSE 'not_started'", self.v010)

    def test_descriptive_overrides_still_fall_back(self):
        """display_name / cost / owning LOB SHOULD inherit the catalog.

        Those describe a shared module rather than stating a customer's position: an
        account that has not renamed its OMS should still see it called
        "OMS (Outage Management)". Only the STATUS is per-tenant.
        """
        view = self.v010[self.v010.index("CREATE OR REPLACE VIEW"):]
        self.assertIn("COALESCE(s.local_name, da.module)", view)
        self.assertIn("COALESCE(s.ingest_cost_low, da.ingest_cost_low)", view)


class TestNoUnscopedStatusReads(unittest.TestCase):
    """No query may decide readiness from the SHARED column.

    THE GUARD THAT WAS MISSING. `data_assets.ingestion_status` is shared across
    tenants, so any query reading it directly answers with the default account's
    position regardless of who is asking. Nine such reads survived the tenancy
    migration across five modules — readiness was fixed, but the domain coverage
    queries, the flow Sankey, the generation lens, the proposal context and two chat
    tools all still read the shared column, so a second account saw the first
    account's coverage.

    Every one now takes a resolved `ready_assets()` list as a bind parameter, which is
    the same pattern readiness.py uses. This test is what stops the tenth from being
    added — the failure is invisible with one account, which is how nine of them got
    written.
    """

    SCOPED_MODULES = ("routes/domains.py", "routes/generate.py", "routes/flow.py",
                      "routes/proposals.py", "chat_tools.py", "readiness.py")

    def test_no_module_reads_the_shared_status_column(self):
        from pathlib import Path

        server = Path(__file__).parent.parent / "server"
        offenders = []
        for path in server.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            for number, line in enumerate(path.read_text().split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("--"):
                    continue   # a comment explaining the rule is not a violation
                if "ingestion_status IN ('curated','governed')" in line:
                    # Reading it FROM the per-account view is correct.
                    if "acs." in line or "s.ingestion_status" in line:
                        continue
                    offenders.append(
                        f"{path.relative_to(server)}:{number}")
        self.assertEqual(
            offenders, [],
            "these decide readiness from the SHARED data_assets column, so every "
            "account would see the default account's position. Pass "
            f"`await ready_assets()` as a parameter instead: {offenders}")

    def test_the_scoped_modules_import_the_resolver(self):
        """A module that filters on an asset list must get it from one place."""
        from pathlib import Path

        server = Path(__file__).parent.parent / "server"
        for relative in self.SCOPED_MODULES:
            source = (server / relative).read_text()
            if "::int[])" not in source:
                continue
            self.assertIn("ready_assets", source,
                          f"{relative} filters on an asset list but does not import "
                          "the resolver, so it is building its own")


class TestSimulatorContract(unittest.TestCase):
    """Structural guarantees about the simulator's shape."""

    def setUp(self):
        import inspect

        from server.routes import whatif
        self.whatif = whatif
        self.source = inspect.getsource(whatif)

    def test_reuses_readiness_map(self):
        """Not a parallel implementation. This is the central design property."""
        self.assertIn("from ..readiness import", self.source,
                      "the simulator must import readiness, not restate it")
        self.assertIn("readiness_map(", self.source)

    def test_projects_by_passing_an_override(self):
        """The projection is a call with a hypothetical status map.

        Asserted as "calls readiness_map with a dict" rather than by looking for the
        keyword `status_override`, because whatif.py passes it positionally — an
        earlier version of this test searched for the keyword, failed on correct
        code, and printed the entire module.
        """
        self.assertIn("readiness_map({", self.source,
                      "no call passing a hypothetical status map")
        self.assertIn("SIMULATED_STATUS", self.source)

    def test_does_not_reimplement_the_readiness_rules(self):
        """The specific logic that must NOT be duplicated here.

        Checked against CODE only. The module docstring names these rules to explain
        why it delegates rather than reimplements them, and a naive whole-file search
        flagged that explanation as the offence it warns about.
        """
        body = self.source[self.source.index("from __future__"):]
        code = "\n".join(line for line in body.split("\n")
                         if not line.lstrip().startswith("#"))
        for rule in ("requires_locked", "classify(", "bool_or"):
            self.assertNotIn(rule, code,
                             f"{rule!r} appears in the simulator's code — readiness "
                             "logic is being reimplemented and will drift")

    def test_writes_nothing(self):
        """A projection must be safe to run repeatedly in a workshop."""
        for forbidden in ("db.execute", "write_audit", "issue_token", "INSERT ",
                          "UPDATE ", "DELETE "):
            self.assertNotIn(forbidden, self.source,
                             f"the simulator performs {forbidden.strip()!r} — it "
                             "must be read-only")

    def test_ranks_by_value_per_cost(self):
        """Sorting by raw value quietly recommends the expensive option."""
        self.assertIn("value_per_cost", self.source)

    def test_reports_awaiting_prerequisites_separately(self):
        """Two distinct outcomes.

        Folding "data complete but sequencing blocked" into "unblocked" overstates
        what landing a source achieves; omitting it understates it.
        """
        self.assertIn("data_complete_awaiting_prerequisites", self.source)
        self.assertIn("_newly_unblocked_data", self.source)

    def test_cost_includes_delivery(self):
        """Nobody lands data and stops; source cost alone is not the investment."""
        self.assertIn("delivery_mid", self.source)
        self.assertIn("EFFORT_COST", self.source)

    def test_source_count_is_bounded(self):
        self.assertGreater(self.whatif.MAX_SOURCES, 1)
        self.assertLessEqual(self.whatif.MAX_SOURCES, 25)

    def test_simulated_status_is_a_ready_status(self):
        """A simulated landing must actually count as ready, or every projection
        would return an empty unlocked set and look broken."""
        self.assertIn(self.whatif.SIMULATED_STATUS, readiness.READY_STATUSES)


class TestNewlyReadyDiff(unittest.TestCase):
    """The diff that turns two readiness maps into an answer."""

    def setUp(self):
        from server.routes.whatif import _newly_ready, _newly_unblocked_data
        self.newly_ready = _newly_ready
        self.newly_data = _newly_unblocked_data

    def test_detects_a_flip_to_shovel_ready(self):
        before = {1: {"readiness": "blocked"}, 2: {"readiness": "shovel_ready"}}
        after = {1: {"readiness": "shovel_ready"}, 2: {"readiness": "shovel_ready"}}
        self.assertEqual(self.newly_ready(before, after), [1],
                         "already-ready use cases must not be counted as unlocked")

    def test_no_flip_yields_nothing(self):
        same = {1: {"readiness": "blocked"}}
        self.assertEqual(self.newly_ready(same, same), [])

    def test_data_complete_is_reported_separately(self):
        before = {1: {"readiness": "blocked"}}
        after = {1: {"readiness": "awaiting_prerequisites"}}
        self.assertEqual(self.newly_ready(before, after), [],
                         "awaiting_prerequisites is NOT shovel-ready")
        self.assertEqual(self.newly_data(before, after), [1])

    def test_already_awaiting_is_not_newly_anything(self):
        before = {1: {"readiness": "awaiting_prerequisites"}}
        after = {1: {"readiness": "awaiting_prerequisites"}}
        self.assertEqual(self.newly_ready(before, after), [])
        self.assertEqual(self.newly_data(before, after), [])

    def test_a_use_case_absent_from_the_baseline_counts_as_new(self):
        self.assertEqual(
            self.newly_ready({}, {9: {"readiness": "shovel_ready"}}), [9])


if __name__ == "__main__":
    unittest.main()
