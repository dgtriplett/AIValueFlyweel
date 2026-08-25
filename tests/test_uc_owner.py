"""Tests for per-account use-case owner/assignee (migration 022).

Verifies:
  1. Setting the owner upserts into the account-scoped account_use_case_progress
     table bound to the CURRENT account (not some other tenant).
  2. The set-owner endpoint is portfolio-scoped: a use case that is neither catalog
     nor in the caller's portfolio 404s (fail closed), and NOTHING is written.
  3. get_use_case_detail surfaces the per-account owner on the progression overlay.
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


class TestSetOwnerUpsert(unittest.TestCase):
    """Setting an owner upserts into account_use_case_progress for the current account."""

    def test_set_owner_upserts_scoped_to_current_account(self):
        db = FakeDB()
        # Portfolio-scoped existence check passes (use case visible to this account).
        db.on("SELECT 1 FROM use_cases uc WHERE uc.id=$1", [Row(col0=1)])
        # No prior owner row.
        db.on("SELECT owner FROM account_use_case_progress WHERE account_id=$1 AND use_case_id=$2", [])
        db.on("INSERT INTO account_use_case_progress", None)

        captured = {}

        async def fake_visibility(alias="uc", *, param_index=1):
            return ("(uc.origin = 'catalog' OR EXISTS (...))", [7])

        real_execute = db.execute

        async def spy_execute(sql, *args):
            if "INSERT INTO account_use_case_progress" in sql:
                captured["upsert_args"] = args
            return await real_execute(sql, *args)

        db.execute = spy_execute  # type: ignore[assignment]

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio") as mock_portfolio, \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock) as mock_audit, \
             patch("server.routes.use_cases.get_progression", new_callable=AsyncMock) as mock_get_prog:
            mock_accounts.current = AsyncMock(return_value=7)
            mock_portfolio.use_case_visibility = fake_visibility
            mock_user.return_value = "alice@example.com"
            mock_get_prog.return_value = {"owner": "bob@example.com"}

            body = use_cases.ProgressionOwner(owner="bob@example.com")
            result = run(use_cases.set_owner(1, body, AsyncMock()))

        # Upserted into the account-scoped table.
        upserts = [q for q in db.queries if "INSERT INTO account_use_case_progress" in q]
        self.assertGreater(len(upserts), 0, "Expected an upsert into account_use_case_progress")
        # Bound to the CURRENT account (7), the use case (1) and the new owner.
        args = captured["upsert_args"]
        self.assertEqual(args[0], 7, "upsert must bind the current account id")
        self.assertEqual(args[1], 1, "upsert must bind the use case id")
        self.assertEqual(args[2], "bob@example.com", "upsert must set the new owner")
        # An audit row was written on change.
        mock_audit.assert_awaited()
        self.assertEqual(result["owner"], "bob@example.com")

    def test_set_owner_blank_clears_to_null(self):
        db = FakeDB()
        db.on("SELECT 1 FROM use_cases uc WHERE uc.id=$1", [Row(col0=1)])
        db.on("SELECT owner FROM account_use_case_progress WHERE account_id=$1 AND use_case_id=$2",
              [Row(owner="old@example.com")])
        db.on("INSERT INTO account_use_case_progress", None)

        captured = {}
        real_execute = db.execute

        async def spy_execute(sql, *args):
            if "INSERT INTO account_use_case_progress" in sql:
                captured["upsert_args"] = args
            return await real_execute(sql, *args)

        db.execute = spy_execute  # type: ignore[assignment]

        async def fake_visibility(alias="uc", *, param_index=1):
            return ("(uc.origin = 'catalog')", [7])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio") as mock_portfolio, \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock), \
             patch("server.routes.use_cases.get_progression", new_callable=AsyncMock) as mock_get_prog:
            mock_accounts.current = AsyncMock(return_value=7)
            mock_portfolio.use_case_visibility = fake_visibility
            mock_user.return_value = "alice@example.com"
            mock_get_prog.return_value = {"owner": None}

            body = use_cases.ProgressionOwner(owner="   ")  # whitespace clears
            run(use_cases.set_owner(1, body, AsyncMock()))

        self.assertIsNone(captured["upsert_args"][2], "blank owner must upsert NULL")


class TestSetOwnerPortfolioScoped(unittest.TestCase):
    """A use case outside the caller's portfolio (and not catalog) 404s — fail closed."""

    def test_out_of_portfolio_404s_and_writes_nothing(self):
        db = FakeDB()
        # Visibility-scoped existence check returns NO row -> not visible to this account.
        db.on("SELECT 1 FROM use_cases uc WHERE uc.id=$1", [])

        async def fake_visibility(alias="uc", *, param_index=1):
            return ("EXISTS (SELECT 1 FROM account_portfolio_use_cases ...)", [7])

        with patch("server.routes.use_cases.db", db), \
             patch("server.routes.use_cases.accounts") as mock_accounts, \
             patch("server.routes.use_cases.portfolio") as mock_portfolio, \
             patch("server.routes.use_cases.current_user") as mock_user, \
             patch("server.routes.use_cases.write_audit", new_callable=AsyncMock) as mock_audit:
            mock_accounts.current = AsyncMock(return_value=7)
            mock_portfolio.use_case_visibility = fake_visibility
            mock_user.return_value = "alice@example.com"

            body = use_cases.ProgressionOwner(owner="bob@example.com")
            with self.assertRaises(HTTPException) as ctx:
                run(use_cases.set_owner(999, body, AsyncMock()))

        self.assertEqual(ctx.exception.status_code, 404)
        # Nothing was upserted.
        upserts = [q for q in db.queries if "INSERT INTO account_use_case_progress" in q]
        self.assertEqual(len(upserts), 0, "must not write when the use case is not visible")
        mock_audit.assert_not_awaited()


class TestDetailReturnsOwner(unittest.TestCase):
    """get_use_case_detail surfaces the per-account owner on the progression overlay."""

    def test_detail_includes_per_account_owner(self):
        db = FakeDB()
        db.on("SELECT * FROM use_cases WHERE id = $1", [
            Row(id=1, title="Test UC", description="Desc", status="in_progress",
                requires_locked=False, in_portfolio=True, created_by="author@example.com")
        ])
        db.on("COALESCE(acs.ingestion_status, da.ingestion_status)", [])
        db.on("SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale", [])
        # Account-scoped progression row carrying the owner.
        db.on("SELECT target_go_live_date, owner, updated_at, updated_by", [
            Row(target_go_live_date=None, owner="owner@example.com",
                updated_at=None, updated_by="alice@example.com")
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
            mock_accounts.current = AsyncMock(return_value=7)
            mock_readiness.return_value = {"readiness": "shovel_ready", "ready_pct": 1.0,
                                           "required_total": 0, "required_ready": 0}
            mock_assumptions.return_value = []
            mock_value_range.return_value = {"low": 0, "mid": 0, "high": 0}
            mock_realized.return_value = {"mode": "none", "value": None, "note": None}

            result = run(use_cases.get_use_case_detail(1))

        self.assertEqual(result["progression"]["owner"], "owner@example.com")


if __name__ == "__main__":
    unittest.main()
