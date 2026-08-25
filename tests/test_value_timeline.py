"""Value-realization timeline: GET /api/use-cases/portfolio/value-timeline.

The feature answers the Executive's "what value is coming, and WHEN" — a
by-quarter curve of projected annual value bucketed on each use case's target
go-live date, with a running cumulative, plus a separate 'unscheduled' bucket for
use cases with no target date for this account.

Its TWO load-bearing properties, and the ones this suite pins as mutation-worthy:

  1. BUCKETING — a use case's value lands in the QUARTER of its target date, the
     per-quarter value_landing is the SUM of that quarter's use cases, the series
     is ordered chronologically, and cumulative_value is the running total. Use
     cases with no target date are counted in `unscheduled`, NOT on the curve.

  2. PORTFOLIO SCOPING — the security-critical half. There is no
     `use_cases.account_id` column; ownership of a non-catalog use case is
     membership in `account_portfolio_use_cases`. The route scopes via
     `portfolio.use_case_visibility` (catalog OR in this account's portfolio) and
     binds the account into the account_use_case_progress join, exactly like the
     at-risk route. A use case in another account's portfolio must never appear,
     and with no resolvable account it fails closed to an empty series.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from server.routes import use_cases  # noqa: E402


def _hyp(mid: float) -> dict:
    """A hypothesized value formula the value engine reads without assumptions.

    `compute_value_range` short-circuits to {low, mid, high} when a formula has no
    `components` but carries the *_mm keys, so a test can set an exact value per
    use case without threading assumption keys through the fake.
    """
    return {"low_mm": mid, "mid_mm": mid, "high_mm": mid}


def _visibility_mock(captured=None):
    """A stand-in for portfolio.use_case_visibility that records it was consulted
    and emits the real catalog-OR-portfolio predicate bound to $param_index."""
    async def mock(alias="use_cases", *, param_index=1):
        if captured is not None:
            captured["called"] = True
            captured["param_index"] = param_index
        return (
            f"({alias}.origin = 'catalog' OR EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
            f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id))",
            [1],
        )
    return mock


class TestValueTimelineBucketing(unittest.TestCase):
    """The curve buckets by quarter, sums value, and runs a cumulative."""

    def test_buckets_by_quarter_sums_and_accumulates(self):
        db = FakeDB()
        # Three use cases in the caller's portfolio:
        #   - two land in 2026-Q1 (Jan + Mar) -> summed into one column
        #   - one lands in 2026-Q3 (Aug)
        #   - one has no target date -> unscheduled, off the curve
        db.on("FROM use_cases", [
            Row(id=1, status="in_progress", hypothesized_value_json=_hyp(3.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=date(2026, 1, 15)),
            Row(id=2, status="scoping", hypothesized_value_json=_hyp(2.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=date(2026, 3, 31)),
            Row(id=3, status="not_started", hypothesized_value_json=_hyp(5.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=date(2026, 8, 1)),
            Row(id=4, status="not_started", hypothesized_value_json=_hyp(7.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=None),
        ])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.load_assumptions", new=AsyncMock(return_value={})), \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=_visibility_mock()):
            mock_accounts.current = AsyncMock(return_value=1)
            result = run(use_cases.value_timeline())

        series = result["series"]
        # Two quarters on the curve, chronologically ordered.
        self.assertEqual([b["quarter"] for b in series], ["2026-Q1", "2026-Q3"])

        q1, q3 = series
        # Q1 sums the two use cases that land there.
        self.assertEqual(q1["use_case_count"], 2)
        self.assertEqual(q1["value_landing"], 5.0)
        self.assertEqual(q1["cumulative_value"], 5.0)
        # Q3 is a single use case; cumulative carries Q1 forward.
        self.assertEqual(q3["use_case_count"], 1)
        self.assertEqual(q3["value_landing"], 5.0)
        self.assertEqual(q3["cumulative_value"], 10.0)

        # The undated use case is reported separately, never on the curve.
        self.assertEqual(result["unscheduled"], {"use_case_count": 1, "value_landing": 7.0})

    def test_realized_value_wins_over_hypothesized(self):
        """A use case with a realized override contributes its REALIZED value."""
        db = FakeDB()
        db.on("FROM use_cases", [
            # Hypothesized says 3.0, but the realized override of 4.5 must win.
            Row(id=1, status="value_realized", hypothesized_value_json=_hyp(3.0),
                realized_value_json=None, realized_override_enabled=True,
                realized_override_amount=4.5, realized_override_note="booked",
                target_go_live_date=date(2027, 4, 10)),
        ])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.load_assumptions", new=AsyncMock(return_value={})), \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=_visibility_mock()):
            mock_accounts.current = AsyncMock(return_value=1)
            result = run(use_cases.value_timeline())

        self.assertEqual(result["series"][0]["quarter"], "2027-Q2")
        self.assertEqual(result["series"][0]["value_landing"], 4.5)


class TestValueTimelineScoping(unittest.TestCase):
    """The curve is scoped to the caller's portfolio and account."""

    def test_other_portfolios_use_case_does_not_appear(self):
        """A use case in account B's portfolio must NOT appear for account A.

        The FakeDB returns only account A's rows (what the account-scoped WHERE
        would yield). We additionally assert the emitted SQL carries the
        visibility predicate + the account-bound progression join, so removing the
        scope — the mutation this kills — fails the assertions.
        """
        db = FakeDB()
        db.on("FROM use_cases", [
            Row(id=1, status="in_progress", hypothesized_value_json=_hyp(2.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=date(2026, 2, 1)),
        ])

        captured = {}
        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.load_assumptions", new=AsyncMock(return_value={})), \
             patch("server.routes.use_cases.portfolio.use_case_visibility", new=_visibility_mock(captured)):
            mock_accounts.current = AsyncMock(return_value=1)  # account A
            result = run(use_cases.value_timeline())

        self.assertTrue(captured.get("called"), "must call portfolio.use_case_visibility")
        timeline_query = [q for q in db.queries if "account_use_case_progress" in q][0]
        self.assertIn("account_portfolio_use_cases", timeline_query,
                      "query must scope by portfolio membership")
        self.assertIn("p.account_id = $1", timeline_query,
                      "progression join must be bound to the current account")

        # Account B's UC 999 never comes back, so it is on no bucket.
        self.assertEqual(sum(b["use_case_count"] for b in result["series"]), 1)
        self.assertEqual(result["series"][0]["use_case_count"], 1)


class TestValueTimelineFailClosed(unittest.TestCase):
    """With no resolvable account the curve is empty rather than spanning tenants."""

    def test_no_account_returns_empty(self):
        db = FakeDB()
        # If the code queried anyway, this row would leak — it must never be read.
        db.on("FROM use_cases", [
            Row(id=1, status="in_progress", hypothesized_value_json=_hyp(9.0),
                realized_value_json=None, realized_override_enabled=False,
                realized_override_amount=None, realized_override_note=None,
                target_go_live_date=date(2026, 1, 1)),
        ])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.load_assumptions", new=AsyncMock(return_value={})):
            mock_accounts.current = AsyncMock(return_value=None)
            result = run(use_cases.value_timeline())

        self.assertEqual(
            result, {"series": [], "unscheduled": {"use_case_count": 0, "value_landing": 0.0}})
        # Fail closed: it must not have run any timeline query at all.
        self.assertEqual(
            [q for q in db.queries if "account_use_case_progress" in q], [],
            "no query may run when the account cannot be resolved")


if __name__ == "__main__":
    unittest.main()
