"""Tests for the HYPOTHESIZED value override (mirror of realized_override_*).

The use-case detail lets you edit hypothesized value two ways — CALCULATE
(per-component multipliers) and OVERRIDE (a straight dollar value + a required
note). The override path persists three columns added in migration 020:
hypothesized_override_enabled / _amount / _note.

Verifies (mutation-worthy):
  1. PUT /use-cases/{id} persists hypothesized_override_enabled+amount+note
     (the values reach the UPDATE statement, exactly as realized_override_* do).
  2. get_use_case_detail returns hypothesized_override_* in the payload so the
     editor can initialize its state.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

from server.routes import use_cases  # noqa: E402


class CapturingDB(FakeDB):
    """FakeDB that records the positional args of the last matching fetchrow.

    The base FakeDB routes by substring but discards call args; to assert that a
    mutation actually *persists* the override values we need to see what was
    handed to the UPDATE. `captured` collects (sql, args) for every fetchrow
    whose SQL contains the given marker.
    """

    def __init__(self, capture_marker: str, **kw):
        super().__init__(**kw)
        self._marker = capture_marker
        self.captured: list = []

    async def fetchrow(self, sql: str, *args):
        if self._marker in sql:
            self.captured.append((sql, args))
        return await super().fetchrow(sql, *args)


class TestUpdatePersistsHypothesizedOverride(unittest.TestCase):
    """PUT persists the three hypothesized_override_* columns like realized does."""

    def test_update_writes_override_fields(self):
        db = CapturingDB("UPDATE use_cases SET")
        db.on("SELECT status, title FROM use_cases WHERE id=$1",
              [Row(status="in_progress", title="Test UC")])
        db.on("UPDATE use_cases SET",
              [Row(id=1, title="Test UC", status="in_progress",
                   hypothesized_override_enabled=True,
                   hypothesized_override_amount=42.0,
                   hypothesized_override_note="finance signed off")])

        body = use_cases.UseCaseIn(
            title="Test UC",
            status="in_progress",
            hypothesized_override_enabled=True,
            hypothesized_override_amount=42.0,
            hypothesized_override_note="finance signed off",
        )

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.current_user", return_value="tester"), \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock):
            result = run(use_cases.update_use_case(1, body, request=object()))

        self.assertTrue(db.captured, "no UPDATE was executed")
        _sql, args = db.captured[-1]
        self.assertIn(True, args)
        self.assertIn(42.0, args)
        self.assertIn("finance signed off", args)
        self.assertTrue(result["hypothesized_override_enabled"])
        self.assertEqual(result["hypothesized_override_amount"], 42.0)
        self.assertEqual(result["hypothesized_override_note"], "finance signed off")

    def test_update_sql_mentions_the_new_columns(self):
        db = CapturingDB("UPDATE use_cases SET")
        db.on("SELECT status, title FROM use_cases WHERE id=$1",
              [Row(status="scoping", title="UC")])
        db.on("UPDATE use_cases SET", [Row(id=1, title="UC", status="scoping")])

        body = use_cases.UseCaseIn(title="UC", status="scoping")
        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.current_user", return_value="tester"), \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock):
            run(use_cases.update_use_case(1, body, request=object()))

        sql = db.captured[-1][0]
        self.assertIn("hypothesized_override_enabled", sql)
        self.assertIn("hypothesized_override_amount", sql)
        self.assertIn("hypothesized_override_note", sql)


class TestDetailSurfacesHypothesizedOverride(unittest.TestCase):
    """get_use_case_detail returns hypothesized_override_* so the editor can init."""

    def test_detail_includes_override_fields(self):
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="in_progress",
                requires_locked=False, in_portfolio=True,
                hypothesized_value_json=None,
                hypothesized_override_enabled=True,
                hypothesized_override_amount=17.5,
                hypothesized_override_note="per FP&A model")
        ])
        db.on("SELECT target_go_live_date, updated_at, updated_by FROM account_use_case_progress", [])
        db.on("SELECT id, event_type, from_value, to_value, note, created_by, created_at FROM use_case_status_events", [])
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

        self.assertTrue(result["hypothesized_override_enabled"])
        self.assertEqual(result["hypothesized_override_amount"], 17.5)
        self.assertEqual(result["hypothesized_override_note"], "per FP&A model")


if __name__ == "__main__":
    unittest.main()
