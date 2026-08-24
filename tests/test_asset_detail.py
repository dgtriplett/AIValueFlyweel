"""Tests for Part A (per-account status + required/helpful split) and Part B (asset detail enrichment).

PART A verifies that get_use_case_detail returns per-account status (not the shared
catalog status) and correctly partitions required vs helpful assets.

PART B verifies that GET /data-assets/{id} enriches with required_by.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from server.routes import use_cases, data_assets  # noqa: E402


class TestUseCaseDetailStatusOverlay(unittest.TestCase):
    """PART A.1: get_use_case_detail must overlay per-account status, not read the shared column."""

    def test_detail_reads_per_account_status(self):
        """The query must LEFT JOIN account_asset_status and COALESCE over the shared column."""
        db = FakeDB()
        # Use case 1 requires asset 10
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="not_started",
                requires_locked=False, in_portfolio=True)
        ])
        # The shared column says not_started, but this account has landed it
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="landed",  # per-account overlay wins
                criticality="required", rationale=None)
        ])
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=123)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 1, "required_ready": 1}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # The asset's ingestion_status must be 'landed' (per-account), not 'not_started' (shared)
        self.assertEqual(len(result["required_assets"]), 1)
        self.assertEqual(result["required_assets"][0]["ingestion_status"], "landed")


class TestUseCaseDetailHelpfulSplit(unittest.TestCase):
    """PART A.2: get_use_case_detail must split required vs helpful into separate lists."""

    def test_detail_splits_required_and_helpful(self):
        """required_assets contains only criticality='required', helpful_assets contains 'helpful'."""
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="not_started",
                requires_locked=False, in_portfolio=True)
        ])
        # Two assets: one required, one helpful
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", criticality="required", rationale="Needs work orders"),
            Row(id=20, source_system="GIS", module="Network Topology",
                ingestion_status="curated", criticality="helpful", rationale="Provides context"),
        ])
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=123)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 1, "required_ready": 1}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # Must have exactly 1 required and 1 helpful
        self.assertEqual(len(result["required_assets"]), 1)
        self.assertEqual(len(result["helpful_assets"]), 1)
        self.assertEqual(result["required_assets"][0]["criticality"], "required")
        self.assertEqual(result["helpful_assets"][0]["criticality"], "helpful")


class TestDataAssetDetailRequiredBy(unittest.TestCase):
    """PART B.2(i): GET /data-assets/{id} must enrich with required_by reverse join."""

    def test_asset_enriched_with_required_by(self):
        """The asset detail must include a required_by list of {use_case_id, title, criticality, rationale, readiness}."""
        db = FakeDB()
        # Asset 10
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", status_user_edited=False)
        ])
        # Benefiting LOBs (empty for this test)
        db.on("SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1", [])
        # required_by join: two use cases require this asset
        db.on("SELECT uc.id AS use_case_id", [
            Row(use_case_id=1, title="UC One", criticality="required", rationale="Needs work orders"),
            Row(use_case_id=2, title="UC Two", criticality="helpful", rationale="Useful for context"),
        ])

        with patch("server.routes.data_assets.db", db), \
             patch("server.routes.data_assets.accounts") as mock_accounts, \
             patch("server.readiness.readiness_map", new_callable=AsyncMock) as mock_readiness:
            mock_accounts.current = AsyncMock(return_value=123)
            # Mock readiness_map returning readiness for UC 1 and 2
            mock_readiness.return_value = {
                1: {"readiness": "shovel_ready"},
                2: {"readiness": "nearly_ready"},
            }

            result = run(data_assets.get_data_asset(10))

        # Must have required_by with 2 entries
        self.assertIn("required_by", result)
        self.assertEqual(len(result["required_by"]), 2)
        # First entry: UC 1
        self.assertEqual(result["required_by"][0]["use_case_id"], 1)
        self.assertEqual(result["required_by"][0]["title"], "UC One")
        self.assertEqual(result["required_by"][0]["criticality"], "required")
        self.assertEqual(result["required_by"][0]["rationale"], "Needs work orders")
        self.assertEqual(result["required_by"][0]["readiness"], "shovel_ready")
        # Second entry: UC 2
        self.assertEqual(result["required_by"][1]["use_case_id"], 2)
        self.assertEqual(result["required_by"][1]["title"], "UC Two")
        self.assertEqual(result["required_by"][1]["criticality"], "helpful")
        self.assertEqual(result["required_by"][1]["readiness"], "nearly_ready")


if __name__ == "__main__":
    unittest.main()
