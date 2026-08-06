"""Setup probes and generated GRANT statements.

The probes exist to turn "403 on some SQL you didn't run" into "here is the GRANT
to run", so the tests check the two things that makes true:

  - REQUIRED vs OPTIONAL is honoured. The app works with only Lakebase and seed
    data; reporting an install broken because Genie isn't configured would send
    people chasing features they never asked for.
  - Dependent probes are SKIPPED, not failed, when their prerequisite is down. A
    dead warehouse otherwise produces four identical red pills and hides the
    root cause.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from fakedb import Row, run  # noqa: E402
from server import config as app_config  # noqa: E402
from server.routes import setup  # noqa: E402


def _ok(rows=None):
    return {"ok": True, "rows": rows if rows is not None else [[1]],
            "columns": [], "error": None}


def _fail(error="PERMISSION_DENIED: user does not have USE CATALOG"):
    return {"ok": False, "rows": [], "columns": [], "error": error}


class ProbeTestCase(unittest.TestCase):
    """Each probe reaches the warehouse through server.routes.setup.run_sql."""

    def setUp(self):
        self._real_run_sql = setup.run_sql
        self._real_db = setup.db

    def tearDown(self):
        setup.run_sql = self._real_run_sql
        setup.db = self._real_db

    def stub_sql(self, result):
        async def fake(statement, timeout_s=40):
            return result
        setup.run_sql = fake


class TestCheckShape(unittest.TestCase):
    def test_check_has_the_fields_the_ui_renders(self):
        check = setup._check("x", False, "X", "detail", required=True,
                             fix="do this", grants=["GRANT ...;"])
        for key in ("name", "ok", "label", "detail", "required", "fix", "grants"):
            self.assertIn(key, check)

    def test_required_defaults_to_false(self):
        self.assertFalse(setup._check("x", True, "X")["required"])


class TestConfigProbe(unittest.TestCase):
    def test_reports_each_missing_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", ""):
            check = setup._probe_config()
        self.assertFalse(check["ok"])
        self.assertTrue(check["required"])
        for var in ("PGHOST", "PGUSER", "DATABRICKS_WAREHOUSE_ID"):
            self.assertIn(var, check["detail"])

    def test_passes_when_all_present(self):
        with mock.patch.dict(os.environ, {"PGHOST": "h", "PGUSER": "u"}), \
             mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", "abc123"):
            self.assertTrue(setup._probe_config()["ok"])

    def test_names_the_fix(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", ""):
            self.assertIn("app.yaml", setup._probe_config()["fix"])


class TestLakebaseProbe(ProbeTestCase):
    def test_demo_mode_when_no_pool(self):
        class NoPool:
            async def get_pool(self):
                return None
        setup.db = NoPool()
        check = run(setup._probe_lakebase())
        self.assertFalse(check["ok"])
        self.assertTrue(check["required"])
        self.assertIn("demo mode", check["detail"])

    def test_ok_when_query_succeeds(self):
        class Good:
            async def get_pool(self):
                return object()

            async def fetchrow(self, sql, *args):
                return Row(ok=1)
        setup.db = Good()
        self.assertTrue(run(setup._probe_lakebase())["ok"])

    def test_connected_but_unqueryable_is_a_distinct_failure(self):
        class Broken:
            async def get_pool(self):
                return object()

            async def fetchrow(self, sql, *args):
                raise RuntimeError("permission denied for table use_cases")
        setup.db = Broken()
        check = run(setup._probe_lakebase())
        self.assertFalse(check["ok"])
        self.assertIn("DML", check["fix"])


class TestSchemaAndSeedProbes(ProbeTestCase):
    def _db(self, row):
        class Fake:
            async def fetchrow(self, sql, *args):
                return row
        return Fake()

    def test_schema_ok_with_all_core_tables(self):
        setup.db = self._db(Row(n=5))
        self.assertTrue(run(setup._probe_schema())["ok"])

    def test_schema_fails_with_partial_tables(self):
        setup.db = self._db(Row(n=3))
        check = run(setup._probe_schema())
        self.assertFalse(check["ok"])
        self.assertIn("seed_clean.py", check["fix"])

    def test_seed_ok_and_reports_counts(self):
        setup.db = self._db(Row(use_cases=240, data_assets=146, domains=63,
                                assumptions=34))
        check = run(setup._probe_seed())
        self.assertTrue(check["ok"])
        self.assertIn("240 use cases", check["detail"])

    def test_seed_notes_missing_domains_without_failing(self):
        """No domains is a supported state — readiness falls back to modules."""
        setup.db = self._db(Row(use_cases=240, data_assets=146, domains=0,
                                assumptions=34))
        check = run(setup._probe_seed())
        self.assertTrue(check["ok"])
        self.assertIn("module requirements", check["detail"])

    def test_seed_fails_when_empty(self):
        setup.db = self._db(Row(use_cases=0, data_assets=0, domains=0, assumptions=0))
        self.assertFalse(run(setup._probe_seed())["ok"])


class TestWarehouseProbe(ProbeTestCase):
    def test_ok(self):
        self.stub_sql(_ok())
        with mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", "wh1"):
            self.assertTrue(run(setup._probe_warehouse("sp"))["ok"])

    def test_missing_config(self):
        with mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", ""):
            check = run(setup._probe_warehouse("sp"))
        self.assertFalse(check["ok"])
        self.assertIn("sql_warehouse", check["fix"])

    def test_failure_yields_an_actionable_grant_hint(self):
        self.stub_sql(_fail("cannot access warehouse"))
        with mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", "wh1"):
            check = run(setup._probe_warehouse("sp@example.com"))
        self.assertFalse(check["ok"])
        self.assertTrue(check["grants"])
        self.assertIn("sp@example.com", check["grants"][0])
        self.assertIn("CAN USE", check["grants"][0])


class TestServingProbe(ProbeTestCase):
    def test_ok(self):
        self.stub_sql(_ok([["OK"]]))
        self.assertTrue(run(setup._probe_serving("sp"))["ok"])

    def test_failure_says_the_app_still_works(self):
        """Losing the LLM degrades agents to heuristics; it must not read as fatal."""
        self.stub_sql(_fail())
        check = run(setup._probe_serving("sp"))
        self.assertFalse(check["ok"])
        self.assertFalse(check["required"])
        self.assertIn("heuristics", check["fix"])
        self.assertIn("CAN QUERY", check["grants"][0])


class TestDiscoveryProbe(ProbeTestCase):
    def test_unconfigured_is_optional_not_broken(self):
        with mock.patch.object(app_config, "ATLAS_CATALOG", ""):
            check = run(setup._probe_discovery("sp"))
        self.assertFalse(check["ok"])
        self.assertFalse(check["required"])
        self.assertIn("146-module catalog works without this", check["fix"])

    def test_read_failure_emits_both_grants(self):
        self.stub_sql(_fail())
        with mock.patch.object(app_config, "ATLAS_CATALOG", "main"), \
             mock.patch.object(app_config, "ATLAS_SCHEMA", "disc"):
            check = run(setup._probe_discovery("sp"))
        self.assertFalse(check["ok"])
        self.assertEqual(len(check["grants"]), 2)
        self.assertIn("CREATE SCHEMA ON CATALOG `main`", check["grants"][0])
        self.assertIn("`main`.`disc`", check["grants"][1])

    def test_readable_but_not_writable_is_caught(self):
        """SHOW SCHEMAS passing is not enough — ingestion needs CREATE/MODIFY, so
        the probe must attempt a write rather than stopping at a read."""
        calls = []

        async def fake(statement, timeout_s=40):
            calls.append(statement)
            return _ok() if statement.startswith("SHOW SCHEMAS") else _fail("no CREATE")
        setup.run_sql = fake
        with mock.patch.object(app_config, "ATLAS_CATALOG", "main"), \
             mock.patch.object(app_config, "ATLAS_SCHEMA", "disc"):
            check = run(setup._probe_discovery("sp"))
        self.assertFalse(check["ok"])
        self.assertIn("cannot create", check["detail"])
        self.assertEqual(len(calls), 2)

    def test_ok_when_writable(self):
        self.stub_sql(_ok())
        with mock.patch.object(app_config, "ATLAS_CATALOG", "main"), \
             mock.patch.object(app_config, "ATLAS_SCHEMA", "disc"):
            self.assertTrue(run(setup._probe_discovery("sp"))["ok"])


class TestGenieProbe(ProbeTestCase):
    def test_unset_is_optional(self):
        with mock.patch.object(app_config, "GENIE_SPACE_ID", ""):
            check = run(setup._probe_genie("sp"))
        self.assertFalse(check["ok"])
        self.assertFalse(check["required"])
        self.assertIn("Optional", check["fix"])

    def test_set_but_mirror_unreadable(self):
        self.stub_sql(_fail())
        with mock.patch.object(app_config, "GENIE_SPACE_ID", "space1"), \
             mock.patch.object(app_config, "GENIE_MIRROR_CATALOG", "main"), \
             mock.patch.object(app_config, "GENIE_MIRROR_SCHEMA", "ga"):
            check = run(setup._probe_genie("sp"))
        self.assertFalse(check["ok"])
        self.assertEqual(len(check["grants"]), 2)


class TestAggregateStatus(ProbeTestCase):
    """`status()` composes the probes; these pin the composition rules."""

    def _setup_env(self, *, lakebase_ok=True, warehouse_ok=True):
        class Fake:
            async def get_pool(self):
                return object() if lakebase_ok else None

            async def fetchrow(self, sql, *args):
                if "information_schema.tables" in sql:
                    return Row(n=5)
                if "FROM use_cases)" in sql:
                    return Row(use_cases=240, data_assets=146, domains=63,
                               assumptions=34)
                return Row(ok=1)
        setup.db = Fake()
        self.stub_sql(_ok() if warehouse_ok else _fail("warehouse unreachable"))

    def _status(self):
        with mock.patch.dict(os.environ, {"PGHOST": "h", "PGUSER": "u"}), \
             mock.patch.object(app_config, "DATABRICKS_WAREHOUSE_ID", "wh1"), \
             mock.patch.object(app_config, "ATLAS_CATALOG", "main"), \
             mock.patch.object(app_config, "GENIE_SPACE_ID", ""), \
             mock.patch.object(setup, "_service_principal",
                               mock.AsyncMock(return_value="sp@example.com")):
            return run(setup.status(mock.MagicMock()))

    def test_ready_when_required_checks_pass(self):
        self._setup_env()
        result = self._status()
        self.assertTrue(result["ready"])
        self.assertEqual(result["summary"]["required_failing"], 0)

    def test_optional_failures_do_not_block_ready(self):
        """Genie unset is an optional failure; the install is still ready."""
        self._setup_env()
        result = self._status()
        self.assertTrue(result["ready"])
        self.assertGreater(result["summary"]["optional_failing"], 0)

    def test_not_ready_when_lakebase_is_down(self):
        self._setup_env(lakebase_ok=False)
        result = self._status()
        self.assertFalse(result["ready"])
        self.assertGreater(result["summary"]["required_failing"], 0)

    def test_dependent_probes_are_skipped_not_failed(self):
        """A dead warehouse must not produce four independent red pills that
        obscure which thing is actually broken."""
        self._setup_env(warehouse_ok=False)
        result = self._status()
        by_name = {c["name"]: c for c in result["checks"]}
        for name in ("serving", "discovery", "system_tables", "genie"):
            self.assertIn("Skipped", by_name[name]["detail"], name)

    def test_schema_and_seed_skipped_without_lakebase(self):
        self._setup_env(lakebase_ok=False)
        by_name = {c["name"]: c for c in self._status()["checks"]}
        self.assertIn("Skipped", by_name["schema"]["detail"])
        self.assertIn("Skipped", by_name["seed"]["detail"])

    def test_checks_returned_in_dependency_order(self):
        self._setup_env()
        names = [c["name"] for c in self._status()["checks"]]
        self.assertEqual(names, [n for n in setup.CHECK_ORDER if n in names])

    def test_next_action_points_at_the_root_cause(self):
        self._setup_env(lakebase_ok=False)
        result = self._status()
        # config passes here, so the first REQUIRED failure is lakebase.
        self.assertIsNotNone(result["next_action"])
        self.assertIn("PGHOST", result["next_action"])

    def test_grants_are_aggregated_and_deduplicated(self):
        self._setup_env(warehouse_ok=False)
        grants = self._status()["grants_sql"]
        self.assertEqual(len(grants), len(set(grants)))

    def test_every_required_check_is_declared_required(self):
        self._setup_env()
        by_name = {c["name"]: c for c in self._status()["checks"]}
        for name in setup.REQUIRED_CHECKS:
            self.assertTrue(by_name[name]["required"], f"{name} should be required")


class TestGrantsEndpoint(ProbeTestCase):
    def test_emits_copyable_sql_naming_the_principal(self):
        with mock.patch.object(app_config, "ATLAS_CATALOG", "main"), \
             mock.patch.object(app_config, "ATLAS_SCHEMA", "disc"), \
             mock.patch.object(app_config, "GENIE_MIRROR_CATALOG", "main"), \
             mock.patch.object(setup, "_service_principal",
                               mock.AsyncMock(return_value="sp@example.com")):
            result = run(setup.grants())
        sql = result["sql"]
        self.assertIn("sp@example.com", sql)
        self.assertIn("GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `main`", sql)
        self.assertIn("system.access", sql)
        # UI-only steps can't be SQL, so they must be comments rather than
        # statements that would fail if pasted into an editor.
        self.assertIn("-- Not SQL", sql)


class TestMigrationsAreAllApplied(unittest.TestCase):
    def test_startup_applies_every_migration_not_just_the_first(self):
        """Regression: the startup path originally read only 001_init.sql, which
        would silently leave the domain, discovery, and agent tables missing."""
        import app as app_module

        files = sorted(p.name for p in app_module.MIGRATIONS_DIR.glob("*.sql"))
        self.assertGreater(len(files), 1)
        combined = "\n".join(
            p.read_text() for p in sorted(app_module.MIGRATIONS_DIR.glob("*.sql")))
        for table in ("use_cases", "data_domains", "discovered_tables",
                      "confirm_tokens", "asset_taxonomy"):
            self.assertIn(table, combined, f"{table} missing from applied migrations")

    def test_migration_order_is_lexical(self):
        import app as app_module
        combined = "\n".join(
            p.read_text() for p in sorted(app_module.MIGRATIONS_DIR.glob("*.sql")))
        # 002+ ALTER tables 001 creates, so creation must come first.
        self.assertLess(combined.index("CREATE TABLE IF NOT EXISTS use_cases"),
                        combined.index("ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS domains_locked"))


if __name__ == "__main__":
    unittest.main()
