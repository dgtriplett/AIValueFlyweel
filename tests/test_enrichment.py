"""Generated enrichment SQL.

Two things are worth testing without a warehouse:

  1. ESCAPING. The Statement Execution API has no bind parameters, so values are
     interpolated. `sql_str` is the only sanctioned escape and a gap in it is an
     injection bug, so it gets adversarial inputs.

  2. THE STAGED PATTERN'S INVARIANTS. The whole reason this module exists is to
     call `ai_query` exactly once per row (a single MERGE re-invokes it per output
     field, ~4x the cost). That property lives in the SQL's shape, so we assert
     the shape: one ai_query in stage 1, none in stage 2, dedupe before the join,
     and the user-edit guard on every write.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

os.environ.setdefault("ATLAS_CATALOG", "test_catalog")
os.environ.setdefault("ATLAS_SCHEMA", "test_schema")

from server import enrichment  # noqa: E402

CANONICALS = ["Data Historian", "ERP", "GIS", "Meter/AMI/MDM"]

# Strip single-quoted SQL literals (COMMENT bodies, prompt text, JSON schemas)
# before counting constructs, so prose that mentions ai_query() isn't counted as
# a call. Doubled quotes inside a literal are consumed by the alternation.
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _code_only(sql: str) -> str:
    return _LITERAL_RE.sub("''", _LINE_COMMENT_RE.sub("", sql))


def _count_invocations(sql: str, func: str = "ai_query") -> int:
    return len(re.findall(rf"\b{func}\s*\(", _code_only(sql)))


class TestSqlStr(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(enrichment.sql_str("hello"), "'hello'")

    def test_single_quote_is_doubled(self):
        self.assertEqual(enrichment.sql_str("O'Brien"), "'O''Brien'")

    def test_statement_breakout_attempt_is_neutralized(self):
        """The canonical injection shape: close the literal, add a statement."""
        out = enrichment.sql_str("x'; DROP TABLE t; --")
        self.assertEqual(out, "'x''; DROP TABLE t; --'")
        # After escaping, no odd number of consecutive quotes remains inside, so
        # the literal cannot terminate early.
        self.assertTrue(out.startswith("'") and out.endswith("'"))
        for run in re.findall(r"'+", out[1:-1]):
            self.assertEqual(len(run) % 2, 0, f"unbalanced quote run {run!r}")

    def test_backslash_is_not_special_in_spark_literals(self):
        self.assertEqual(enrichment.sql_str("a\\b"), "'a\\b'")

    def test_none_becomes_null_keyword(self):
        self.assertEqual(enrichment.sql_str(None), "NULL")

    def test_numbers_and_bools_unquoted(self):
        self.assertEqual(enrichment.sql_str(42), "42")
        self.assertEqual(enrichment.sql_str(1.5), "1.5")
        self.assertEqual(enrichment.sql_str(True), "true")
        self.assertEqual(enrichment.sql_str(False), "false")

    def test_bool_checked_before_int(self):
        """bool is a subclass of int; a wrong branch order yields '1'/'0'."""
        self.assertNotIn("1", enrichment.sql_str(True))


class TestIdentifierQuoting(unittest.TestCase):
    def test_normal_identifier_backticked(self):
        self.assertEqual(enrichment._ident("main"), "`main`")

    def test_backtick_in_identifier_rejected(self):
        """A backtick would let the name terminate its own quoting."""
        with self.assertRaises(ValueError):
            enrichment._ident("ma`in")

    def test_empty_identifier_rejected(self):
        with self.assertRaises(ValueError):
            enrichment._ident("")


class TestStagingSql(unittest.TestCase):
    def setUp(self):
        self.sql = enrichment.build_staging_sql(
            company_name="Test Utility", canonicals=CANONICALS)

    def test_ai_query_invoked_exactly_once(self):
        """The core cost property this module exists to guarantee.

        Counts only *invocations*: the table COMMENT documents the pattern and
        mentions ai_query() in prose, which must not be mistaken for a call.
        """
        self.assertEqual(_count_invocations(self.sql), 1)

    def test_writes_raw_response_to_staging(self):
        self.assertIn("CREATE OR REPLACE TABLE", self.sql)
        self.assertIn(enrichment.STAGING_TABLE, self.sql)
        self.assertIn("AS resp", self.sql)

    def test_failure_isolation_and_determinism(self):
        self.assertIn("failOnError => false", self.sql)
        self.assertIn("'temperature', 0.0", self.sql)
        self.assertIn("responseFormat =>", self.sql)

    def test_schema_context_deduped_before_join(self):
        """Duplicate schema rows would fan out candidates and multiply cost."""
        self.assertIn("ROW_NUMBER() OVER", self.sql)
        self.assertLess(self.sql.index("rn = 1"), self.sql.index("LEFT JOIN schema_ctx"))

    def test_skips_user_edited_and_already_enriched(self):
        self.assertIn("COALESCE(t.is_user_edited, false) = false", self.sql)
        self.assertIn("t.ai_definition IS NULL", self.sql)
        self.assertIn("t.is_present = true", self.sql)

    def test_company_name_is_escaped(self):
        sql = enrichment.build_staging_sql(
            company_name="O'Brien's Power & Light", canonicals=CANONICALS)
        self.assertIn("O''Brien''s Power & Light", sql)

    def test_canonicals_supplied_as_closed_vocabulary(self):
        for canonical in CANONICALS:
            self.assertIn(canonical, self.sql)

    def test_max_rows_and_schema_filter(self):
        sql = enrichment.build_staging_sql(
            company_name="U", canonicals=[], max_rows=500, only_schema="ami")
        self.assertIn("LIMIT 500", sql)
        self.assertIn("t.schema_name = 'ami'", sql)

    def test_schema_filter_is_escaped(self):
        sql = enrichment.build_staging_sql(
            company_name="U", canonicals=[], only_schema="a'; DROP TABLE x; --")
        self.assertIn("'a''; DROP TABLE x; --'", sql)

    def test_max_rows_coerced_to_int(self):
        """max_rows reaches here from a query param; a string must not be
        interpolated raw."""
        sql = enrichment.build_staging_sql(
            company_name="U", canonicals=[], max_rows="250")
        self.assertIn("LIMIT 250", sql)

    def test_endpoint_override_escaped(self):
        sql = enrichment.build_staging_sql(
            company_name="U", canonicals=[], endpoint="my-endpoint")
        self.assertIn("'my-endpoint'", sql)


class TestMergeSql(unittest.TestCase):
    def setUp(self):
        self.sql = enrichment.build_merge_sql()

    def test_no_llm_calls_in_stage_two(self):
        """If ai_query leaks into the MERGE, the staging split bought nothing."""
        self.assertEqual(_count_invocations(self.sql), 0)

    def test_reads_from_staging(self):
        self.assertIn(enrichment.STAGING_TABLE, self.sql)
        self.assertIn("MERGE INTO", self.sql)

    def test_strips_markdown_fences(self):
        self.assertIn("REGEXP_REPLACE", self.sql)
        self.assertIn("```", self.sql)

    def test_drops_empty_definitions(self):
        """A failed generation must not overwrite a good value with NULL."""
        self.assertIn("c.ai_definition IS NOT NULL", self.sql)
        self.assertIn("c.ai_definition <> ''", self.sql)

    def test_dedupes_merge_source(self):
        """Guards DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE."""
        self.assertIn("ROW_NUMBER() OVER", self.sql)
        self.assertIn("QUALIFY", self.sql)

    def test_respects_user_edits(self):
        self.assertIn("COALESCE(target.is_user_edited, false) = false", self.sql)

    def test_merges_on_the_full_natural_key(self):
        """All four key columns must be in the ON clause; a partial key would
        merge unrelated tables that share a name across workspaces."""
        on_clause = self.sql.split(") AS src", 1)[1].split("WHEN MATCHED", 1)[0]
        for column in ("workspace_id", "catalog_name", "schema_name", "table_name"):
            self.assertRegex(
                on_clause, rf"target\.{column}\s*=\s*src\.{column}",
                f"{column} missing from the MERGE ON clause")

    def test_from_json_parsed_once(self):
        """Multiple from_json calls would reintroduce the projection pushdown."""
        self.assertEqual(_count_invocations(self.sql, "from_json"), 1)


class TestSchemaEnrichmentSql(unittest.TestCase):
    def setUp(self):
        self.sql = enrichment.build_schema_enrichment_sql("Test Utility")

    def test_single_ai_query_and_single_parse(self):
        self.assertEqual(_count_invocations(self.sql), 1)
        self.assertEqual(_count_invocations(self.sql, "from_json"), 1)

    def test_respects_user_edits_and_skips_enriched(self):
        self.assertIn("COALESCE(target.is_user_edited, false) = false", self.sql)
        self.assertIn("s.ai_definition IS NULL", self.sql)

    def test_samples_table_names_with_a_bound(self):
        self.assertIn("LIMIT 40", self.sql)

    def test_company_name_escaped(self):
        sql = enrichment.build_schema_enrichment_sql("O'Brien Co")
        self.assertIn("O''Brien Co", sql)


class TestPruneSql(unittest.TestCase):
    def test_deletes_only_orphans(self):
        sql = enrichment.build_prune_staging_sql()
        self.assertIn("DELETE FROM", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("discovered_tables", sql)


class TestStatsSql(unittest.TestCase):
    def test_counts_ok_and_error_rows(self):
        sql = enrichment.build_staging_stats_sql()
        self.assertIn("rows_ok", sql)
        self.assertIn("rows_error", sql)
        self.assertIn("resp.errorMessage", sql)


if __name__ == "__main__":
    unittest.main()
