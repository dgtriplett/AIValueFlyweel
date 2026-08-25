"""Value model magnitude calibration tests.

Tests the shared calibrate_components() helper and verifies it works correctly
for both NEW models (via build_value_model) and EXISTING models (via backfill).
"""
import unittest

from server.value_engine import calibrate_components, compute_value_range


class TestValueCalibration(unittest.TestCase):
    """Test value model magnitude calibration."""

    def setUp(self):
        """Set up test fixtures."""
        # Typical assumption values (matching seed defaults)
        self.assumptions = {
            "annualRevenueMM": 5000.0,
            "customerCount": 2_000_000.0,
            "omBudgetMM": 1400.0,
            "transformerCount": 500_000.0,
            "tdLineMiles": 15_000.0,
        }
        self.annual_revenue = self.assumptions["annualRevenueMM"]

    def test_uncalibrated_bug_241_style_model_is_absurd(self):
        """Verify that an uncalibrated #241-style model produces billion-dollar values.

        This test documents the BUG: a model with multiplier 0.00015 chaining
        annualRevenueMM * customerCount produces $1500B for a single component,
        which is absurd for a utility with $5B revenue.
        """
        # Real #241-style uncalibrated component
        uncalibrated_components = [
            {
                "name": "Revenue recovery from theft detection",
                "calculationDisplay": "0.00015 x Annual Revenue x Customer Count",
                "multiplier": 0.00015,
                "assumptionKeys": ["annualRevenueMM", "customerCount"],
                "lowCoeff": 0.7,
                "highCoeff": 1.3,
            }
        ]

        # Compute what this produces WITHOUT calibration
        model = {"components": uncalibrated_components}
        rng = compute_value_range(model, self.assumptions)

        # This is the BUG: should produce ~tens of $M, but produces billions
        self.assertIsNotNone(rng)
        # With annualRevenueMM=5000, customerCount=2M, multiplier=0.00015:
        # raw = 0.00015 * 5000 * 2000000 = 1,500,000 $M = $1.5 trillion
        # mid with coeff ~1.0 = $1.5 trillion
        self.assertGreater(rng["mid"], 1_000_000,  # Over $1B is absurd
                          "Uncalibrated model should produce absurd billion-dollar values")

    def test_calibrated_241_style_model_is_sane(self):
        """Verify calibrate_components brings a #241-style model into sane range.

        After calibration:
        - No single component should exceed 5% of annualRevenueMM (~250 $M)
        - Total mid should not exceed 25% of annualRevenueMM (~1250 $M)
        """
        uncalibrated_components = [
            {
                "name": "Revenue recovery from theft detection",
                "calculationDisplay": "0.00015 x Annual Revenue x Customer Count",
                "multiplier": 0.00015,
                "assumptionKeys": ["annualRevenueMM", "customerCount"],
                "lowCoeff": 0.7,
                "highCoeff": 1.3,
            }
        ]

        # Apply calibration
        calibrated = calibrate_components(
            uncalibrated_components, self.assumptions, self.annual_revenue
        )

        # Verify each component is under 5% ceiling
        per_component_ceiling = 0.05 * self.annual_revenue  # 250 $M
        for comp in calibrated:
            comp_value = comp["multiplier"]
            for key in comp["assumptionKeys"]:
                comp_value *= self.assumptions.get(key, 0)
            self.assertLessEqual(comp_value, per_component_ceiling,
                                f"Component '{comp['name']}' exceeds per-component ceiling")

        # Verify total is under 25% ceiling
        model = {"components": calibrated}
        rng = compute_value_range(model, self.assumptions)
        global_ceiling = 0.25 * self.annual_revenue  # 1250 $M
        self.assertLessEqual(rng["mid"], global_ceiling,
                            f"Total mid {rng['mid']} exceeds global ceiling {global_ceiling}")

    def test_calibration_with_multiple_components(self):
        """Test calibration with multiple components that stack up."""
        # 6 components each trying to claim 500 $M (10% of revenue)
        # Without calibration, total would be 3000 $M (60% of revenue)
        components = []
        for i in range(6):
            components.append({
                "name": f"Component {i+1}",
                "calculationDisplay": "O&M efficiency",
                "multiplier": 500.0 / self.assumptions["omBudgetMM"],  # targets 500 $M
                "assumptionKeys": ["omBudgetMM"],
                "lowCoeff": 0.9,
                "highCoeff": 1.1,
            })

        # Without calibration, total would be ~3000 $M
        uncalibrated_model = {"components": components}
        uncalibrated_rng = compute_value_range(uncalibrated_model, self.assumptions)
        self.assertGreater(uncalibrated_rng["mid"], 2500,
                          "Uncalibrated 6-component model should exceed 2500 $M")

        # With calibration, should be clamped to 1250 $M (25% of 5000)
        calibrated = calibrate_components(components, self.assumptions, self.annual_revenue)
        calibrated_model = {"components": calibrated}
        calibrated_rng = compute_value_range(calibrated_model, self.assumptions)

        global_ceiling = 0.25 * self.annual_revenue  # 1250 $M
        self.assertLessEqual(calibrated_rng["mid"], global_ceiling,
                            f"Calibrated mid {calibrated_rng['mid']} exceeds ceiling {global_ceiling}")

    def test_already_sane_model_unchanged(self):
        """Test that an already-sane model (like seeded catalog) is unchanged.

        Catalog models have hand-calibrated tiny multipliers (e.g. 8e-10).
        Calibration should be a no-op on these (idempotent).
        """
        # A hand-calibrated catalog-style component
        sane_components = [
            {
                "name": "Customer churn reduction",
                "calculationDisplay": "8e-10 x customerCount x customerChurnPct x avgResidentialRevenue",
                "multiplier": 8e-10,
                "assumptionKeys": ["customerCount"],  # simplified for test
                "lowCoeff": 0.6,
                "highCoeff": 1.4,
            }
        ]

        # This produces a sane value
        model_before = {"components": [dict(c) for c in sane_components]}
        rng_before = compute_value_range(model_before, self.assumptions)
        self.assertLess(rng_before["mid"], 100,
                       "Seeded catalog model should have sane mid value")

        # Calibration should NOT change it
        calibrated = calibrate_components(sane_components, self.assumptions, self.annual_revenue)
        model_after = {"components": calibrated}
        rng_after = compute_value_range(model_after, self.assumptions)

        # Should be byte-identical (or within float precision)
        self.assertAlmostEqual(rng_before["mid"], rng_after["mid"], places=2,
                              msg="Calibration should not change already-sane models")

    def test_tightened_ceilings(self):
        """Verify that the NEW ceilings (5% per-component, 25% global) are tighter.

        OLD ceilings were 10% per-component, 50% global.
        NEW ceilings are 5% per-component, 25% global.
        This test ensures a model that would pass OLD ceilings but not NEW
        ones is correctly clamped.
        """
        # Component that would be 400 $M (8% of 5000) — OK under old 10%, exceeds new 5%
        component = {
            "name": "Medium-sized component",
            "calculationDisplay": "8% of revenue",
            "multiplier": 0.08,
            "assumptionKeys": ["annualRevenueMM"],
            "lowCoeff": 1.0,
            "highCoeff": 1.0,
        }

        # This would produce 400 $M uncalibrated
        raw_value = component["multiplier"] * self.assumptions["annualRevenueMM"]
        self.assertEqual(raw_value, 400.0, "Setup check: component should target 400 $M")

        # Under NEW calibration, should be clamped to 250 $M (5%)
        calibrated = calibrate_components([component], self.assumptions, self.annual_revenue)
        calibrated_value = calibrated[0]["multiplier"] * self.assumptions["annualRevenueMM"]

        new_ceiling = 0.05 * self.annual_revenue  # 250 $M
        self.assertLessEqual(calibrated_value, new_ceiling,
                            f"Component should be clamped to {new_ceiling} $M under new ceiling")

    def test_empty_components(self):
        """Test that calibration handles edge cases gracefully."""
        # Empty components list
        result = calibrate_components([], self.assumptions, self.annual_revenue)
        self.assertEqual(result, [])

        # Component with zero multiplier
        zero_comp = [{
            "name": "Zero component",
            "multiplier": 0,
            "assumptionKeys": ["omBudgetMM"],
            "lowCoeff": 1.0,
            "highCoeff": 1.0,
        }]
        result = calibrate_components(zero_comp, self.assumptions, self.annual_revenue)
        self.assertEqual(result[0]["multiplier"], 0)

    def test_calibration_does_not_mutate_input(self):
        """Verify that calibrate_components returns a copy and doesn't mutate input."""
        original = [{
            "name": "Test",
            "multiplier": 0.5,
            "assumptionKeys": ["annualRevenueMM"],
            "lowCoeff": 1.0,
            "highCoeff": 1.0,
        }]
        original_multiplier = original[0]["multiplier"]

        calibrated = calibrate_components(original, self.assumptions, self.annual_revenue)

        # Original should be unchanged
        self.assertEqual(original[0]["multiplier"], original_multiplier,
                        "calibrate_components should not mutate input")
        # And the returned copy IS a distinct, calibrated object (0.5 * 5000 = 2500
        # $M exceeds the 250 $M ceiling, so its multiplier must have been rescaled).
        self.assertLess(calibrated[0]["multiplier"], original_multiplier,
                        "returned copy should carry the calibrated multiplier")


if __name__ == "__main__":
    unittest.main()
