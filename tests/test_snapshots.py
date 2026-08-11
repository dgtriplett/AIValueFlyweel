"""Portfolio snapshots: the trend, and the events that write to it.

WHAT MAKES A TREND WRONG RATHER THAN ABSENT
------------------------------------------
A snapshot table is easy to build and easy to build uselessly. Three ways it goes bad,
each tested here:

  1. Storing references instead of figures. If a snapshot were a view over current
     state, recalibrating an assumption would retroactively change every historical
     point and the trend would be a flat line by construction — showing no progress
     precisely when progress was made.
  2. Capturing on every write. Confirming twenty assumption changes writes twenty rows
     milliseconds apart, and the chart becomes a vertical line at one timestamp.
  3. Failing loudly. capture() runs at the tail of a confirm executor that has ALREADY
     applied a real change; raising there reports "your change failed" when it did not.

THE PROPERTY VERIFIED IN PRODUCTION
-----------------------------------
Landing Weather/NWP Forecasts on the live instance moved buildable value +$54.45M and
shovel-ready 8 -> 11 — exactly what the what-if simulator had predicted for that same
source. They agree because both read readiness through one code path. A trend that
disagreed with the simulator would make both untrustworthy.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import snapshots as snap  # noqa: E402


class TestDelta(unittest.TestCase):
    """The "since you started" line, which is the whole point of the feature."""

    def test_signed_change(self):
        older = {"buildable_value_mm": 100.0, "shovel_ready": 5}
        newer = {"buildable_value_mm": 161.23, "shovel_ready": 8}
        result = snap.delta(newer, older)
        self.assertAlmostEqual(result["buildable_value_mm"], 61.23, places=2)
        self.assertEqual(result["shovel_ready"], 3)

    def test_negative_change_is_reported(self):
        """Regression must be visible. A trend that only goes up is a sales chart."""
        result = snap.delta({"blocked": 171}, {"blocked": 179})
        self.assertEqual(result["blocked"], -8)

    def test_integers_stay_integers(self):
        """So the UI never renders "+3.0 use cases"."""
        result = snap.delta({"shovel_ready": 8}, {"shovel_ready": 5})
        self.assertIsInstance(result["shovel_ready"], int)

    def test_missing_field_is_none_not_zero(self):
        """A field absent from an older snapshot means "unknown", not "no change".

        Reporting 0 would claim a metric held steady when it simply was not recorded
        yet — the schema gained columns over time.
        """
        result = snap.delta({"domains_satisfied": 18}, {})
        self.assertIsNone(result["domains_satisfied"])

    def test_identical_snapshots_yield_zeros(self):
        same = {"buildable_value_mm": 50.0, "shovel_ready": 2}
        result = snap.delta(same, same)
        self.assertEqual(result["buildable_value_mm"], 0)
        self.assertEqual(result["shovel_ready"], 0)


class TestReasons(unittest.TestCase):
    def test_reasons_are_named_constants(self):
        """Callers must share spellings.

        Free text in the column means one caller writes 'source_landed' and another
        'source-landed', and the chart legend silently gains a duplicate series.
        """
        for reason in (snap.REASON_MANUAL, snap.REASON_ASSUMPTIONS,
                       snap.REASON_RESEARCH, snap.REASON_SOURCE,
                       snap.REASON_STATUS, snap.REASON_SEED):
            self.assertIsInstance(reason, str)
            self.assertRegex(reason, r"^[a-z_]+$",
                             "reasons should be lower_snake so they group cleanly")

    def test_reasons_are_distinct(self):
        reasons = {snap.REASON_MANUAL, snap.REASON_ASSUMPTIONS, snap.REASON_RESEARCH,
                   snap.REASON_SOURCE, snap.REASON_STATUS, snap.REASON_SEED}
        self.assertEqual(len(reasons), 6)


class TestCaptureQuietlyNeverRaises(unittest.TestCase):
    """The rule that keeps a charting failure from looking like a data-loss bug."""

    def test_swallows_any_failure(self):
        import asyncio

        async def run():
            original = snap.compute_metrics

            async def explode():
                raise RuntimeError("database on fire")

            snap.compute_metrics = explode
            try:
                # Must return normally. The caller has already applied a real change.
                await snap.capture_quietly(snap.REASON_SOURCE)
            finally:
                snap.compute_metrics = original

        asyncio.run(run())   # any exception escaping fails the test

    def test_capture_itself_does_raise(self):
        """The manual endpoint must be able to report a problem.

        Asserted behaviourally. An earlier version also searched the source for the
        word "raise", which was meaningless: capture() propagates by NOT catching, so
        there is nothing to find — the test failed on correct code.
        """
        import asyncio

        async def run():
            original = snap.compute_metrics

            async def explode():
                raise RuntimeError("boom")

            snap.compute_metrics = explode
            try:
                with self.assertRaises(RuntimeError):
                    await snap.capture()
            finally:
                snap.compute_metrics = original

        asyncio.run(run())


class TestSnapshotIsDenormalized(unittest.TestCase):
    """A snapshot must be a ledger entry, not a view."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.sql = (Path(__file__).parent.parent / "server" / "migrations"
                   / "011_snapshots.sql").read_text()

    def test_stores_figures_not_foreign_keys(self):
        """No FK to use_cases or data_assets.

        A snapshot referencing them would change meaning when those rows change, so
        recalibrating an assumption would rewrite history and the trend would show no
        progress exactly when progress happened.
        """
        for table in ("use_cases", "data_assets", "data_domains"):
            self.assertNotIn(f"REFERENCES {table}", self.sql,
                             f"a snapshot references {table} — history would be "
                             "rewritten whenever that table changes")

    def test_scoped_to_an_account(self):
        self.assertIn("account_id", self.sql)
        self.assertIn("REFERENCES accounts(id)", self.sql)

    def test_records_why_it_was_captured(self):
        """`reason` is what lets the chart explain itself instead of needing a
        separate changelog."""
        self.assertIn("reason", self.sql)

    def test_keeps_the_full_payload(self):
        """metrics_json exists because the past cannot be backfilled.

        A chart someone wants later can only plot a field that was captured at the
        time, so storing the whole computed payload is the one hedge available.
        """
        self.assertIn("metrics_json", self.sql)

    def test_deduplicates_per_minute(self):
        """Otherwise a batch of confirmations becomes a vertical line."""
        self.assertIn("value_snapshots_one_per_minute", self.sql)

    def test_uses_a_generated_column_for_the_minute(self):
        """A functional index on date_trunc(timestamptz) is rejected by Postgres:
        "functions in index expression must be marked IMMUTABLE", because it depends
        on the session TimeZone. Casting to UTC first makes it immutable."""
        self.assertIn("GENERATED ALWAYS AS", self.sql)
        self.assertIn("AT TIME ZONE 'UTC'", self.sql)

    def test_is_idempotent(self):
        import re
        self.assertIsNone(re.search(r"CREATE TABLE (?!IF NOT EXISTS)", self.sql))
        self.assertIsNone(re.search(r"CREATE (?:UNIQUE )?INDEX (?!IF NOT EXISTS)",
                                    self.sql))


class TestCaptureIsTriggeredByRealEvents(unittest.TestCase):
    """Snapshots must fire where the number actually moves.

    A manual-only button means the history exists solely if someone remembers, which
    means it usually will not.
    """

    def source_of(self, relative: str) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / "server" / relative).read_text()

    def test_research_apply_captures(self):
        """Applying calibrated assumptions re-quantifies every use case at once —
        the single largest move the portfolio value ever makes."""
        source = self.source_of("routes/research.py")
        self.assertIn("capture_quietly", source)
        self.assertIn("REASON_RESEARCH", source)

    def test_landing_a_source_captures(self):
        source = self.source_of("routes/data_assets.py")
        self.assertIn("capture_quietly", source)
        self.assertIn("REASON_SOURCE", source)

    def test_live_sync_captures_only_when_something_changed(self):
        """A dry run and a no-op sync are not events.

        Charting them would fill the trend with duplicate points implying activity
        where there was none.
        """
        source = self.source_of("routes/live.py")
        self.assertIn("capture_quietly", source)
        self.assertIn("if apply and (", source)

    def test_triggers_use_capture_quietly_not_capture(self):
        """An automatic trigger must never turn a successful change into an error."""
        for relative in ("routes/research.py", "routes/data_assets.py",
                         "routes/live.py"):
            source = self.source_of(relative)
            self.assertNotIn("await snap.capture(", source,
                             f"{relative} calls capture() directly; a charting "
                             "failure would report the change as failed")


class TestStatusWriteIsAccountScoped(unittest.TestCase):
    """The write-side twin of the read leak migration 010 fixed.

    FOUND WHILE TESTING THE TREND: landing a source returned 200 but moved nothing,
    because PATCH /data-assets/{id}/status wrote to the SHARED data_assets column. That
    is worse than the read leak — one utility marking their OMS governed would have
    done so for every tenant on the instance.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.source = (Path(__file__).parent.parent / "server" / "routes"
                      / "data_assets.py").read_text()

    def test_status_writes_to_the_account_table(self):
        self.assertIn("INSERT INTO account_asset_status", self.source,
                      "the status write must be per-account")

    def test_marks_the_row_as_user_edited(self):
        """So a later system-table sweep cannot silently revert a human's judgement —
        the same rule the rest of the app follows."""
        self.assertIn("is_user_edited", self.source)

    def test_reads_overlay_the_account_status(self):
        """The list and detail views must show THIS customer's position.

        Returning the catalog default would mean the UI displays the value it just
        overwrote.
        """
        self.assertIn("LEFT JOIN account_asset_status", self.source)
        self.assertIn("COALESCE(acs.ingestion_status", self.source)

    def test_pre_migration_installs_still_work(self):
        """An un-upgraded database has no account table; the write must still land."""
        self.assertIn("if account_id is None", self.source)


if __name__ == "__main__":
    unittest.main()
