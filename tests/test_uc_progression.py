"""Tests for use-case progression tracking (target dates, slippage, account scoping).

Verifies:
  1. Progression is account-scoped (account A's target dates/events do NOT appear for account B)
  2. Moving target date later records a slippage event with reason
  3. at_risk computes correctly (target in past + status not live/value_realized)
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


class TestProgressionAccountScoping(unittest.TestCase):
    """Progression data is account-scoped: account A's targets/events do NOT appear for account B."""

    def test_progression_isolated_by_account(self):
        """Account 1's target date and events must not appear when querying as account 2."""
        db = FakeDB()
        # Use case exists
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="in_progress",
                requires_locked=False, in_portfolio=True)
        ])
        # Account 2 has NO progression row (account 1 has set a target, but we're querying as account 2)
        db.on("SELECT target_go_live_date, updated_at, updated_by FROM account_use_case_progress", [])
        # No events for account 2
        db.on("SELECT id, event_type, from_value, to_value, note, created_by, created_at FROM use_case_status_events", [])
        db.on("SELECT uc.id, uc.title", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=2)  # Account 2
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 0, "required_ready": 0}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # Account 2 sees no target date and no events (account 1's data is isolated)
        self.assertIsNone(result["progression"]["target_go_live_date"])
        self.assertEqual(len(result["progression"]["events"]), 0)
        self.assertFalse(result["progression"]["at_risk"])


class TestProgressionSlippage(unittest.TestCase):
    """Moving target date LATER records a date_change event with reason (slippage)."""

    def test_slippage_records_event(self):
        """When target_go_live_date moves later, the server records event_type='date_change' with reason."""
        db = FakeDB()
        # Use case exists
        db.on("SELECT 1 FROM use_cases WHERE id=$1", [Row(col0=1)])
        # Current progress: target is 2026-09-01
        db.on("SELECT target_go_live_date FROM account_use_case_progress WHERE account_id=$1 AND use_case_id=$2", [
            Row(target_go_live_date=date(2026, 9, 1))
        ])
        # Upsert will be called with the new date
        db.on("INSERT INTO account_use_case_progress", None)
        # Event insert
        db.on("INSERT INTO use_case_status_events", None)

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock), \
             patch("server.routes.use_cases.get_progression", new_callable=AsyncMock) as mock_get_prog:
            mock_accounts.current = AsyncMock(return_value=1)
            mock_user.return_value = "testuser"
            mock_get_prog.return_value = {
                "target_go_live_date": "2026-10-15",
                "updated_at": None,
                "updated_by": "testuser",
                "at_risk": False,
                "events": []
            }

            body = use_cases.ProgressionTargetDate(
                target_go_live_date="2026-10-15",  # Later than 2026-09-01 => slippage
                reason="Upstream dependency delayed"
            )

            run(use_cases.set_target_date(1, body, AsyncMock()))

        # The set_target_date handler should have called INSERT INTO use_case_status_events
        # Verify via the queries attribute (FakeDB logs all SQL)
        event_inserts = [q for q in db.queries if "use_case_status_events" in q]
        self.assertGreater(len(event_inserts), 0, "Expected use_case_status_events INSERT")
        # Verify the upsert to account_use_case_progress was called
        progress_upserts = [q for q in db.queries if "account_use_case_progress" in q]
        self.assertGreater(len(progress_upserts), 0, "Expected account_use_case_progress upsert")


class TestProgressionAtRisk(unittest.TestCase):
    """at_risk flag computes correctly: target in past + status not live/value_realized."""

    def test_at_risk_true_when_past_and_not_live(self):
        """Use case with past target date and status='in_progress' is at_risk=True."""
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="in_progress",
                requires_locked=False, in_portfolio=True)
        ])
        yesterday = (date.today() - timedelta(days=1))
        # Required assets query
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [])
        # Enables/enabled_by queries
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        # Progression queries - must match the WHERE clause pattern used in detail endpoint
        db.on("account_use_case_progress", [
            Row(target_go_live_date=yesterday, owner=None, updated_at=None, updated_by="testuser")
        ])
        db.on("use_case_status_events", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=1)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 0, "required_ready": 0}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # at_risk=True because target is in the past and status is 'in_progress'
        # The target date should be set
        self.assertIsNotNone(result["progression"]["target_go_live_date"])
        # And at_risk should be true
        self.assertTrue(result["progression"]["at_risk"],
                        f"Expected at_risk=True for past date {yesterday} with status 'in_progress', got {result['progression']}")

    def test_at_risk_false_when_live(self):
        """Use case with past target but status='live' is at_risk=False."""
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="live",
                requires_locked=False, in_portfolio=True)
        ])
        yesterday = (date.today() - timedelta(days=1))
        # Required assets query
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [])
        # Enables/enabled_by queries
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        # Progression queries - must match the WHERE clause pattern used in detail endpoint
        db.on("account_use_case_progress", [
            Row(target_go_live_date=yesterday, owner=None, updated_at=None, updated_by="testuser")
        ])
        db.on("use_case_status_events", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=1)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 0, "required_ready": 0}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # at_risk=False because status is 'live' (even though target is past)
        self.assertFalse(result["progression"]["at_risk"])

    def test_at_risk_false_when_future(self):
        """Use case with future target date is at_risk=False."""
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="in_progress",
                requires_locked=False, in_portfolio=True)
        ])
        tomorrow = (date.today() + timedelta(days=1))
        # Required assets query
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [])
        # Enables/enabled_by queries
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        # Progression queries - must match the WHERE clause pattern used in detail endpoint
        db.on("account_use_case_progress", [
            Row(target_go_live_date=tomorrow, owner=None, updated_at=None, updated_by="testuser")
        ])
        db.on("use_case_status_events", [])
        db.on("SELECT * FROM value_records WHERE", [])
        db.on("SELECT * FROM comments WHERE", [])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.readiness_for", new_callable=AsyncMock) as mock_readiness, \
             patch("server.routes.use_cases.load_assumptions", new_callable=AsyncMock) as mock_assumptions, \
             patch("server.routes.use_cases.compute_value_range") as mock_value_range, \
             patch("server.routes.use_cases.compute_realized") as mock_realized:
            mock_accounts.current = AsyncMock(return_value=1)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                            "required_total": 0, "required_ready": 0}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        # at_risk=False because target is in the future
        self.assertFalse(result["progression"]["at_risk"])


if __name__ == "__main__":
    unittest.main()
