"""Use-case generation: prompt construction and candidate validation.

Validation is the part that matters. Model output becomes portfolio rows, and two
specific failures would be corrosive:

  - A hallucinated data domain silently entering the vocabulary, which corrupts
    the layer readiness depends on.
  - A use case labelled 'ready' when its data isn't landed. That claim is the
    entire basis of the recommendation, so it is recomputed from real satisfaction
    state rather than trusted.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from server import generation as gen  # noqa: E402

DOMAIN_INDEX = {
    "interval_meter_usage": {"label": "Interval Meter Usage"},
    "outage_records": {"label": "Outage & Interruption Records"},
    "work_order_history": {"label": "Work Order History"},
    "market_prices": {"label": "Market & Locational Prices"},
    "weather_forecast": {"label": "Weather Forecast"},
}
SATISFIED = {"interval_meter_usage", "outage_records"}


def _candidate(**overrides):
    base = {
        "title": "Predictive Transformer Failure",
        "description": "Scores transformers by failure risk.",
        "business_value": "Avoids unplanned outages.",
        "value_rationale": "Reduces emergency replacement spend.",
        "effort_tshirt": "L",
        "sub_vertical": "cross",
        "lens": "ready",
        "time_horizon": "strategic",
        "value_type": "cost",
        "is_regulatory": False,
        "required_domains": ["interval_meter_usage"],
        "helpful_domains": [],
    }
    base.update(overrides)
    return base


def _validate(items, requested_lens="both", existing=None, limit=10):
    return gen.validate_candidates(
        {"use_cases": items},
        lob_name="Distribution",
        requested_lens=requested_lens,
        domain_index=DOMAIN_INDEX,
        satisfied_names=SATISFIED,
        existing_titles=existing or [],
        limit=limit,
    )


class TestCandidateId(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(gen.candidate_id("A Use Case", "Distribution"),
                         gen.candidate_id("A Use Case", "Distribution"))

    def test_insensitive_to_case_and_padding(self):
        self.assertEqual(gen.candidate_id("  a use CASE ", "distribution"),
                         gen.candidate_id("A Use Case", "Distribution"))

    def test_differs_by_title_and_lob(self):
        self.assertNotEqual(gen.candidate_id("A", "Distribution"),
                            gen.candidate_id("B", "Distribution"))
        self.assertNotEqual(gen.candidate_id("A", "Distribution"),
                            gen.candidate_id("A", "Generation"))

    def test_has_readable_prefix(self):
        self.assertTrue(gen.candidate_id("A", "B").startswith("cand_"))


class TestNormalizeTitle(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(gen.normalize_title("  Load   Forecasting \n"), "Load Forecasting")

    def test_strips_enumeration_prefix(self):
        self.assertEqual(gen.normalize_title("1. Load Forecasting"), "Load Forecasting")
        self.assertEqual(gen.normalize_title("2) Load Forecasting"), "Load Forecasting")

    def test_handles_empty(self):
        self.assertEqual(gen.normalize_title(""), "")
        self.assertEqual(gen.normalize_title(None), "")


class TestPrompt(unittest.TestCase):
    def _prompt(self, **kwargs):
        defaults = dict(
            company_name="Test Utility", lob_name="Distribution", lens="both", count=5,
            satisfied_domains=[{"name": "outage_records", "label": "Outage Records"}],
            unsatisfied_domains=[{"name": "market_prices", "label": "Market Prices"}],
            existing_titles=[],
        )
        defaults.update(kwargs)
        return gen.build_prompt(**defaults)

    def test_includes_both_domain_lists(self):
        prompt = self._prompt()
        self.assertIn("outage_records", prompt)
        self.assertIn("market_prices", prompt)
        self.assertIn("AVAILABLE DATA DOMAINS", prompt)
        self.assertIn("MISSING DATA DOMAINS", prompt)

    def test_ready_lens_constrains_to_available(self):
        prompt = self._prompt(lens="ready")
        self.assertIn("ONLY 'ready'", prompt)
        self.assertIn("no new ingestion", prompt)

    def test_gap_lens_requires_a_missing_domain(self):
        self.assertIn("ONLY 'gap'", self._prompt(lens="gap"))

    def test_both_lens_asks_for_a_mix(self):
        self.assertIn("mix", self._prompt(lens="both"))

    def test_collision_list_included(self):
        prompt = self._prompt(existing_titles=["Existing Thing"])
        self.assertIn("ALREADY IN THE PORTFOLIO", prompt)
        self.assertIn("Existing Thing", prompt)

    def test_collision_list_omitted_when_empty(self):
        self.assertNotIn("ALREADY IN THE PORTFOLIO", self._prompt(existing_titles=[]))

    def test_biases_rendered(self):
        prompt = self._prompt(time_horizon_bias="quick_win", value_type_bias="risk",
                              prioritize_regulatory=True)
        self.assertIn("EMPHASIS", prompt)
        self.assertIn("quick wins", prompt)
        self.assertIn("risk", prompt)
        self.assertIn("regulatory", prompt)

    def test_unknown_bias_ignored(self):
        self.assertNotIn("EMPHASIS", self._prompt(time_horizon_bias="whenever"))

    def test_forbids_inventing_dollar_figures(self):
        """Value must come from the customer's own assumptions via the value
        engine, not from the model's imagination."""
        self.assertIn("Do NOT invent a dollar figure", self._prompt())


class TestValidation(unittest.TestCase):
    def test_happy_path(self):
        candidates, warnings = _validate([_candidate()])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(warnings, [])
        candidate = candidates[0]
        self.assertEqual(candidate["title"], "Predictive Transformer Failure")
        self.assertEqual(candidate["lens"], "ready")
        self.assertEqual(candidate["required_domains"][0]["name"], "interval_meter_usage")
        self.assertTrue(candidate["required_domains"][0]["satisfied"])
        self.assertEqual(candidate["missing_domains"], [])

    def test_hallucinated_domain_dropped_not_created(self):
        candidates, warnings = _validate([
            _candidate(required_domains=["interval_meter_usage", "unicorn_data"])])
        names = [d["name"] for d in candidates[0]["required_domains"]]
        self.assertEqual(names, ["interval_meter_usage"])
        self.assertTrue(any("invented" in w for w in warnings))
        self.assertTrue(any("unicorn_data" in w for w in warnings))

    def test_candidate_with_no_valid_required_domain_is_skipped(self):
        candidates, warnings = _validate([_candidate(required_domains=["unicorn_data"])])
        self.assertEqual(candidates, [])
        self.assertTrue(any("no recognized required data domain" in w for w in warnings))

    def test_lens_recomputed_when_model_lies(self):
        """The model claims 'ready' but requires an unlanded domain."""
        candidates, warnings = _validate(
            [_candidate(lens="ready", required_domains=["market_prices"])])
        self.assertEqual(candidates[0]["lens"], "gap")
        self.assertTrue(any("recorded as 'gap'" in w for w in warnings))

    def test_lens_recomputed_in_the_other_direction(self):
        candidates, _ = _validate(
            [_candidate(lens="gap", required_domains=["outage_records"])])
        self.assertEqual(candidates[0]["lens"], "ready")

    def test_mixed_requirements_are_gap(self):
        """One unlanded domain makes the whole use case a gap."""
        candidates, _ = _validate([_candidate(
            required_domains=["interval_meter_usage", "market_prices"])])
        self.assertEqual(candidates[0]["lens"], "gap")
        self.assertEqual([d["name"] for d in candidates[0]["missing_domains"]],
                         ["market_prices"])

    def test_requested_lens_filters_mismatches(self):
        candidates, warnings = _validate(
            [_candidate(required_domains=["market_prices"])], requested_lens="ready")
        self.assertEqual(candidates, [])
        self.assertTrue(any("you asked for 'ready'" in w for w in warnings))

    def test_both_lens_keeps_everything(self):
        candidates, _ = _validate([
            _candidate(title="A", required_domains=["outage_records"]),
            _candidate(title="B", required_domains=["market_prices"]),
        ], requested_lens="both")
        self.assertEqual({c["lens"] for c in candidates}, {"ready", "gap"})

    def test_duplicate_within_batch_removed(self):
        candidates, _ = _validate([_candidate(title="Same"), _candidate(title="  same  ")])
        self.assertEqual(len(candidates), 1)

    def test_collision_with_portfolio_skipped(self):
        candidates, warnings = _validate([_candidate(title="Already Here")],
                                         existing=["already here"])
        self.assertEqual(candidates, [])
        self.assertTrue(any("already in the portfolio" in w for w in warnings))

    def test_enums_coerced_to_closed_vocabulary(self):
        candidates, _ = _validate([_candidate(
            effort_tshirt="enormous", sub_vertical="offshore",
            time_horizon="someday", value_type="vibes")])
        candidate = candidates[0]
        self.assertEqual(candidate["effort_tshirt"], gen.DEFAULT_EFFORT)
        self.assertEqual(candidate["sub_vertical"], gen.DEFAULT_SUB_VERTICAL)
        self.assertIsNone(candidate["time_horizon"])
        self.assertIsNone(candidate["value_type"])

    def test_enum_matching_is_case_insensitive(self):
        candidates, _ = _validate([_candidate(effort_tshirt="l", sub_vertical="NUCLEAR")])
        self.assertEqual(candidates[0]["effort_tshirt"], "L")
        self.assertEqual(candidates[0]["sub_vertical"], "nuclear")

    def test_helpful_domains_deduped_against_required(self):
        candidates, _ = _validate([_candidate(
            required_domains=["interval_meter_usage"],
            helpful_domains=["interval_meter_usage", "weather_forecast"])])
        self.assertEqual([d["name"] for d in candidates[0]["helpful_domains"]],
                         ["weather_forecast"])

    def test_limit_respected(self):
        items = [_candidate(title=f"UC {i}") for i in range(10)]
        candidates, _ = _validate(items, limit=3)
        self.assertEqual(len(candidates), 3)

    def test_titles_normalized(self):
        candidates, _ = _validate([_candidate(title="3. Feeder   Load  Forecasting")])
        self.assertEqual(candidates[0]["title"], "Feeder Load Forecasting")

    def test_untitled_candidate_skipped(self):
        self.assertEqual(_validate([_candidate(title="   ")])[0], [])

    def test_non_dict_items_ignored(self):
        candidates, _ = _validate(["junk", None, _candidate()])
        self.assertEqual(len(candidates), 1)


class TestMalformedPayloads(unittest.TestCase):
    def test_none_payload(self):
        candidates, warnings = gen.validate_candidates(
            None, lob_name="X", requested_lens="both", domain_index=DOMAIN_INDEX,
            satisfied_names=SATISFIED, existing_titles=[], limit=5)
        self.assertEqual(candidates, [])
        self.assertTrue(warnings)

    def test_missing_key(self):
        candidates, warnings = gen.validate_candidates(
            {"wrong": []}, lob_name="X", requested_lens="both",
            domain_index=DOMAIN_INDEX, satisfied_names=SATISFIED,
            existing_titles=[], limit=5)
        self.assertEqual(candidates, [])
        self.assertTrue(warnings)

    def test_stringified_array_recovered(self):
        """A strict schema mostly prevents this, but it still happens."""
        candidates, _ = gen.validate_candidates(
            {"use_cases": json.dumps([_candidate()])},
            lob_name="Distribution", requested_lens="both",
            domain_index=DOMAIN_INDEX, satisfied_names=SATISFIED,
            existing_titles=[], limit=5)
        self.assertEqual(len(candidates), 1)

    def test_unparseable_string_array(self):
        candidates, warnings = gen.validate_candidates(
            {"use_cases": "not json at all"}, lob_name="X", requested_lens="both",
            domain_index=DOMAIN_INDEX, satisfied_names=SATISFIED,
            existing_titles=[], limit=5)
        self.assertEqual(candidates, [])
        self.assertTrue(warnings)


class TestResponseSchema(unittest.TestCase):
    def test_schema_is_strict_and_complete(self):
        schema = gen.RESPONSE_SCHEMA
        self.assertEqual(schema["type"], "json_schema")
        self.assertTrue(schema["json_schema"]["strict"])
        item = schema["json_schema"]["schema"]["properties"]["use_cases"]["items"]
        for field in ("title", "description", "business_value", "effort_tshirt",
                      "lens", "required_domains"):
            self.assertIn(field, item["required"])

    def test_enums_match_module_constants(self):
        props = gen.RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]
        item = props["use_cases"]["items"]["properties"]
        self.assertEqual(tuple(item["effort_tshirt"]["enum"]), gen.EFFORT_VALUES)
        self.assertEqual(tuple(item["sub_vertical"]["enum"]), gen.SUB_VERTICALS)
        self.assertEqual(tuple(item["value_type"]["enum"]), gen.VALUE_TYPES)

    def test_schema_does_not_ask_for_a_dollar_figure(self):
        """Value is computed by the value engine; a model-supplied number would
        compete with it and invite trusting the wrong one."""
        item = gen.RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]["use_cases"]["items"]
        self.assertNotIn("estimated_value_usd", item["properties"])


class TestSummarize(unittest.TestCase):
    def test_counts_by_lens_and_flag(self):
        candidates, _ = _validate([
            _candidate(title="A", required_domains=["outage_records"]),
            _candidate(title="B", required_domains=["market_prices"],
                       is_regulatory=True, time_horizon="quick_win"),
        ])
        summary = gen.summarize_batch(candidates)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["gap"], 1)
        self.assertEqual(summary["regulatory"], 1)
        self.assertEqual(summary["quick_wins"], 1)
        self.assertEqual(summary["distinct_missing_domains"], 1)

    def test_empty_batch(self):
        self.assertEqual(gen.summarize_batch([])["total"], 0)


if __name__ == "__main__":
    unittest.main()


class TestUnwrapList(unittest.TestCase):
    """Regression: the live endpoint returned a DOUBLY-nested payload.

    `databricks-claude-sonnet-5` with a strict response_format replied with
        {"use_cases": "{\"use_cases\": [ {...} ]}"}
    — the array JSON-encoded as a string, wrapped in another copy of the envelope.
    A single json.loads retry recovers a dict where a list belongs, so perfectly
    good candidates were silently discarded. Found only by deploying.
    """

    def test_plain_list(self):
        self.assertEqual(gen.unwrap_list({"use_cases": [1, 2]}, "use_cases"), [1, 2])

    def test_single_stringified(self):
        self.assertEqual(gen.unwrap_list({"use_cases": "[1, 2]"}, "use_cases"), [1, 2])

    def test_doubly_nested_envelope(self):
        """The exact shape observed in production."""
        payload = {"use_cases": json.dumps({"use_cases": [{"title": "A"}]})}
        self.assertEqual(gen.unwrap_list(payload, "use_cases"), [{"title": "A"}])

    def test_triply_nested(self):
        inner = json.dumps({"use_cases": [{"title": "A"}]})
        payload = {"use_cases": json.dumps({"use_cases": inner})}
        self.assertEqual(gen.unwrap_list(payload, "use_cases"), [{"title": "A"}])

    def test_envelope_with_a_lone_differently_named_list(self):
        payload = {"use_cases": {"items": [{"title": "A"}]}}
        self.assertEqual(gen.unwrap_list(payload, "use_cases"), [{"title": "A"}])

    def test_ambiguous_envelope_returns_none(self):
        """Two candidate lists and no matching key — guessing would be wrong."""
        payload = {"use_cases": {"a": [1], "b": [2]}}
        self.assertIsNone(gen.unwrap_list(payload, "use_cases"))

    def test_unparseable_string(self):
        self.assertIsNone(gen.unwrap_list({"use_cases": "not json"}, "use_cases"))

    def test_missing_key_and_empty_payload(self):
        self.assertIsNone(gen.unwrap_list({"other": [1]}, "use_cases"))
        self.assertIsNone(gen.unwrap_list({}, "use_cases"))
        self.assertIsNone(gen.unwrap_list(None, "use_cases"))

    def test_depth_is_bounded(self):
        """A deeply self-referential reply must terminate rather than loop."""
        payload = {"use_cases": json.dumps({"use_cases": json.dumps(
            {"use_cases": [{"title": "deep"}]})})}
        # Each nesting level costs two iterations (parse, then descend), so a
        # tight budget gives up rather than spinning...
        self.assertIsNone(gen.unwrap_list(payload, "use_cases", max_depth=2))
        # ...while the default budget recovers it.
        self.assertEqual(gen.unwrap_list(payload, "use_cases"), [{"title": "deep"}])

    def test_validate_candidates_accepts_the_nested_shape(self):
        """End to end: the production payload must yield usable candidates."""
        nested = {"use_cases": json.dumps({"use_cases": [_candidate()]})}
        candidates, _ = gen.validate_candidates(
            nested, lob_name="Distribution", requested_lens="both",
            domain_index=DOMAIN_INDEX, satisfied_names=SATISFIED,
            existing_titles=[], limit=5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Predictive Transformer Failure")
