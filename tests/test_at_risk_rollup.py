"""At-risk rollup: GET /api/use-cases/at-risk must be portfolio-scoped.

The feature answers "which use cases are slipping / overdue, why, and how often"
for the Portfolio Manager and Executive personas. Its ONE security-critical
property: it must return only use cases visible to the CALLER's account. A use
case in another account's portfolio must never appear.

There is no `use_cases.account_id` column; ownership of a non-catalog use case is
membership in `account_portfolio_use_cases`. So this route scopes exactly like the
other use-case routes, via `portfolio.use_case_visibility` — catalog OR in this
account's portfolio — and the account is bound into the progression joins too. This
suite pins that scoping (the mutation-worthy part), the fail-closed no-account
branch, and the days-overdue / times-slipped row shaping.
"""
import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from server.routes import use_cases  # noqa: E402


class TestAtRiskScoping(unittest.TestCase):
    """The rollup returns only at-risk use cases in the caller's portfolio."""

    def test_other_portfolios_use_case_does_not_appear(self):
        """A use case in account B's portfolio must NOT appear for account A.

        The FakeDB stands in for Postgres: it returns the rows the account-scoped
        WHERE clause would yield (account A's overdue UC only). The out-of-portfolio
        UC 999 is deliberately NOT in that result — and we additionally assert the
        emitted SQL actually carries the visibility predicate + the account binding,
        so removing the scope (the mutation this test kills) fails the assertions.
        """
        db = FakeDB()
        yesterday = date.today() - timedelta(days=3)
        # Only account A's in-portfolio, overdue use case comes back from the DB.
        # UC 999 (account B's) is absent because the WHERE clause excluded it.
        db.on("FROM use_cases", [
            Row(id=1, title="Account A overdue UC", status="in_progress",
                target_go_live_date=yesterday, times_slipped=2,
                latest_slippage_reason="Vendor integration delayed"),
        ])

        captured = {}

        async def mock_visibility(alias="use_cases", *, param_index=1):
            captured["called"] = True
            return (
                f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
                f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
                [1],
            )

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=mock_visibility):
            mock_accounts.current = AsyncMock(return_value=1)  # account A
            result = run(use_cases.at_risk_use_cases())

        # The scoping helper was consulted and its predicate reached the query.
        self.assertTrue(captured.get("called"), "must call portfolio.use_case_visibility")
        at_risk_query = [q for q in db.queries if "account_use_case_progress" in q][0]
        self.assertIn("account_portfolio_use_cases", at_risk_query,
                      "query must scope by portfolio membership")
        self.assertIn("p.account_id = $1", at_risk_query,
                      "progression join must be bound to the current account")

        ids = {item["id"] for item in result["items"]}
        self.assertIn(1, ids, "account A's own at-risk UC must appear")
        self.assertNotIn(999, ids, "account B's use case must NOT leak into the rollup")
        self.assertEqual(result["total"], 1)

    def test_row_shaping_days_overdue_and_slippage(self):
        """days_overdue is derived from the past target; slippage count/reason pass through."""
        db = FakeDB()
        target = date.today() - timedelta(days=10)
        db.on("FROM use_cases", [
            Row(id=1, title="Slipping UC", status="scoping",
                target_go_live_date=target, times_slipped=3,
                latest_slippage_reason="Data quality remediation"),
        ])

        async def mock_visibility(alias="use_cases", *, param_index=1):
            return (f"{alias}.in_portfolio = true", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=mock_visibility):
            mock_accounts.current = AsyncMock(return_value=1)
            result = run(use_cases.at_risk_use_cases())

        item = result["items"][0]
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["days_overdue"], 10)
        self.assertEqual(item["times_slipped"], 3)
        self.assertEqual(item["latest_slippage_reason"], "Data quality remediation")
        self.assertEqual(item["target_go_live_date"], target.isoformat())


class TestAtRiskFailClosed(unittest.TestCase):
    """With no resolvable account the rollup returns empty rather than spanning tenants."""

    def test_no_account_returns_empty(self):
        db = FakeDB()
        # If the code queried anyway, this row would leak — it must never be read.
        db.on("FROM use_cases", [
            Row(id=1, title="Leaky UC", status="in_progress",
                target_go_live_date=date.today() - timedelta(days=1),
                times_slipped=1, latest_slippage_reason="should not appear"),
        ])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts:
            mock_accounts.current = AsyncMock(return_value=None)
            result = run(use_cases.at_risk_use_cases())

        self.assertEqual(result, {"items": [], "total": 0})
        # Fail closed: it must not have run any at-risk query at all.
        self.assertEqual(
            [q for q in db.queries if "account_use_case_progress" in q], [],
            "no query may run when the account cannot be resolved")


if __name__ == "__main__":
    unittest.main()
