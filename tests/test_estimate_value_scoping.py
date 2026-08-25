"""Estimate-value: POST /api/use-cases/{id}/estimate-value must be portfolio-scoped.

BUG (user-reported, root-caused): the drawer's 'Estimate value' button (shown for a
generated use case with no value model) did nothing when clicked. The endpoint
`estimate_and_persist_value` selected `account_id` FROM use_cases and 403'd when it
did not match the caller -- but there is NO `use_cases.account_id` column. Ownership of
a non-catalog use case is membership in `account_portfolio_use_cases`, so that check
raised 403 for EVERY use case; the frontend catch only console.error'd, so the button
silently no-op'd.

FIX: scope exactly like every other use-case route -- `portfolio.use_case_visibility`
(catalog OR in-this-account's portfolio), account bound into the predicate -- and return
404 (not a bogus 403) when the use case is not visible. This suite pins that scoping
(the mutation-worthy part), mirroring tests/test_reqby_tenant_isolation.py's approach:

  (a) a use case VISIBLE to the caller's account (via the visibility predicate + account
      binding) succeeds: the value model is built and persisted, and the emitted SQL
      carries the predicate bound to the current account; and
  (b) a use case NOT in the caller's portfolio returns 404 (never the old broken 403,
      and never leaks/estimates another tenant's use case).
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from server.routes import use_cases  # noqa: E402


# A representative value model the shared build_value_model would return.
FAKE_VALUE_MODEL = {
    "driver": "Hypothesized",
    "components": [
        {"name": "O&M efficiency", "calculationDisplay": "O&M budget x 0.3%",
         "multiplier": 0.003, "assumptionKeys": ["omBudgetMM"]},
    ],
    "roiMonths": 12,
    "notes": "",
}


class TestEstimateValueScoping(unittest.TestCase):
    """POST /use-cases/{id}/estimate-value scopes by portfolio membership."""

    def test_estimate_succeeds_for_use_case_in_callers_portfolio(self):
        """A use case VISIBLE to the caller (visibility predicate + account binding)
        estimates and persists -- and the emitted SELECT carries the predicate + $2 binding.

        This is the mutation-worthy assertion: the FakeDB returns the row the scoped
        WHERE clause would yield, and we additionally assert the query text carries the
        `account_portfolio_use_cases` predicate bound to the current account ($2). Drop
        the scope (regress to the old `use_cases.account_id` compare) and these fail.
        """
        db = FakeDB()
        # The scoped SELECT yields the caller's in-portfolio use case.
        db.on("FROM use_cases uc", [
            Row(id=7, title="Predictive maintenance", description="Cut downtime"),
        ])
        # The persist UPDATE just records the SQL.
        db.on("UPDATE use_cases", None)

        captured = {}

        async def mock_visibility(alias="uc", *, param_index=1):
            captured["param_index"] = param_index
            return (
                f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
                f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
                [1],  # account A bound as $2
            )

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=mock_visibility), \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock) as mock_audit, \
             patch("server.routes.agents.build_value_model", new_callable=AsyncMock) as mock_build:
            mock_accounts.current = AsyncMock(return_value=1)  # account A
            mock_user.return_value = "analyst@example.com"
            mock_build.return_value = FAKE_VALUE_MODEL

            result = run(use_cases.estimate_and_persist_value(7, AsyncMock()))

        # The scoping predicate was bound as $2 (uc_id is $1) and reached the SELECT.
        self.assertEqual(captured.get("param_index"), 2,
                         "visibility predicate must bind the account as $2 (uc_id is $1)")
        select_q = [q for q in db.queries if "FROM use_cases uc" in q][0]
        self.assertIn("account_portfolio_use_cases", select_q,
                      "SELECT must scope by portfolio membership, not use_cases.account_id")
        self.assertIn("uc.id = $1", select_q,
                      "SELECT must bind the use case id as $1")
        # There is NO use_cases.account_id column -- the fix must not resurrect it.
        self.assertNotIn("account_id = $", select_q.replace("ap.account_id", ""),
                         "must not compare a nonexistent use_cases.account_id column")

        # The value model was built from the scoped row and persisted.
        mock_build.assert_awaited_once_with("Predictive maintenance", "Cut downtime")
        update_q = [q for q in db.queries if "UPDATE use_cases" in q]
        self.assertTrue(update_q, "must persist hypothesized_value_json")
        self.assertIn("hypothesized_value_json", update_q[0])

        # Audit log write is kept.
        mock_audit.assert_awaited_once()

        # The endpoint returns the persisted value model.
        self.assertEqual(result, FAKE_VALUE_MODEL)

    def test_use_case_not_in_portfolio_returns_404(self):
        """A use case NOT visible to the caller returns 404 -- never the old broken 403,
        and the value model builder is never invoked (no cross-tenant estimate)."""
        db = FakeDB()
        # The scoped SELECT returns nothing: the visibility predicate excluded it
        # (it belongs to another account's portfolio and is not catalog).
        db.on("FROM use_cases uc", [])

        async def mock_visibility(alias="uc", *, param_index=1):
            return (
                f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
                f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
                [1],
            )

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=mock_visibility), \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock) as mock_audit, \
             patch("server.routes.agents.build_value_model", new_callable=AsyncMock) as mock_build:
            mock_accounts.current = AsyncMock(return_value=1)  # account A
            mock_user.return_value = "analyst@example.com"
            mock_build.return_value = FAKE_VALUE_MODEL

            with self.assertRaises(HTTPException) as ctx:
                run(use_cases.estimate_and_persist_value(999, AsyncMock()))

        self.assertEqual(ctx.exception.status_code, 404,
                         "an out-of-portfolio use case must 404, not the old broken 403")
        # No cross-tenant estimate/persist happened.
        mock_build.assert_not_awaited()
        self.assertFalse([q for q in db.queries if "UPDATE use_cases" in q],
                         "must not persist a value model for an invisible use case")
        mock_audit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
