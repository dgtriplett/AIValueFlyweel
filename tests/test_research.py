"""Company research, focused on value-assumption calibration.

The assumptions drive every dollar figure in the app, so this is the highest-stakes
model output in the codebase: a wrong number here is not a bad suggestion the user
can ignore, it silently re-scales the whole portfolio. Two properties matter most —
an invented key must never be written, and a confidence badge must be honest,
because a reviewer decides what to check based on it.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from server import research as rs  # noqa: E402

CURRENT = [
    {"key": "customerCount", "label": "Customers", "value": 2000000, "unit": "count",
     "category": "Utility profile"},
    {"key": "omBudgetMM", "label": "O&M budget", "value": 2000, "unit": "$M",
     "category": "Utility profile"},
    {"key": "currentSAIDI", "label": "SAIDI", "value": 120, "unit": "min",
     "category": "Grid & operations"},
]


def _proposal(**overrides):
    base = {"key": "customerCount", "value": 3600000, "confidence": "high",
            "basis": "2024 10-K", "rationale": "Serves ~3.6M electric customers."}
    base.update(overrides)
    return base


def _parse(items, current=None):
    return rs.parse_assumptions({"assumptions": items}, current or CURRENT)


class TestAssumptionGuideCompleteness(unittest.TestCase):
    """The guide is what makes calibration work; a gap in it means the model is
    guessing at semantics for that key."""

    def test_every_shipped_assumption_has_guidance(self):
        path = os.path.join(ROOT, "scripts", "seed_data.json")
        with open(path) as handle:
            seeded = {a["key"] for a in json.load(handle)["value_assumptions"]}
        missing = sorted(seeded - set(rs.ASSUMPTION_GUIDE))
        self.assertEqual(missing, [], f"no derivation guidance for: {missing}")

    def test_no_guidance_for_keys_that_do_not_exist(self):
        path = os.path.join(ROOT, "scripts", "seed_data.json")
        with open(path) as handle:
            seeded = {a["key"] for a in json.load(handle)["value_assumptions"]}
        extra = sorted(set(rs.ASSUMPTION_GUIDE) - seeded)
        self.assertEqual(extra, [], f"guidance for unknown keys: {extra}")

    def test_units_match_the_seeded_definitions(self):
        """A unit mismatch between the guide and the data is how an order-of-
        magnitude error gets introduced."""
        path = os.path.join(ROOT, "scripts", "seed_data.json")
        with open(path) as handle:
            seeded = {a["key"]: a.get("unit") for a in json.load(handle)["value_assumptions"]}
        for key, (unit, _meaning, _how) in rs.ASSUMPTION_GUIDE.items():
            self.assertEqual(unit, seeded[key], f"{key} unit disagrees")

    def test_each_entry_explains_meaning_and_derivation(self):
        for key, (unit, meaning, how) in rs.ASSUMPTION_GUIDE.items():
            self.assertTrue(unit.strip(), key)
            self.assertGreater(len(meaning), 15, f"{key}: meaning too thin")
            self.assertGreater(len(how), 25, f"{key}: no real derivation guidance")

    def test_the_ambiguous_keys_are_disambiguated(self):
        """saidiMinuteValueMM is the canonical trap: the name reads like a
        duration, but it is a dollar value per minute avoided. A model left to
        infer that is wrong by orders of magnitude."""
        _unit, meaning, _how = rs.ASSUMPTION_GUIDE["saidiMinuteValueMM"]
        self.assertIn("NOT a duration", meaning + _how)


class TestCoerceNumber(unittest.TestCase):
    def test_plain_numbers(self):
        self.assertEqual(rs._coerce_number(3600000), 3600000.0)
        self.assertEqual(rs._coerce_number(2.5), 2.5)

    def test_formatted_strings_models_actually_emit(self):
        self.assertEqual(rs._coerce_number("3,600,000"), 3600000.0)
        self.assertEqual(rs._coerce_number("$1200"), 1200.0)
        self.assertEqual(rs._coerce_number("3.6M"), 3600000.0)
        self.assertEqual(rs._coerce_number("450k"), 450000.0)
        self.assertEqual(rs._coerce_number("1.2B"), 1200000000.0)

    def test_rejects_unparseable(self):
        for value in ("about 3 million", "", None, "N/A", {}, []):
            self.assertIsNone(rs._coerce_number(value), repr(value))

    def test_bool_is_not_a_number(self):
        """bool is an int subclass; True must not become 1.0."""
        self.assertIsNone(rs._coerce_number(True))


class TestParseAssumptions(unittest.TestCase):
    def test_happy_path(self):
        proposals, warnings = _parse([_proposal()])
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal["key"], "customerCount")
        self.assertEqual(proposal["value_proposed"], 3600000.0)
        self.assertEqual(proposal["value_before"], 2000000.0)
        self.assertEqual(proposal["confidence"], "high")
        self.assertEqual(proposal["basis"], "2024 10-K")
        self.assertEqual(proposal["pct_change"], 80.0)

    def test_invented_key_is_never_written(self):
        """The critical guard. A key the engine never reads would look like it
        worked while changing nothing."""
        proposals, warnings = _parse([_proposal(key="madeUpMetricMM")])
        self.assertEqual(proposals, [])
        self.assertTrue(any("does not have" in w for w in warnings))
        self.assertTrue(any("madeUpMetricMM" in w for w in warnings))

    def test_missing_keys_are_reported_not_defaulted(self):
        """A half-calibrated model mixing real and generic figures is the most
        misleading state possible, so silence is not acceptable."""
        proposals, warnings = _parse([_proposal()])
        self.assertEqual(len(proposals), 1)
        self.assertTrue(any("generic default" in w for w in warnings))
        self.assertTrue(any("omBudgetMM" in w or "currentSAIDI" in w
                            for w in warnings))

    def test_unreadable_value_is_skipped_with_a_warning(self):
        proposals, warnings = _parse([_proposal(value="roughly 3 million")])
        self.assertEqual(proposals, [])
        self.assertTrue(any("could not read a number" in w for w in warnings))

    def test_negative_value_rejected(self):
        proposals, warnings = _parse([_proposal(value=-5)])
        self.assertEqual(proposals, [])
        self.assertTrue(any("negative" in w for w in warnings))

    def test_zero_is_allowed(self):
        """0 is a real answer — a wires-only utility has no fuel spend — and must
        not be confused with a missing value."""
        proposals, _ = _parse([_proposal(value=0, rationale="No owned generation.")])
        self.assertEqual(proposals[0]["value_proposed"], 0.0)

    def test_implausible_swing_is_flagged_and_downgraded(self):
        """A 1000x change is far more often a unit misread than a real finding.
        Surfaced for review rather than trusted or silently dropped."""
        proposals, warnings = _parse([_proposal(value=2_000_000_000, confidence="high")])
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["confidence"], "low",
                         "an implausible value must not keep a 'high' badge")
        self.assertTrue(any("check the unit" in w for w in warnings))

    def test_plausible_large_change_is_kept(self):
        proposals, warnings = _parse([_proposal(value=8_000_000)])
        self.assertEqual(proposals[0]["confidence"], "high")
        self.assertFalse(any("check the unit" in w for w in warnings))

    def test_bad_confidence_downgraded_to_low(self):
        proposals, _ = _parse([_proposal(confidence="very sure")])
        self.assertEqual(proposals[0]["confidence"], "low")

    def test_duplicate_key_first_wins(self):
        proposals, _ = _parse([_proposal(value=3_600_000),
                               _proposal(value=9_000_000)])
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["value_proposed"], 3600000.0)

    def test_carries_label_and_unit_for_display(self):
        proposals, _ = _parse([_proposal()])
        self.assertEqual(proposals[0]["label"], "Customers")
        self.assertEqual(proposals[0]["unit"], "count")

    def test_doubly_nested_payload_recovered(self):
        """Shares generation.unwrap_list — sonnet-5 returns this shape."""
        payload = {"assumptions": json.dumps({"assumptions": [_proposal()]})}
        proposals, _ = rs.parse_assumptions(payload, CURRENT)
        self.assertEqual(len(proposals), 1)

    def test_malformed_payloads(self):
        for payload in (None, {}, {"assumptions": None}, {"assumptions": "junk"},
                        {"assumptions": ["not a dict"]}):
            proposals, warnings = rs.parse_assumptions(payload, CURRENT)
            self.assertEqual(proposals, [], repr(payload))
            self.assertTrue(warnings, repr(payload))


class TestParseProfile(unittest.TestCase):
    def test_happy_path(self):
        profile = rs.parse_profile({
            "company_name": "Eversource Energy", "utility_type": "investor-owned",
            "segments": ["transmission", "distribution", "retail"],
            "service_territory": "CT, MA, NH", "regulator": "CT PURA, MA DPU",
            "iso_rto": "ISO-NE", "description": "A large New England utility.",
            "confidence": "high", "caveats": "",
        })
        self.assertEqual(profile["company_name"], "Eversource Energy")
        self.assertEqual(profile["segments"],
                         ["transmission", "distribution", "retail"])
        self.assertEqual(profile["confidence"], "high")

    def test_unknown_segments_dropped(self):
        """segments drives which use cases apply, so an invented one does damage."""
        profile = rs.parse_profile({
            "company_name": "X", "segments": ["distribution", "telecom", "water"],
            "description": "d"})
        self.assertEqual(profile["segments"], ["distribution"])

    def test_segments_accepted_as_a_string(self):
        profile = rs.parse_profile({
            "company_name": "X", "segments": "generation, distribution",
            "description": "d"})
        self.assertEqual(profile["segments"], ["generation", "distribution"])

    def test_missing_name_is_an_error(self):
        with self.assertRaises(rs.ResearchError):
            rs.parse_profile({"description": "no name"})
        with self.assertRaises(rs.ResearchError):
            rs.parse_profile(None)

    def test_confidence_defaults_to_low(self):
        profile = rs.parse_profile({"company_name": "X", "description": "d"})
        self.assertEqual(profile["confidence"], "low")


class TestPrompts(unittest.TestCase):
    def test_assumption_prompt_carries_meaning_and_derivation(self):
        prompt = rs.build_assumption_prompt(
            "Test Utility",
            {"utility_type": "investor-owned", "segments": ["distribution"],
             "description": "A utility."},
            CURRENT)
        for entry in CURRENT:
            self.assertIn(entry["key"], prompt)
        # The guidance is the whole reason calibration works.
        self.assertIn("how to derive", prompt)
        self.assertIn("current generic default", prompt)

    def test_assumption_prompt_demands_every_key(self):
        prompt = rs.build_assumption_prompt("X", {}, CURRENT)
        self.assertIn("EVERY key", prompt)
        self.assertIn("Never omit", prompt)

    def test_assumption_prompt_defines_the_confidence_levels(self):
        """A dishonest 'high' badge is worse than a low one, so the prompt has to
        say what each level means and why it matters."""
        prompt = rs.build_assumption_prompt("X", {}, CURRENT)
        for level in rs.CONFIDENCE_LEVELS:
            self.assertIn(f'"{level}"', prompt)
        self.assertIn("corrosive", prompt)

    def test_assumption_prompt_allows_zero_for_inapplicable(self):
        prompt = rs.build_assumption_prompt("X", {}, CURRENT)
        self.assertIn("return 0", prompt)

    def test_profile_prompt_discourages_guessing(self):
        prompt = rs.build_profile_prompt("Some Utility")
        self.assertIn("Some Utility", prompt)
        self.assertIn("rather than guessing", prompt)

    def test_response_schemas_are_strict(self):
        for schema in (rs.RESPONSE_SCHEMA_PROFILE, rs.RESPONSE_SCHEMA_ASSUMPTIONS):
            self.assertTrue(schema["json_schema"]["strict"])
        item = (rs.RESPONSE_SCHEMA_ASSUMPTIONS["json_schema"]["schema"]
                ["properties"]["assumptions"]["items"])
        for field in ("key", "value", "confidence", "rationale"):
            self.assertIn(field, item["required"])


class TestSummarize(unittest.TestCase):
    def test_counts_by_confidence_and_change(self):
        proposals, _ = _parse([
            _proposal(key="customerCount", value=3_600_000, confidence="high"),
            _proposal(key="omBudgetMM", value=2000, confidence="medium"),
            _proposal(key="currentSAIDI", value=95, confidence="low"),
        ])
        summary = rs.summarize_assumptions(proposals)
        self.assertEqual(summary["total"], 3)
        # omBudgetMM was proposed unchanged at 2000.
        self.assertEqual(summary["changed"], 2)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["by_confidence"],
                         {"high": 1, "medium": 1, "low": 1})
        self.assertEqual(summary["needs_review"], 1)

    def test_empty(self):
        summary = rs.summarize_assumptions([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["needs_review"], 0)


class TestConfirmIntegration(unittest.TestCase):
    def test_apply_research_is_a_registered_intent(self):
        from server import confirm as cf
        self.assertIn(cf.INTENT_APPLY_RESEARCH, cf.VALID_INTENTS)

    def test_apply_research_has_an_executor(self):
        from server import confirm as cf
        from server.routes import generate
        self.assertIn(cf.INTENT_APPLY_RESEARCH, generate._EXECUTORS)

    def test_research_never_writes_assumptions_directly(self):
        """Recalibrating re-quantifies the entire portfolio, so it must only ever
        happen through the confirm gate."""
        import inspect

        from server.routes import research as routes_research

        source = inspect.getsource(routes_research.research_company)
        self.assertNotIn("UPDATE value_assumptions", source,
                         "research_company writes assumptions without confirmation")


if __name__ == "__main__":
    unittest.main()
