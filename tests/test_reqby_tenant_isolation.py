"""Security: Cross-tenant isolation for data-asset required_by.

This test verifies that GET /data-assets/{id} does NOT leak custom use-case titles and
rationale from other accounts. Custom use cases (origin='custom') are account-owned via
account_portfolio_use_cases; a tenant must only see:
  - Catalog use cases (origin='catalog', shared to all)
  - Their OWN custom use cases

Without the visibility predicate, Tenant A viewing a shared data asset would see Tenant B's
private custom use-case titles + rationale — the same cross-tenant leak class as the
onboarding-export bug (migration 014).
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from server.routes import data_assets  # noqa: E402


class TestRequiredByTenantIsolation(unittest.TestCase):
    """GET /data-assets/{id} must scope required_by to prevent cross-tenant use-case leaks."""

    def test_required_by_hides_other_accounts_custom_use_cases(self):
        """A custom use case owned by account B must NOT appear when current account is A."""
        db = FakeDB()
        # Asset 10 (shared, origin='catalog' implicitly)
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", status_user_edited=False)
        ])
        db.on("SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1", [])

        # required_by query: three use cases require asset 10
        # UC 1: catalog (shared) — should appear
        # UC 2: custom, owned by account A (100) — should appear
        # UC 3: custom, owned by account B (200) — must NOT appear (the leak)
        db.on("SELECT uc.id AS use_case_id", [
            Row(use_case_id=1, title="Catalog UC", criticality="required",
                rationale="Shared use case", origin="catalog"),
            Row(use_case_id=2, title="Account A Custom", criticality="required",
                rationale="Account A's private UC", origin="custom"),
            # UC 3 is filtered out by the visibility predicate, so it does NOT appear here
        ])

        # Mock portfolio.use_case_visibility to simulate account A (100) being current
        # It should return a condition that:
        # - allows origin='catalog' (UC 1)
        # - allows account_portfolio_use_cases for account 100 (UC 2)
        # - blocks account 200's use cases (UC 3)
        async def mock_visibility(alias="uc", *, param_index=1):
            # Simulate: (uc.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ...))
            # For testing, we mock the DB to only return UC 1 and UC 2, as if the WHERE clause worked
            return (
                f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
                f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
                [100],  # account A
            )

        with patch("server.routes.data_assets.db", db), \
             patch("server.routes.data_assets.accounts") as mock_accounts, \
             patch("server.routes.data_assets.portfolio.use_case_visibility", new=mock_visibility), \
             patch("server.readiness.readiness_map", new_callable=AsyncMock) as mock_readiness:
            mock_accounts.current = AsyncMock(return_value=100)  # account A
            mock_readiness.return_value = {
                1: {"readiness": "shovel_ready"},
                2: {"readiness": "nearly_ready"},
                # UC 3 is not in readiness map since it was filtered out
            }

            result = run(data_assets.get_data_asset(10))

        # Verify the visibility condition was applied to the query
        required_by_query = [q for q in db.queries if "uc.id AS use_case_id" in q][0]
        self.assertIn("uc.origin = 'catalog'", required_by_query,
                      "Query must filter by use-case visibility to prevent cross-tenant leaks")
        self.assertIn("account_portfolio_use_cases", required_by_query,
                      "Query must check account portfolio membership")

        # Must have required_by with exactly 2 entries (UC 1 and UC 2), not 3
        self.assertIn("required_by", result)
        self.assertEqual(len(result["required_by"]), 2, "Must exclude account B's custom UC")

        # UC 1: catalog
        self.assertEqual(result["required_by"][0]["use_case_id"], 1)
        self.assertEqual(result["required_by"][0]["title"], "Catalog UC")

        # UC 2: account A custom
        self.assertEqual(result["required_by"][1]["use_case_id"], 2)
        self.assertEqual(result["required_by"][1]["title"], "Account A Custom")

        # UC 3 must NOT be present (would be index 2 if leak existed)
        use_case_ids = {r["use_case_id"] for r in result["required_by"]}
        self.assertNotIn(3, use_case_ids, "Account B's custom UC must not leak to account A")

    def test_required_by_shows_all_catalog_use_cases(self):
        """Catalog use cases (origin='catalog') are shared and must appear for all accounts."""
        db = FakeDB()
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", status_user_edited=False)
        ])
        db.on("SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1", [])
        # Two catalog use cases
        db.on("SELECT uc.id AS use_case_id", [
            Row(use_case_id=1, title="Catalog UC One", criticality="required",
                rationale="Shared", origin="catalog"),
            Row(use_case_id=2, title="Catalog UC Two", criticality="helpful",
                rationale="Also shared", origin="catalog"),
        ])

        async def mock_visibility(alias="uc", *, param_index=1):
            return (
                f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
                f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
                [100],
            )

        with patch("server.routes.data_assets.db", db), \
             patch("server.routes.data_assets.accounts") as mock_accounts, \
             patch("server.routes.data_assets.portfolio.use_case_visibility", new=mock_visibility), \
             patch("server.readiness.readiness_map", new_callable=AsyncMock) as mock_readiness:
            mock_accounts.current = AsyncMock(return_value=100)
            mock_readiness.return_value = {
                1: {"readiness": "shovel_ready"},
                2: {"readiness": "nearly_ready"},
            }

            result = run(data_assets.get_data_asset(10))

        # Must have both catalog use cases
        self.assertEqual(len(result["required_by"]), 2)
        self.assertEqual(result["required_by"][0]["use_case_id"], 1)
        self.assertEqual(result["required_by"][1]["use_case_id"], 2)

    def test_required_by_preserves_no_account_fallback(self):
        """Pre-migration (no account_id): fall back to legacy in_portfolio column."""
        db = FakeDB()
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [
            Row(id=10, source_system="OMS", module="Work Management",
                ingestion_status="governed", status_user_edited=False)
        ])
        db.on("SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1", [])
        # One use case in legacy portfolio
        db.on("SELECT uc.id AS use_case_id", [
            Row(use_case_id=1, title="Legacy UC", criticality="required",
                rationale="Pre-migration", origin="catalog"),
        ])

        async def mock_visibility(alias="uc", *, param_index=1):
            # No account: fall back to in_portfolio
            return f"{alias}.in_portfolio = true", []

        with patch("server.routes.data_assets.db", db), \
             patch("server.routes.data_assets.accounts") as mock_accounts, \
             patch("server.routes.data_assets.portfolio.use_case_visibility", new=mock_visibility), \
             patch("server.readiness.readiness_map", new_callable=AsyncMock) as mock_readiness:
            mock_accounts.current = AsyncMock(return_value=None)  # no account
            mock_readiness.return_value = {1: {"readiness": "shovel_ready"}}

            result = run(data_assets.get_data_asset(10))

        # Must work without error and return the UC
        self.assertEqual(len(result["required_by"]), 1)
        self.assertEqual(result["required_by"][0]["use_case_id"], 1)


if __name__ == "__main__":
    unittest.main()
