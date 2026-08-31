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
from server import accounts as _accounts  # noqa: E402


class TestUseCaseDetailStatusOverlay(unittest.TestCase):
    """PART A.1 / BUG A: get_use_case_detail must overlay per-account status resolved
    through readiness.asset_status_map (the asset_status_by_account view), NOT
    COALESCE the shared da.ingestion_status column into the per-asset badge."""

    def test_detail_reads_per_account_status(self):
        """The per-asset badge must reflect the account's own status from the view,
        even when the shared catalog column says something different."""
        db = FakeDB()
        # Use case 1 requires asset 10
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="not_started",
                requires_locked=False, in_portfolio=True)
        ])
        # The required-asset catalog row: shared column says not_started.
        db.on("FROM uc_requires_asset ura", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="not_started",  # shared column
                criticality="required", rationale=None)
        ])
        # asset_status_map reads the per-account view: this account has LANDED it.
        db.on("FROM asset_status_by_account", [
            Row(data_asset_id=10, ingestion_status="landed")
        ])
        # No domain requirements for this UC -> module path.
        db.on("FROM uc_requires_domain urd", [])
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.readiness.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.readiness.domain_satisfaction_for", new_callable=AsyncMock) as mock_domsat, \
             patch("server.routes.use_cases.domain_satisfaction_for", new_callable=AsyncMock) as mock_domsat2, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=123)
            mock_domsat.return_value = {}
            mock_domsat2.return_value = {}
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 1, "required_ready": 1}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            _tok = _accounts.current_account_id.set(123)
            try:
                result = run(use_cases.get_use_case_detail(1))
            finally:
                _accounts.current_account_id.reset(_tok)

        # The asset's ingestion_status must be 'landed' (per-account view), not
        # 'not_started' (shared column) — resolved through asset_status_map.
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
        # Two assets: one required, one helpful (shared-column rows).
        db.on("FROM uc_requires_asset ura", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", criticality="required", rationale="Needs work orders"),
            Row(id=20, source_system="GIS", module="Network Topology",
                ingestion_status="curated", criticality="helpful", rationale="Provides context"),
        ])
        # Per-account view: mirror the catalog statuses for this account.
        db.on("FROM asset_status_by_account", [
            Row(data_asset_id=10, ingestion_status="governed"),
            Row(data_asset_id=20, ingestion_status="curated"),
        ])
        db.on("FROM uc_requires_domain urd", [])
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.readiness.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.readiness.domain_satisfaction_for", new_callable=AsyncMock) as mock_domsat, \
             patch("server.routes.use_cases.domain_satisfaction_for", new_callable=AsyncMock) as mock_domsat2, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=123)
            mock_domsat.return_value = {}
            mock_domsat2.return_value = {}
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 1, "required_ready": 1}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            _tok = _accounts.current_account_id.set(123)
            try:
                result = run(use_cases.get_use_case_detail(1))
            finally:
                _accounts.current_account_id.reset(_tok)

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


class TestDataAssetDescriptiveFieldsRoundtrip(unittest.TestCase):
    """PART B.3: PUT /data-assets/{id} must persist the four new descriptive fields."""

    def test_put_roundtrips_descriptive_fields(self):
        """Updating an asset with provides/steward/source_of_record/refresh_cadence must persist all four."""
        db = FakeDB()

        # Mock the UPDATE query returning the updated row
        updated_row = Row(
            id=10,
            source_system="ERP",
            module="Plant Maintenance",
            source_category="ERP",
            description="Maintenance tracking",
            ingestion_status="landed",
            provides="Work orders and equipment records",
            steward="Corporate Services team",
            source_of_record="SAP ERP PM",
            refresh_cadence="Daily batch",
            sub_vertical="cross",
            uc_catalog="gridvalue",
            uc_schema="erp",
            owning_lob_id=1,
            origin="catalog",
            vendor=None,
            auto_captured=False,
            status_user_edited=False,
        )

        db.on("UPDATE data_assets SET", [updated_row])
        db.on("INSERT INTO account_asset_status", None)
        db.on("SELECT lob_id FROM data_asset_lobs", [])
        db.on("DELETE FROM data_asset_lobs", None)
        db.on("INSERT INTO data_asset_lobs", None)
        db.on("INSERT INTO audit_log", None)

        body = {
            "source_category": "ERP",
            "vendor": None,
            "module": "Plant Maintenance",
            "description": "Maintenance tracking",
            "sub_vertical": "cross",
            "ingestion_status": "landed",
            "uc_catalog": "gridvalue",
            "uc_schema": "erp",
            "owning_lob_id": 1,
            "benefiting_lob_ids": [],
            "provides": "Work orders and equipment records",
            "steward": "Corporate Services team",
            "source_of_record": "SAP ERP PM",
            "refresh_cadence": "Daily batch",
        }

        from server.routes.data_assets import DataAssetIn
        from fastapi import Request
        from unittest.mock import MagicMock

        with patch("server.routes.data_assets.db", db), \
             patch("server.routes.data_assets.accounts") as mock_accounts, \
             patch("server.routes.data_assets.current_user") as mock_user, \
             patch("server.routes.data_assets.write_audit", new_callable=AsyncMock):
            mock_accounts.current = AsyncMock(return_value=123)
            mock_user.return_value = "test_user"

            request = MagicMock(spec=Request)
            result = run(data_assets.update_data_asset(10, DataAssetIn(**body), request))

        # Verify all four descriptive fields are in the result
        self.assertEqual(result["provides"], "Work orders and equipment records")
        self.assertEqual(result["steward"], "Corporate Services team")
        self.assertEqual(result["source_of_record"], "SAP ERP PM")
        self.assertEqual(result["refresh_cadence"], "Daily batch")

        # Verify the UPDATE query was executed (it should be in db.queries)
        update_queries = [q for q in db.queries if "UPDATE data_assets SET" in q]
        self.assertTrue(len(update_queries) > 0, "UPDATE query should have been called")
        update_query = update_queries[0]
        # Check that the query includes the four new fields as parameters
        self.assertIn("provides=", update_query)
        self.assertIn("refresh_cadence=", update_query)
        self.assertIn("steward=", update_query)
        self.assertIn("source_of_record=", update_query)


if __name__ == "__main__":
    unittest.main()
