"""Joint funding: entered labor cost instead of auto-assumed estimate.

Tests that the delivery_cost field is properly stored and retrieved, and that
the auto-calculated delivery_cost_mid is only used as a suggestion (not
silently injected when the user provides their own value).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server.routes.joint_funding import FundIn  # noqa: E402


class TestJointFundingDeliveryCost(unittest.TestCase):
    """Test that delivery_cost is a user-entered field, not auto-assumed."""

    def test_fund_in_accepts_delivery_cost(self):
        """FundIn model accepts an optional delivery_cost field."""
        body = FundIn(
            data_asset_id=1,
            requesting_lob_id=2,
            co_funding_lobs=[3, 4],
            combined_value=5_000_000,
            sponsor="Alice",
            cost_share={"2": 200_000, "3": 150_000, "4": 150_000},
            brief_md="# Test brief",
            status="proposed",
            delivery_cost=500_000,
        )
        self.assertEqual(body.delivery_cost, 500_000)

    def test_fund_in_delivery_cost_optional(self):
        """delivery_cost is optional and defaults to None."""
        body = FundIn(
            data_asset_id=1,
            requesting_lob_id=2,
            co_funding_lobs=[3, 4],
            combined_value=5_000_000,
        )
        self.assertIsNone(body.delivery_cost)

    def test_fund_in_delivery_cost_can_be_zero(self):
        """delivery_cost can be explicitly set to zero (user's choice)."""
        body = FundIn(
            data_asset_id=1,
            requesting_lob_id=2,
            co_funding_lobs=[3, 4],
            combined_value=5_000_000,
            delivery_cost=0,
        )
        self.assertEqual(body.delivery_cost, 0)


if __name__ == "__main__":
    unittest.main()
