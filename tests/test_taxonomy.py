"""Asset taxonomy vocabularies and LLM output validation.

The design decision worth protecting is the SMALL dimension set: only the three
that describe something nothing else in the app models. If someone later adds
`data_domain` or `department` here, there are suddenly two competing answers to
"what domain is this?" and the readiness layer's answer is the one that matters —
so that is asserted explicitly rather than left to review.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from server import taxonomy as tx  # noqa: E402

ASSETS = [
    {"id": 1, "source_category": "Data Historian", "module": "Steam Turbine",
     "description": "Turbine process tags."},
    {"id": 2, "source_category": "Market/ISO Feed", "module": "Day-Ahead LMP",
     "vendor": "PJM"},
]


class TestDimensionScope(unittest.TestCase):
    """The point of importing only three dimensions."""

    def test_exactly_three_dimensions(self):
        self.assertEqual(tx.DIMENSIONS,
                         ("integration_pattern", "criticality", "vendor_type"))

    def test_does_not_duplicate_concepts_modelled_elsewhere(self):
        """data_domain -> data_domains, department -> lobs,
        industry_vertical -> sub_vertical, use_case -> use_cases. Re-adding any of
        these creates a second, competing answer to the same question."""
        for redundant in ("data_domain", "department", "industry_vertical",
                          "use_case", "category"):
            self.assertNotIn(redundant, tx.DIMENSIONS,
                             f"{redundant} is already modelled elsewhere in the app")

    def test_every_dimension_has_a_vocabulary(self):
        for dimension in tx.DIMENSIONS:
            self.assertIn(dimension, tx.ALLOWED_VALUES)
            self.assertGreater(len(tx.ALLOWED_VALUES[dimension]), 1)

    def test_no_vocabulary_entries_beyond_the_dimensions(self):
        self.assertEqual(set(tx.ALLOWED_VALUES), set(tx.DIMENSIONS))

    def test_values_are_unique_within_a_dimension(self):
        for dimension, values in tx.ALLOWED_VALUES.items():
            self.assertEqual(len(values), len(set(values)), dimension)

    def test_every_integration_pattern_has_an_effort_weight(self):
        """A pattern with no weight silently yields None where the UI expects a
        t-shirt size."""
        for value in tx.ALLOWED_VALUES["integration_pattern"]:
            self.assertIn(value, tx.INTEGRATION_EFFORT, value)

    def test_effort_weights_are_valid_sizes(self):
        self.assertTrue(set(tx.INTEGRATION_EFFORT.values()) <= {"S", "M", "L", "XL"})


class TestValidate(unittest.TestCase):
    def test_exact_value_accepted(self):
        self.assertEqual(tx.validate("criticality", "T1 - Mission critical"),
                         "T1 - Mission critical")

    def test_case_insensitive_and_normalizing(self):
        """A model answering in lowercase should be accepted and canonicalized,
        not rejected on capitalization."""
        self.assertEqual(tx.validate("criticality", "t1 - mission critical"),
                         "T1 - Mission critical")
        self.assertEqual(tx.validate("vendor_type", "  OPEN SOURCE  "),
                         "Open source")

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(tx.TaxonomyError) as caught:
            tx.validate("data_domain", "Grid")
        self.assertIn("Unknown dimension", str(caught.exception))

    def test_unknown_value_rejected_and_lists_options(self):
        with self.assertRaises(tx.TaxonomyError) as caught:
            tx.validate("criticality", "Extremely Important")
        self.assertIn("T1 - Mission critical", str(caught.exception))

    def test_empty_value_rejected(self):
        with self.assertRaises(tx.TaxonomyError):
            tx.validate("criticality", "")

    def test_coerce_returns_none_instead_of_raising(self):
        self.assertIsNone(tx.coerce("criticality", "nope"))
        self.assertEqual(tx.coerce("criticality", "T2 - Important"), "T2 - Important")


class TestPrompt(unittest.TestCase):
    def setUp(self):
        self.prompt = tx.build_prompt(ASSETS)

    def test_lists_every_asset_with_its_id(self):
        self.assertIn("id=1", self.prompt)
        self.assertIn("Steam Turbine", self.prompt)
        self.assertIn("id=2", self.prompt)
        self.assertIn("PJM", self.prompt)

    def test_includes_all_three_vocabularies(self):
        for dimension in tx.DIMENSIONS:
            for value in tx.ALLOWED_VALUES[dimension]:
                self.assertIn(value, self.prompt)

    def test_distinguishes_operational_criticality_from_value(self):
        """The most likely misread: criticality is not "how valuable"."""
        self.assertIn("not how valuable", self.prompt)

    def test_asks_for_reasoning(self):
        self.assertIn("reasoning", self.prompt)

    def test_handles_assets_without_optional_fields(self):
        prompt = tx.build_prompt([{"id": 9, "source_category": "GIS",
                                   "module": "Vegetation Management"}])
        self.assertIn("id=9", prompt)


class TestParseClassifications(unittest.TestCase):
    def _parse(self, items, valid_ids=None):
        return tx.parse_classifications(
            {"classifications": items}, valid_ids or {1, 2})

    def test_valid_batch(self):
        rows, warnings = self._parse([{
            "asset_id": 1,
            "integration_pattern": "Real-time streaming",
            "criticality": "T1 - Mission critical",
            "vendor_type": "Commercial COTS",
            "reasoning": "Feeds grid operations.",
        }], valid_ids={1})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["values"]["criticality"], "T1 - Mission critical")
        self.assertEqual(rows[0]["reasoning"], "Feeds grid operations.")
        # One asset requested, one classified -> nothing to warn about.
        self.assertEqual(warnings, [])

    def test_invalid_dimension_dropped_but_others_kept(self):
        """A partially-good classification is still useful."""
        rows, warnings = self._parse([{
            "asset_id": 1,
            "integration_pattern": "Telepathy",
            "criticality": "T2 - Important",
            "vendor_type": "Open source",
        }], valid_ids={1})
        self.assertNotIn("integration_pattern", rows[0]["values"])
        self.assertIn("criticality", rows[0]["values"])
        self.assertTrue(any("Dropped" in w for w in warnings))

    def test_entry_with_no_valid_dimension_skipped(self):
        rows, _ = self._parse([{"asset_id": 1, "criticality": "nope",
                                "vendor_type": "nope",
                                "integration_pattern": "nope"}], valid_ids={1})
        self.assertEqual(rows, [])

    def test_unknown_asset_id_reported_not_silently_ignored(self):
        """Usually means the batch and the response drifted out of sync."""
        rows, warnings = self._parse([{
            "asset_id": 999, "integration_pattern": "Batch",
            "criticality": "T3 - Supporting", "vendor_type": "Open source",
        }], valid_ids={1})
        self.assertEqual(rows, [])
        self.assertTrue(any("not in this batch" in w for w in warnings))

    def test_unclassified_assets_are_reported(self):
        rows, warnings = self._parse([{
            "asset_id": 1, "integration_pattern": "Batch",
            "criticality": "T3 - Supporting", "vendor_type": "Open source",
        }], valid_ids={1, 2, 3})
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("remain unlabelled" in w for w in warnings))

    def test_duplicate_asset_first_answer_wins(self):
        rows, _ = self._parse([
            {"asset_id": 1, "integration_pattern": "Batch",
             "criticality": "T1 - Mission critical", "vendor_type": "Open source"},
            {"asset_id": 1, "integration_pattern": "Batch",
             "criticality": "T3 - Supporting", "vendor_type": "Open source"},
        ], valid_ids={1})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["values"]["criticality"], "T1 - Mission critical")

    def test_non_integer_asset_id_skipped(self):
        rows, _ = self._parse([{"asset_id": "abc", "criticality": "T1 - Mission critical"}])
        self.assertEqual(rows, [])

    def test_string_asset_id_coerced(self):
        rows, _ = self._parse([{
            "asset_id": "1", "integration_pattern": "Batch",
            "criticality": "T1 - Mission critical", "vendor_type": "Open source",
        }], valid_ids={1})
        self.assertEqual(len(rows), 1)

    def test_none_and_malformed_payloads(self):
        self.assertEqual(tx.parse_classifications(None, {1})[0], [])
        self.assertEqual(tx.parse_classifications({}, {1})[0], [])
        self.assertEqual(tx.parse_classifications({"classifications": None}, {1})[0], [])
        self.assertEqual(tx.parse_classifications({"classifications": ["x"]}, {1})[0], [])

    def test_stringified_array_recovered(self):
        import json
        rows, _ = tx.parse_classifications(
            {"classifications": json.dumps([{
                "asset_id": 1, "integration_pattern": "Batch",
                "criticality": "T1 - Mission critical",
                "vendor_type": "Open source"}])}, {1})
        self.assertEqual(len(rows), 1)

    def test_missing_reasoning_becomes_none(self):
        rows, _ = self._parse([{
            "asset_id": 1, "integration_pattern": "Batch",
            "criticality": "T1 - Mission critical", "vendor_type": "Open source",
        }], valid_ids={1})
        self.assertIsNone(rows[0]["reasoning"])


class TestResponseSchema(unittest.TestCase):
    def test_strict_and_enum_backed(self):
        schema = tx.RESPONSE_SCHEMA["json_schema"]
        self.assertTrue(schema["strict"])
        item = schema["schema"]["properties"]["classifications"]["items"]
        for dimension in tx.DIMENSIONS:
            self.assertEqual(tuple(item["properties"][dimension]["enum"]),
                             tx.ALLOWED_VALUES[dimension])
            self.assertIn(dimension, item["required"])

    def test_asset_id_required(self):
        item = tx.RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]["classifications"]["items"]
        self.assertIn("asset_id", item["required"])


class TestSummarize(unittest.TestCase):
    def test_counts_per_dimension_value(self):
        rows = [
            {"asset_id": 1, "values": {"criticality": "T1 - Mission critical",
                                       "vendor_type": "Open source"}},
            {"asset_id": 2, "values": {"criticality": "T1 - Mission critical"}},
        ]
        summary = tx.summarize(rows)
        self.assertEqual(summary["criticality"]["T1 - Mission critical"], 2)
        self.assertEqual(summary["vendor_type"]["Open source"], 1)

    def test_empty_input_has_all_dimension_keys(self):
        summary = tx.summarize([])
        self.assertEqual(set(summary), set(tx.DIMENSIONS))


class TestEffectiveDatingContract(unittest.TestCase):
    """The history-preserving write must close the old row BEFORE inserting, or
    the partial unique index on the current row rejects the insert."""

    def test_supersede_closes_then_inserts(self):
        import inspect

        from server.routes import taxonomy as routes_tx

        source = inspect.getsource(routes_tx._supersede_and_insert)
        self.assertLess(source.index("UPDATE asset_taxonomy"),
                        source.index("INSERT INTO asset_taxonomy"))
        self.assertIn("effective_to IS NULL", source)

    def test_migration_enforces_one_current_value_per_dimension(self):
        path = os.path.join(ROOT, "server", "migrations", "003_discovery.sql")
        with open(path) as fh:
            sql = fh.read()
        self.assertIn("idx_taxonomy_current", sql)
        self.assertIn("WHERE effective_to IS NULL", sql)
        self.assertIn("CREATE UNIQUE INDEX", sql)

    def test_ai_classify_never_overwrites_a_manual_value(self):
        import inspect

        from server.routes import taxonomy as routes_tx

        source = inspect.getsource(routes_tx.classify)
        self.assertIn("at.source = 'manual'", source)


if __name__ == "__main__":
    unittest.main()
