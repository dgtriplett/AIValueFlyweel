"""The parameterized value model: component arithmetic, bands, realized value.

tests/README.md has claimed this file exists for some time; it did not. That is
worth fixing before anything else in CI, because value_engine.py produces every
dollar figure the app shows — the portfolio total, each use case's badge, the
value-per-cost ranking that orders investment recommendations, and the numbers the
research agent recalibrates. An arithmetic error here is invisible: the app still
renders a confident figure, just the wrong one, and it is the figure a customer
takes into a funding conversation.

The evaluator is deliberately not eval() — it is pure multiplication of resolved
numbers — so these tests pin the multiplication, the coefficient bands, and above
all the MISSING-KEY behaviour, which silently zeroes a value.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import value_engine as ve  # noqa: E402


def component(multiplier, keys=(), low=1.0, high=1.0, name="c"):
    return {"name": name, "multiplier": multiplier, "assumptionKeys": list(keys),
            "lowCoeff": low, "highCoeff": high}


def formula(*components, **extra):
    return {"driver": "test", "components": list(components), **extra}


class TestComponentArithmetic(unittest.TestCase):
    def test_multiplier_times_one_assumption(self):
        result = ve.compute_value_range(
            formula(component(2.0, ["customers"])), {"customers": 4.0})
        self.assertEqual(result, {"low": 8.0, "mid": 8.0, "high": 8.0})

    def test_multiplies_every_key_in_the_product(self):
        # The core semantic: keys form a PRODUCT, not a sum.
        result = ve.compute_value_range(
            formula(component(1.0, ["a", "b", "c"])),
            {"a": 2.0, "b": 3.0, "c": 5.0})
        self.assertEqual(result["mid"], 30.0)

    def test_components_sum(self):
        result = ve.compute_value_range(
            formula(component(10.0), component(5.0), component(2.5)), {})
        self.assertEqual(result["mid"], 17.5)

    def test_low_and_high_coefficients_form_the_band(self):
        result = ve.compute_value_range(
            formula(component(100.0, low=0.6, high=1.4)), {})
        self.assertEqual(result, {"low": 60.0, "mid": 100.0, "high": 140.0})

    def test_mid_is_the_midpoint_not_the_unscaled_value(self):
        # An asymmetric band must not report the raw component as the mid.
        result = ve.compute_value_range(
            formula(component(100.0, low=0.5, high=2.0)), {})
        self.assertEqual(result["low"], 50.0)
        self.assertEqual(result["high"], 200.0)
        self.assertEqual(result["mid"], 125.0)

    def test_bands_apply_per_component_then_sum(self):
        result = ve.compute_value_range(
            formula(component(10.0, low=0.5, high=1.5),
                    component(20.0, low=0.9, high=1.1)), {})
        self.assertEqual(result["low"], 5.0 + 18.0)
        self.assertEqual(result["high"], 15.0 + 22.0)

    def test_rounds_to_cents(self):
        result = ve.compute_value_range(
            formula(component(1.0, ["x"])), {"x": 1.0 / 3.0})
        self.assertEqual(result["mid"], 0.33)


class TestMissingAndMalformedInput(unittest.TestCase):
    """The failure modes that produce a confident wrong number rather than an error."""

    def test_missing_assumption_key_zeroes_the_component(self):
        """A typo'd or unseeded key silently yields $0 — pinned so it stays known.

        This is the model's most consequential quiet behaviour: it makes a use case
        read as worthless rather than as unquantified. It is the right default (a
        missing input must never inflate a number a customer takes to a funding
        conversation), but it is why the research agent's parse_assumptions rejects
        invented keys instead of writing them.
        """
        result = ve.compute_value_range(
            formula(component(5.0, ["nonexistent_key"])), {"customers": 4.0})
        self.assertEqual(result["mid"], 0.0)

    def test_one_missing_key_zeroes_only_its_own_component(self):
        result = ve.compute_value_range(
            formula(component(5.0, ["missing"]), component(7.0)), {})
        self.assertEqual(result["mid"], 7.0)

    def test_zero_assumption_zeroes_the_component(self):
        # Correct for a wires-only utility: research sets fuel/capacity keys to 0.
        result = ve.compute_value_range(
            formula(component(5.0, ["fuelCostPerMWh"])), {"fuelCostPerMWh": 0.0})
        self.assertEqual(result["mid"], 0.0)

    def test_none_assumption_is_treated_as_zero(self):
        result = ve.compute_value_range(
            formula(component(5.0, ["k"])), {"k": None})
        self.assertEqual(result["mid"], 0.0)

    def test_none_formula(self):
        self.assertIsNone(ve.compute_value_range(None, {}))

    def test_unparseable_json_string(self):
        self.assertIsNone(ve.compute_value_range("{not json", {}))

    def test_non_dict_json(self):
        self.assertIsNone(ve.compute_value_range("[1,2,3]", {}))

    def test_empty_components_with_no_static_fallback(self):
        self.assertIsNone(ve.compute_value_range(formula(), {}))

    def test_missing_multiplier_defaults_to_zero(self):
        # Not 1: a component with no multiplier is incomplete data, and defaulting
        # to 1 would invent value out of an omission.
        result = ve.compute_value_range(
            formula({"name": "c", "assumptionKeys": ["a"]}), {"a": 100.0})
        self.assertEqual(result["mid"], 0.0)

    def test_missing_coefficients_default_to_one(self):
        result = ve.compute_value_range(
            formula({"name": "c", "multiplier": 10.0}), {})
        self.assertEqual(result, {"low": 10.0, "mid": 10.0, "high": 10.0})

    def test_null_coefficients_default_to_one(self):
        # JSON nulls arrive from the DB where a coefficient was never set.
        result = ve.compute_value_range(
            formula({"name": "c", "multiplier": 10.0,
                     "lowCoeff": None, "highCoeff": None}), {})
        self.assertEqual(result["low"], 10.0)
        self.assertEqual(result["high"], 10.0)

    def test_null_assumption_keys_list(self):
        result = ve.compute_value_range(
            formula({"name": "c", "multiplier": 4.0, "assumptionKeys": None}), {})
        self.assertEqual(result["mid"], 4.0)

    def test_string_numbers_are_coerced(self):
        # asyncpg returns NUMERIC as Decimal and jsonb numbers can arrive as strings.
        result = ve.compute_value_range(
            formula(component("2.5", ["a"])), {"a": "4"})
        self.assertEqual(result["mid"], 10.0)


class TestJsonStringInput(unittest.TestCase):
    """Formulas arrive as a jsonb string from asyncpg as often as a dict."""

    def test_json_string_is_parsed(self):
        import json
        as_string = json.dumps(formula(component(3.0, ["a"])))
        self.assertEqual(
            ve.compute_value_range(as_string, {"a": 2.0}),
            ve.compute_value_range(formula(component(3.0, ["a"])), {"a": 2.0}))


class TestStaticFallback(unittest.TestCase):
    """Some use cases carry a flat low/high band instead of a driver formula."""

    def test_static_band_is_used_when_there_are_no_components(self):
        result = ve.compute_value_range(
            {"components": [], "low_mm": 1.0, "mid_mm": 3.0, "high_mm": 5.0}, {})
        self.assertEqual(result, {"low": 1.0, "mid": 3.0, "high": 5.0})

    def test_components_take_precedence_over_a_static_band(self):
        result = ve.compute_value_range(
            formula(component(42.0), low_mm=1.0, high_mm=5.0), {})
        self.assertEqual(result["mid"], 42.0)

    def test_static_band_without_mid_yields_none_mid(self):
        """Documents a real edge: mid_mm is not derived from low/high.

        compute_value() returns that None straight through, so a badge renders
        blank rather than wrong. Recorded because the components path DOES derive
        mid, and the asymmetry is surprising.
        """
        result = ve.compute_value_range(
            {"components": [], "low_mm": 2.0, "high_mm": 6.0}, {})
        self.assertIsNone(result["mid"])


class TestComputeValue(unittest.TestCase):
    def test_returns_the_mid(self):
        self.assertEqual(
            ve.compute_value(formula(component(10.0, low=0.5, high=1.5)), {}), 10.0)

    def test_returns_none_for_an_unquantified_use_case(self):
        self.assertIsNone(ve.compute_value(None, {}))


class TestUseCaseValue(unittest.TestCase):
    """The shared helper the two recommenders rank by; must never return None."""

    def test_returns_the_mid_for_a_quantified_use_case(self):
        self.assertEqual(
            ve.use_case_value(
                {"hypothesized_value_json": formula(component(8.0))}, {}), 8.0)

    def test_unquantified_is_zero_not_none(self):
        # Both recommenders sort on this; a None would raise a TypeError mid-ranking.
        self.assertEqual(ve.use_case_value({"hypothesized_value_json": None}, {}), 0.0)
        self.assertEqual(ve.use_case_value({}, {}), 0.0)


class TestAssetCost(unittest.TestCase):
    def test_explicit_costs_win(self):
        self.assertEqual(
            ve.asset_cost({"ingest_cost_low": 10_000, "ingest_cost_high": 20_000,
                           "ingest_effort": "XL"}),
            (10_000.0, 20_000.0))

    def test_falls_back_to_the_effort_band(self):
        self.assertEqual(ve.asset_cost({"ingest_effort": "L"}),
                         (400_000.0, 1_000_000.0))

    def test_unknown_effort_defaults_to_medium(self):
        self.assertEqual(ve.asset_cost({"ingest_effort": "enormous"}),
                         (150_000.0, 400_000.0))

    def test_missing_effort_defaults_to_medium(self):
        self.assertEqual(ve.asset_cost({}), (150_000.0, 400_000.0))
        self.assertEqual(ve.asset_cost({"ingest_effort": None}),
                         (150_000.0, 400_000.0))

    def test_partial_explicit_cost_falls_back(self):
        # Only one bound set is not a usable band.
        self.assertEqual(ve.asset_cost({"ingest_cost_low": 5_000,
                                        "ingest_effort": "S"}),
                         (50_000.0, 150_000.0))

    def test_effort_bands_are_ordered_and_non_overlapping(self):
        """A larger t-shirt size must never be cheaper — it orders the UI."""
        bands = [ve.EFFORT_COST[size] for size in ("S", "M", "L", "XL")]
        for (low, high), (next_low, next_high) in zip(bands, bands[1:]):
            self.assertLess(low, next_low)
            self.assertLess(high, next_high)
            self.assertLessEqual(high, next_low)
        for low, high in bands:
            self.assertLess(low, high)


class TestComputeRealized(unittest.TestCase):
    def test_override_wins_over_a_formula(self):
        result = ve.compute_realized(
            {"realized_override_enabled": True, "realized_override_amount": 12.5,
             "realized_override_note": "per finance",
             "realized_value_json": formula(component(99.0))}, {})
        self.assertEqual(result, {"mode": "override", "value": 12.5,
                                  "note": "per finance"})

    def test_override_enabled_without_an_amount(self):
        # Someone ticked the box and did not fill in the number: report the mode
        # honestly with no value rather than falling through to the formula, which
        # would show a figure finance did not sign off on.
        result = ve.compute_realized(
            {"realized_override_enabled": True, "realized_override_amount": None,
             "realized_value_json": formula(component(99.0))}, {})
        self.assertEqual(result["mode"], "override")
        self.assertIsNone(result["value"])

    def test_calculated_from_components(self):
        result = ve.compute_realized(
            {"realized_value_json": formula(component(2.0, ["a"]))}, {"a": 3.0})
        self.assertEqual(result, {"mode": "calculated", "value": 6.0, "note": None})

    def test_calculated_ignores_coefficients(self):
        """Realized value is measured, so it has no low/high band by design."""
        result = ve.compute_realized(
            {"realized_value_json": formula(component(10.0, low=0.1, high=9.9))}, {})
        self.assertEqual(result["value"], 10.0)

    def test_calculated_uses_shared_assumptions(self):
        # The point of sharing: a global assumption edit re-quantifies realized too.
        row = {"realized_value_json": formula(component(1.0, ["customers"]))}
        self.assertEqual(ve.compute_realized(row, {"customers": 100.0})["value"], 100.0)
        self.assertEqual(ve.compute_realized(row, {"customers": 200.0})["value"], 200.0)

    def test_no_realized_value(self):
        self.assertEqual(ve.compute_realized({}, {}),
                         {"mode": "none", "value": None, "note": None})

    def test_empty_components_is_none_not_zero(self):
        # "Not measured" and "measured as zero" must not look the same.
        result = ve.compute_realized({"realized_value_json": formula()}, {})
        self.assertEqual(result["mode"], "none")
        self.assertIsNone(result["value"])


class TestPortfolioConsistency(unittest.TestCase):
    """Properties that must hold across the whole portfolio, not per formula."""

    def test_low_never_exceeds_high(self):
        cases = [
            formula(component(10.0, low=0.5, high=1.5)),
            formula(component(10.0), component(5.0, low=0.9, high=1.2)),
            formula(component(0.0)),
        ]
        for case in cases:
            result = ve.compute_value_range(case, {})
            self.assertLessEqual(result["low"], result["high"], case)
            self.assertLessEqual(result["low"], result["mid"], case)
            self.assertLessEqual(result["mid"], result["high"], case)

    def test_scaling_one_assumption_scales_value_linearly(self):
        """Pins linearity, which is what makes the research recalibration sound.

        Research adjusts assumptions and the portfolio total moves. If the model
        were not linear in each assumption, the reported effect of a calibration
        would not be attributable to it.
        """
        model = formula(component(2.0, ["customers"]), component(3.0, ["outages"]))
        base = ve.compute_value_range(model, {"customers": 10.0, "outages": 5.0})
        doubled = ve.compute_value_range(model, {"customers": 20.0, "outages": 5.0})
        self.assertEqual(doubled["mid"] - base["mid"], 2.0 * 10.0)

class TestShippedCatalog(unittest.TestCase):
    """The shipped data must satisfy the model's requirements.

    These are the tests that catch a catalog typo. Because a missing assumption key
    silently evaluates to $0 rather than raising, a mistyped key ships looking like
    a use case that is genuinely worth nothing — and nobody investigates a zero.
    """

    @staticmethod
    def _seed():
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent / "scripts" / "seed_data.json"
        return json.loads(path.read_text())

    @staticmethod
    def _catalog():
        import sys as _sys
        from pathlib import Path
        scripts = str(Path(__file__).parent.parent / "scripts")
        if scripts not in _sys.path:
            _sys.path.insert(0, scripts)
        import pu_catalog
        return pu_catalog

    def test_every_assumption_key_used_by_the_catalog_is_seeded(self):
        """An unseeded key resolves to 0 and silently zeroes its component."""
        seeded = {a["key"] for a in self._seed()["value_assumptions"]}
        missing = {}
        for entry in self._catalog().EXTRA_USE_CASES:
            for comp in entry.get("value_components") or []:
                for key in comp.get("assumptionKeys") or []:
                    if key not in seeded:
                        missing.setdefault(key, []).append(entry.get("id"))
        self.assertEqual(
            missing, {},
            "these assumption keys are referenced by the catalog but never seeded, "
            f"so the components using them evaluate to $0: {missing}")

    def test_every_assumption_key_used_by_seeded_use_cases_is_seeded(self):
        seed = self._seed()
        seeded = {a["key"] for a in seed["value_assumptions"]}
        missing = {}
        for entry in seed.get("use_cases") or []:
            model = ve._as_dict(entry.get("hypothesized_value_json"))
            for comp in (model or {}).get("components") or []:
                for key in comp.get("assumptionKeys") or []:
                    if key not in seeded:
                        missing.setdefault(key, []).append(entry.get("title"))
        self.assertEqual(missing, {},
                         f"unseeded assumption keys in seed_data.json: {missing}")

    def test_every_catalog_component_evaluates_to_a_number(self):
        """A formula returning None renders a blank badge, reading as "no value"
        rather than "malformed data" — so a typo would ship as an omission."""
        catalog = self._catalog()
        assumptions = {a["key"]: 1.0 for a in self._seed()["value_assumptions"]}
        unevaluable = []
        for entry in catalog.EXTRA_USE_CASES:
            components = entry.get("value_components")
            if not components:
                continue
            result = ve.compute_value_range({"components": components}, assumptions)
            if result is None or result["mid"] is None:
                unevaluable.append(entry.get("id"))
        self.assertEqual(unevaluable, [],
                         f"these catalog formulas do not evaluate: {unevaluable}")

    def test_no_catalog_use_case_is_accidentally_worthless(self):
        """With every assumption at a realistic value, no component sums to zero.

        Distinguishes "deliberately unquantified" (no components at all, which is
        allowed) from "quantified but evaluating to nothing", which always means a
        zero multiplier or a key that does not resolve.
        """
        assumptions = {a["key"]: float(a["value"] or 0) or 1.0
                       for a in self._seed()["value_assumptions"]}
        zeroed = []
        for entry in self._catalog().EXTRA_USE_CASES:
            components = entry.get("value_components")
            if not components:
                continue   # unquantified by design
            result = ve.compute_value_range({"components": components}, assumptions)
            if result and result["mid"] == 0.0:
                zeroed.append(entry.get("id"))
        self.assertEqual(zeroed, [],
                         f"these are quantified but evaluate to $0: {zeroed}")

    def test_catalog_bands_are_well_formed(self):
        offenders = []
        for entry in self._catalog().EXTRA_USE_CASES:
            for comp in entry.get("value_components") or []:
                low = comp.get("lowCoeff", 1)
                high = comp.get("highCoeff", 1)
                if low is None or high is None or low > high or low < 0:
                    offenders.append((entry.get("id"), comp.get("name"), low, high))
        self.assertEqual(offenders, [],
                         f"components with an inverted or negative band: {offenders}")


if __name__ == "__main__":
    unittest.main()
