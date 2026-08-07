"""Classification rules.

Rules exist so a user can state a naming convention once instead of hand-correcting
thousands of discovered rows. Two properties carry the weight: first-match-wins must
be reliable (or a specific rule can't override a general one, which is how real
conventions work), and one bad regex must not break classification for an entire
estate.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from server import rules as rl  # noqa: E402


def _rule(**overrides):
    base = {"dimension": "environment", "field": "catalog_name",
            "match_type": "prefix", "pattern": "prod", "value": "production",
            "priority": 100, "is_active": True, "case_sensitive": False}
    base.update(overrides)
    return base


class TestValidateRule(unittest.TestCase):
    def test_valid_rule_normalized(self):
        rule = rl.validate_rule(_rule(dimension="ENVIRONMENT", field="Catalog_Name"))
        self.assertEqual(rule["dimension"], "environment")
        self.assertEqual(rule["field"], "catalog_name")

    def test_unknown_dimension_field_match_rejected(self):
        for bad in ({"dimension": "colour"}, {"field": "table_owner"},
                    {"match_type": "fuzzy"}):
            with self.assertRaises(rl.RuleError, msg=repr(bad)):
                rl.validate_rule(_rule(**bad))

    def test_empty_pattern_rejected(self):
        with self.assertRaises(rl.RuleError):
            rl.validate_rule(_rule(pattern="   "))

    def test_bad_regex_rejected_at_definition_time(self):
        """The user must learn immediately, not on the next 40,000-row sweep."""
        with self.assertRaises(rl.RuleError) as caught:
            rl.validate_rule(_rule(match_type="regex", pattern="opco_(unclosed"))
        self.assertIn("invalid regex", str(caught.exception))

    def test_assigning_rule_needs_a_value(self):
        with self.assertRaises(rl.RuleError) as caught:
            rl.validate_rule(_rule(value=None))
        self.assertIn("needs a value", str(caught.exception))

    def test_ignore_rule_must_not_have_a_value(self):
        rule = rl.validate_rule(_rule(dimension="ignore", value=None))
        self.assertIsNone(rule["value"])
        with self.assertRaises(rl.RuleError):
            rl.validate_rule(_rule(dimension="ignore", value="something"))


class TestMatches(unittest.TestCase):
    def test_each_match_type(self):
        self.assertTrue(rl.matches(_rule(match_type="equals", pattern="prod_dw"),
                                   "prod_dw"))
        self.assertTrue(rl.matches(_rule(match_type="prefix", pattern="prod"),
                                   "prod_dw"))
        self.assertTrue(rl.matches(_rule(match_type="suffix", pattern="_dw"),
                                   "prod_dw"))
        self.assertTrue(rl.matches(_rule(match_type="contains", pattern="od_d"),
                                   "prod_dw"))
        self.assertTrue(rl.matches(
            _rule(match_type="regex", pattern=r"^opco_\w+_prod$"), "opco_east_prod"))

    def test_case_insensitive_by_default(self):
        self.assertTrue(rl.matches(_rule(pattern="PROD"), "prod_dw"))

    def test_case_sensitive_when_requested(self):
        self.assertFalse(rl.matches(
            _rule(pattern="PROD", case_sensitive=True), "prod_dw"))

    def test_none_subject_never_matches(self):
        self.assertFalse(rl.matches(_rule(), None))

    def test_broken_regex_returns_false_instead_of_raising(self):
        """One bad rule must not break classification for the whole estate.
        validate_rule is where a human hears about it."""
        broken = {"match_type": "regex", "pattern": "(unclosed",
                  "case_sensitive": False}
        self.assertFalse(rl.matches(broken, "anything"))


class TestClassify(unittest.TestCase):
    def test_first_match_wins_per_dimension(self):
        """The property that makes 'everything dev_ EXCEPT dev_shared' expressible."""
        rules = [
            _rule(id=1, priority=10, match_type="equals", pattern="dev_shared",
                  value="production"),
            _rule(id=2, priority=50, match_type="prefix", pattern="dev",
                  value="development"),
        ]
        result = rl.classify({"catalog_name": "dev_shared"}, rules)
        self.assertEqual(result["environment"], "production")
        self.assertEqual(result["matched_rules"], [1])

    def test_lower_priority_applies_when_specific_rule_misses(self):
        rules = [
            _rule(id=1, priority=10, match_type="equals", pattern="dev_shared",
                  value="production"),
            _rule(id=2, priority=50, match_type="prefix", pattern="dev",
                  value="development"),
        ]
        result = rl.classify({"catalog_name": "dev_analytics"}, rules)
        self.assertEqual(result["environment"], "development")

    def test_independent_dimensions_both_assigned(self):
        rules = [
            _rule(id=1, dimension="environment", pattern="prod", value="production"),
            _rule(id=2, dimension="lob", field="schema_name", match_type="contains",
                  pattern="outage", value="Distribution"),
        ]
        result = rl.classify(
            {"catalog_name": "prod_dw", "schema_name": "outage_mgmt"}, rules)
        self.assertEqual(result["environment"], "production")
        self.assertEqual(result["lob"], "Distribution")

    def test_ignore_flag_set(self):
        rules = [_rule(id=1, dimension="ignore", field="schema_name",
                       match_type="equals", pattern="information_schema", value=None)]
        result = rl.classify({"schema_name": "information_schema"}, rules)
        self.assertTrue(result["ignored"])

    def test_ignore_does_not_stop_other_dimensions_being_recorded(self):
        """Knowing what else matched is how a user judges whether an ignore rule is
        too broad."""
        rules = [
            _rule(id=1, dimension="ignore", field="catalog_name",
                  match_type="prefix", pattern="tmp", value=None),
            _rule(id=2, dimension="environment", pattern="tmp", value="scratch"),
        ]
        result = rl.classify({"catalog_name": "tmp_load"}, rules)
        self.assertTrue(result["ignored"])
        self.assertEqual(result["environment"], "scratch")
        self.assertEqual(sorted(result["matched_rules"]), [1, 2])

    def test_inactive_rules_skipped(self):
        rules = [_rule(id=1, is_active=False)]
        result = rl.classify({"catalog_name": "prod_dw"}, rules)
        self.assertNotIn("environment", result)

    def test_no_match_returns_no_assignment(self):
        result = rl.classify({"catalog_name": "acme_lake"}, [_rule(id=1)])
        self.assertNotIn("environment", result)
        self.assertFalse(result["ignored"])
        self.assertEqual(result["matched_rules"], [])

    def test_matched_rule_ids_are_reported(self):
        """Without these, a surprising classification is undebuggable."""
        result = rl.classify({"catalog_name": "prod_dw"}, [_rule(id=7)])
        self.assertEqual(result["matched_rules"], [7])


class TestSeedRules(unittest.TestCase):
    def test_all_seeds_validate(self):
        for rule in rl.SEED_RULES:
            rl.validate_rule(rule)  # raises on a bad seed

    def test_platform_internals_are_ignored_first(self):
        """These distort every count, so they must win over any assigning rule."""
        ignores = [r for r in rl.SEED_RULES if r["dimension"] == "ignore"]
        self.assertTrue(ignores)
        patterns = {r["pattern"] for r in ignores}
        self.assertIn("information_schema", patterns)
        highest_assign = min((r["priority"] for r in rl.SEED_RULES
                              if r["dimension"] != "ignore"), default=100)
        for rule in ignores:
            self.assertLessEqual(rule["priority"], highest_assign)

    def test_no_utility_specific_business_rules_are_seeded(self):
        """Business-unit prefixes are per-customer; guessing them would silently
        mislabel someone else's estate."""
        self.assertEqual([r for r in rl.SEED_RULES if r["dimension"] == "lob"], [])


class TestSummarize(unittest.TestCase):
    def test_reports_unmatched_which_is_the_useful_signal(self):
        rules = [_rule(id=1)]
        results = rl.test_rules(rules, [
            {"catalog_name": "prod_a"}, {"catalog_name": "prod_b"},
            {"catalog_name": "acme_c"},
        ])
        summary = rl.summarize(results)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["unmatched"], 1)
        self.assertEqual(summary["by_dimension"]["environment"]["production"], 2)

    def test_counts_ignored_separately_from_unmatched(self):
        rules = [_rule(id=1, dimension="ignore", value=None)]
        results = rl.test_rules(rules, [{"catalog_name": "prod_a"}])
        summary = rl.summarize(results)
        self.assertEqual(summary["ignored"], 1)
        self.assertEqual(summary["unmatched"], 0)


if __name__ == "__main__":
    unittest.main()
