"""Portfolio-level plausibility of the value model.

WHY THIS FILE EXISTS
--------------------
test_value_engine.py verifies the arithmetic: multiplier x assumptions, bands, rounding,
malformed input. Every one of those tests passed while the portfolio claimed $3,498M/yr
against $12,500M of revenue — 28% of a utility's total revenue, from analytics.

The arithmetic was never wrong. The claims were. And the error was invisible one use case
at a time: "2.5% of the distribution capital plan" is a defensible sentence, and every
multiplier in the catalog was a sentence like that. What nobody had done was ADD THEM UP.
Twenty-eight use cases each took a slice of distributionCapexMM and together claimed
67.2% of it — i.e. that analytics would eliminate two-thirds of the capital plan.

So this file tests the one property a per-formula test cannot see: what the portfolio
claims IN AGGREGATE against each denominator. It reads the seed catalog, so it fails in
CI on the data as shipped rather than waiting for someone to deploy and eyeball a total.

WHY THE THRESHOLDS ARE WHAT THEY ARE
------------------------------------
They are deliberately loose — a limit on the indefensible, not a target. Utilities
publicly discuss deferring a few percent of a capital plan and 10-20% O&M efficiency from
digital programs. A portfolio claiming 15% of the capital plan might be wrong but is
arguable; one claiming 67% is not. The point is to catch the next 67%, not to police
whether 6% should have been 5%.
"""
import json
import os
import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

ROOT = Path(__file__).parent.parent
SEED = ROOT / "scripts" / "seed_data.json"

# Ceiling on the TOTAL share of each denominator the whole portfolio may claim.
# Exceeding one of these means the catalog is asserting that analytics removes that
# fraction of a utility's capital plan, O&M budget or revenue.
AGGREGATE_CEILINGS = {
    "distributionCapexMM": 0.15,   # was 0.672 — "two-thirds of the capital plan"
    "transmissionCapexMM": 0.15,   # was 0.550
    "capitalBudgetMM": 0.05,       # was 0.053, four planning tools
    "annualRevenueMM": 0.02,       # was 0.041; revenue-denominated claims add up fast
    "omBudgetMM": 0.25,            # currently 0.176 and left alone deliberately
    "annualFuelSpendMM": 0.15,     # was 0.650 across 59 generation/nuclear use cases
}

# Assumptions that are NOT budgets, so "% claimed" is meaningless for them. A multiplier
# against saidiMinuteValueMM is a number of minutes; against generationFleetMW it is
# dollars per MW; against transformerCount it is a failure rate. Summing those and
# comparing to 1.0 would be a category error — 8 minutes of SAIDI improvement is not
# "800% of" anything. They are covered by the per-use-case dollar ceiling below instead.
RATE_NOT_SHARE = {
    "saidiMinuteValueMM",
    "generationFleetMW",
    "transformerCount",
    "regulatoryPenaltyRiskMM",   # a risk pot several use cases each mitigate part of
    "procurementSpendMM",
    "customerCount",
    "workforceSize",
    "tdLineMiles",
    "callsPerYear",
    "avgStormCostMM",
    "rateCasePrepCostMM",        # a $3M prep cost, not a budget to take a share of
    "costPerCall",
    "badDebtPct",
    "currentSAIDI",
    "stormsPerYear",
}

# No single use case may claim more than this share of one denominator. Catches the
# reverse failure: an aggregate that passes only because one use case dominates.
SINGLE_CEILING = 0.02


def _formulas():
    """(id, title, formula) for every seeded use case with a component formula."""
    data = json.loads(SEED.read_text())
    rows = data.get("use_cases") if isinstance(data, dict) else data
    out = []
    for row in rows or []:
        formula = row.get("hypothesized_value_json")
        if isinstance(formula, str):
            try:
                formula = json.loads(formula)
            except ValueError:
                continue
        if isinstance(formula, dict) and formula.get("components"):
            out.append((row.get("id"), row.get("title") or "?", formula))
    return out


class TestSeedCatalogIsPresent(unittest.TestCase):
    """Guards every other test here from passing vacuously.

    If the seed moves or its shape changes, the assertions below would find zero
    formulas and report success on unexamined data — the exact failure mode that let the
    original problem ship.
    """

    def test_seed_exists(self):
        self.assertTrue(SEED.exists(), f"{SEED} is missing")

    def test_formulas_are_found(self):
        formulas = _formulas()
        self.assertGreater(len(formulas), 100,
                           "expected the full use-case catalog; found "
                           f"{len(formulas)} component formulas")


class TestAggregateClaims(unittest.TestCase):
    """The test that would have caught the $3.5B portfolio."""

    @classmethod
    def setUpClass(cls):
        cls.by_base = defaultdict(list)
        for use_case_id, title, formula in _formulas():
            for component in formula["components"]:
                keys = component.get("assumptionKeys") or []
                # Only single-key components are shares of a denominator. A multi-key
                # component (fleet MW x capacity price x rate) is a physical calculation,
                # not a percentage of anything, so summing it would be meaningless.
                if len(keys) != 1 or keys[0] in RATE_NOT_SHARE:
                    continue
                cls.by_base[keys[0]].append(
                    (float(component.get("multiplier") or 0), use_case_id, title))

    def test_no_denominator_is_over_claimed(self):
        for base, ceiling in AGGREGATE_CEILINGS.items():
            claims = self.by_base.get(base) or []
            if not claims:
                continue
            total = sum(multiplier for multiplier, _, _ in claims)
            top = sorted(claims, reverse=True)[:5]
            self.assertLessEqual(
                total, ceiling,
                f"\n{len(claims)} use cases together claim {total * 100:.1f}% of "
                f"{base}, above the {ceiling * 100:.0f}% ceiling.\n"
                "Each one may look reasonable alone; the portfolio is what the customer "
                "sees. Largest contributors:\n"
                + "\n".join(f"    {m * 100:6.3f}%  id={i}  {t}" for m, i, t in top))

    def test_no_single_use_case_dominates_a_denominator(self):
        for base in self.by_base:
            for multiplier, use_case_id, title in self.by_base[base]:
                self.assertLessEqual(
                    multiplier, SINGLE_CEILING,
                    f"id={use_case_id} ({title}) alone claims "
                    f"{multiplier * 100:.2f}% of {base}, above the "
                    f"{SINGLE_CEILING * 100:.0f}% single-use-case ceiling")

    def test_every_denominator_with_claims_has_a_ceiling(self):
        """A new denominator must get a reviewed ceiling, not skip the check.

        Without this, adding use cases against a new base silently opts out of the only
        test that looks at aggregate plausibility.
        """
        unbounded = {}
        for base, claims in self.by_base.items():
            if base in AGGREGATE_CEILINGS:
                continue
            total = sum(m for m, _, _ in claims)
            # Small denominators (a penalty-risk pot, a per-unit cost) are not shares of
            # a budget and legitimately sum past 1.0; only flag bases that look like a
            # percentage-of-a-big-number family.
            if len(claims) >= 5:
                unbounded[base] = (len(claims), total)
        self.assertEqual(
            unbounded, {},
            "these denominators carry percentage-style claims from several use cases but "
            f"have no ceiling in AGGREGATE_CEILINGS: {unbounded}")


class TestPortfolioTotalIsDefensible(unittest.TestCase):
    """The headline number, computed against the seeded assumptions.

    This is what an executive is shown first and what Genie quotes, so it is worth
    asserting directly rather than only through its components.
    """

    @classmethod
    def setUpClass(cls):
        data = json.loads(SEED.read_text())
        rows = (data.get("value_assumptions") if isinstance(data, dict) else None) or []
        cls.assumptions = {}
        for row in rows:
            key = row.get("key")
            if key is not None:
                try:
                    cls.assumptions[key] = float(row.get("value") or 0)
                except (TypeError, ValueError):
                    pass

    def _total(self):
        from server.value_engine import compute_value_range

        total = 0.0
        for _, _, formula in _formulas():
            band = compute_value_range(formula, self.assumptions) or {}
            total += band.get("mid") or 0.0
        return total

    def test_assumptions_were_loaded(self):
        """Otherwise every value computes to zero and the ratio test below passes on
        nothing at all."""
        self.assertIn("annualRevenueMM", self.assumptions,
                      "seed_data.json has no annualRevenueMM assumption")
        self.assertGreater(self.assumptions["annualRevenueMM"], 0)

    def test_total_is_a_believable_share_of_revenue(self):
        """28% of a utility's revenue from analytics is not a number anyone defends.

        The ceiling is 15%: high enough that an ambitious portfolio passes, low enough
        that the original $3,498M (28.0%) fails.
        """
        revenue = self.assumptions["annualRevenueMM"]
        total = self._total()
        share = total / revenue
        self.assertLessEqual(
            share, 0.15,
            f"portfolio claims ${total:,.0f}M/yr against ${revenue:,.0f}M revenue "
            f"({share * 100:.1f}%). Above ~15% the total stops being credible "
            "regardless of how each use case is justified.")

    def test_no_single_use_case_is_implausibly_large(self):
        """One $125M use case discredits the other 238.

        The old maximum was $125.4M for DER & EV Charging Management — 1% of the
        company's revenue from one analytics use case.

        The ceiling is 0.6% rather than a round 0.5% because the largest remaining use
        case, self-healing distribution networks at 20% SAIDI reduction, is genuinely
        supported by deployed FLISR results. Tightening the threshold until it failed
        would mean overriding a defensible number to satisfy a round one.
        """
        from server.value_engine import compute_value_range

        revenue = self.assumptions["annualRevenueMM"]
        worst = (0.0, None)
        for _, title, formula in _formulas():
            mid = (compute_value_range(formula, self.assumptions) or {}).get("mid") or 0.0
            if mid > worst[0]:
                worst = (mid, title)
        self.assertLessEqual(
            worst[0], revenue * 0.006,
            f"{worst[1]!r} alone claims ${worst[0]:,.1f}M/yr, over 0.6% of total "
            "revenue for a single use case")


if __name__ == "__main__":
    unittest.main()
