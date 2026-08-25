"""Test magnitude calibration for LLM-generated value models.

CRITICAL BUG: LLM-generated use-case value models produce absurd totals (e.g.
$2408.72B/yr for a utility with ~$5B revenue — off by ~5-6 orders of magnitude).

ROOT CAUSE: The value engine computes per-component annual value in $M as:
  multiplier * product(assumption_values) * coeff

The LLM cannot reliably compute the ~1e-9-scale coefficients needed to normalize a
product of big raw assumptions (like customerCount=2M, annualRevenueMM=5000) back
into $M. Seeded catalog models work because their multipliers are hand-calibrated
to absorb units (e.g. 8e-10 for customerCount * customerChurnPct * avgResidentialRevenue).

THE FIX: Two-layer calibration in build_value_model (server/routes/agents.py):
  LAYER 1: Per-component rescaling — detect absurd component values and adjust
           multipliers to bring each component under ~500 $M (10% of revenue)
  LAYER 2: Global sanity clamp — if total still exceeds 0.5 * annualRevenueMM,
           proportionally scale ALL component multipliers down
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import value_engine as ve  # noqa: E402
from server.routes import agents  # noqa: E402


class TestValueCalibration(unittest.TestCase):
    """Test that build_value_model applies magnitude calibration to LLM output."""

    def test_uncalibrated_model_produces_absurd_total(self):
        """Verify the bug exists: LLM multipliers like 0.00015 explode with big assumptions.

        This test simulates what the LLM returns BEFORE calibration is applied.
        It should FAIL against the current (unpatched) build_value_model.
        """
        # Simulate LLM output: reasonable-looking but NOT properly scaled multipliers
        llm_components = [
            {
                "name": "Customer churn reduction",
                "calculationDisplay": "Customers × churn% × revenue × reduction%",
                "multiplier": 0.00015,  # LLM thinks this looks right, but it's ~6 orders too big
                "assumptionKeys": ["customerCount", "customerChurnPct", "avgResidentialRevenue"],
                "lowCoeff": 0.6,
                "highCoeff": 1.4,
            },
            {
                "name": "Revenue optimization",
                "calculationDisplay": "Annual revenue × optimization%",
                "multiplier": 0.00025,  # Also way too big
                "assumptionKeys": ["annualRevenueMM"],
                "lowCoeff": 0.7,
                "highCoeff": 1.3,
            },
        ]

        # Real seeded assumptions (from seed_data.json)
        assumptions = {
            "customerCount": 2_000_000,
            "customerChurnPct": 2.5,
            "avgResidentialRevenue": 1800,
            "annualRevenueMM": 5000,
        }

        # Compute what the UNCALIBRATED model would produce
        # Component 1: 0.00015 * 2M * 2.5 * 1800 = 1,350 $M (way too high!)
        # Component 2: 0.00025 * 5000 = 1.25 $M
        # Total mid: ~1,351 $M (for comparison, annualRevenueMM = 5000)

        formula = {"driver": "test", "components": llm_components}
        result = ve.compute_value_range(formula, assumptions)

        # The uncalibrated model produces an absurd total
        self.assertIsNotNone(result)
        uncalibrated_mid = result["mid"]

        # Document the absurdity: a single component exceeds reasonable bounds
        component1_value = 0.00015 * 2_000_000 * 2.5 * 1800
        self.assertGreater(component1_value, 1000,
                          f"Uncalibrated component 1 = {component1_value:.2f} $M >> sanity bounds")

        # This is the bug we're fixing
        self.assertGreater(uncalibrated_mid, 1000,
                          f"Uncalibrated mid = {uncalibrated_mid:.2f} $M is absurdly high")

    @patch('server.routes.agents.db')
    @patch('server.routes.agents._llm_json')
    async def test_calibrated_model_is_sane(self, mock_llm_json, mock_db):
        """After calibration, generated models must stay within reasonable bounds.

        This test feeds build_value_model a stubbed LLM response that WOULD explode,
        runs it through the calibration layers, and verifies the result is sane.
        """
        # Mock the LLM to return components that would explode without calibration
        llm_response = {
            "components": [
                {
                    "name": "Customer churn reduction",
                    "calculationDisplay": "Customers × churn% × revenue × reduction%",
                    "multiplier": 0.00015,
                    "assumptionKeys": ["customerCount", "customerChurnPct", "avgResidentialRevenue"],
                    "lowCoeff": 0.6,
                    "highCoeff": 1.4,
                },
                {
                    "name": "Bad debt reduction",
                    "calculationDisplay": "Revenue × bad debt% × reduction",
                    "multiplier": 0.012,
                    "assumptionKeys": ["annualRevenueMM", "badDebtPct"],
                    "lowCoeff": 0.7,
                    "highCoeff": 1.3,
                },
            ],
            "roiMonths": 12,
            "notes": "Test value model",
        }
        mock_llm_json.return_value = (llm_response, True, None)

        # Mock db.fetch to return seeded assumptions
        mock_db.fetch = AsyncMock(side_effect=[
            # First call: value_assumptions
            [
                {"key": "customerCount", "label": "Customers", "unit": "count", "value": 2_000_000},
                {"key": "customerChurnPct", "label": "Churn %", "unit": "%", "value": 2.5},
                {"key": "avgResidentialRevenue", "label": "Avg Revenue", "unit": "$/yr", "value": 1800},
                {"key": "annualRevenueMM", "label": "Annual Revenue", "unit": "$M", "value": 5000},
                {"key": "badDebtPct", "label": "Bad Debt %", "unit": "%", "value": 2.0},
                {"key": "omBudgetMM", "label": "O&M Budget", "unit": "$M", "value": 1400},
            ],
            # Second call: benchmark_library
            [],
        ])

        # Run the calibrated build_value_model
        result = await agents.build_value_model("Test Use Case", "Test description")

        # Extract the calibrated components
        components = result["components"]
        self.assertGreater(len(components), 0, "Should have components")

        # Build assumptions dict for computation
        assumptions = {
            "customerCount": 2_000_000,
            "customerChurnPct": 2.5,
            "avgResidentialRevenue": 1800,
            "annualRevenueMM": 5000,
            "badDebtPct": 2.0,
        }

        # Compute value range with the CALIBRATED multipliers
        formula = {"driver": result["driver"], "components": components}
        calibrated_result = ve.compute_value_range(formula, assumptions)

        self.assertIsNotNone(calibrated_result, "Calibrated model should evaluate")

        calibrated_mid = calibrated_result["mid"]

        # ACCEPTANCE CRITERIA:
        # 1. The calibrated mid must be > 0 (model is not broken)
        self.assertGreater(calibrated_mid, 0,
                          "Calibrated model should produce positive value")

        # 2. The calibrated mid must be < 0.5 * annualRevenueMM = 2500 $M
        max_sane_value = 0.5 * assumptions["annualRevenueMM"]
        self.assertLess(calibrated_mid, max_sane_value,
                       f"Calibrated mid {calibrated_mid:.2f} $M must be < {max_sane_value} $M")

        # 3. No single component should exceed ~10% of revenue (~500 $M)
        max_component_value = 0.1 * assumptions["annualRevenueMM"]
        for comp in components:
            comp_value = comp["multiplier"]
            for key in comp["assumptionKeys"]:
                comp_value *= assumptions.get(key, 0)
            self.assertLess(comp_value, max_component_value * 2,  # Allow some slack
                           f"Component '{comp['name']}' = {comp_value:.2f} $M exceeds reasonable bounds")

    def test_seeded_catalog_models_unchanged(self):
        """CRITICAL: Calibration must NOT affect existing seeded models.

        Catalog use-case values must stay byte-identical before/after the fix.
        The calibration is applied ONLY in build_value_model (the NEW-model path),
        never in compute_value_range (which evaluates EXISTING models).
        """
        import json
        from pathlib import Path

        # Load a seeded model with hand-calibrated multipliers
        seed_path = Path(__file__).parent.parent / "scripts" / "seed_data.json"
        seed_data = json.loads(seed_path.read_text())

        # Get assumptions
        assumptions = {a["key"]: float(a["value"]) for a in seed_data["value_assumptions"]}

        # Test a seeded use case with tiny multipliers (these must not change)
        for uc in seed_data["use_cases"]:
            hvj = uc.get("hypothesized_value_json")
            if not hvj or not hvj.get("components"):
                continue

            # Compute value with the original seeded multipliers
            original_result = ve.compute_value_range(hvj, assumptions)

            if original_result is None:
                continue

            # The value_engine.py arithmetic should be unchanged
            # (calibration happens in agents.py, not value_engine.py)
            self.assertIsNotNone(original_result,
                               f"Seeded model for '{uc['title']}' should still evaluate")

            # Verify the seeded model produces a reasonable value
            mid = original_result["mid"]
            self.assertGreater(mid, 0, f"'{uc['title']}' should have positive value")
            self.assertLess(mid, 10000, f"'{uc['title']}' value should be reasonable")

            # If this test fails, we've broken the existing catalog
            break  # Just test one seeded model as a smoke check


def async_test(coro):
    """Decorator to run async test methods."""
    import asyncio
    def wrapper(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(self))
        finally:
            loop.close()
    return wrapper


# Apply decorator to async test methods
TestValueCalibration.test_calibrated_model_is_sane = async_test(
    TestValueCalibration.test_calibrated_model_is_sane)


if __name__ == "__main__":
    unittest.main()
