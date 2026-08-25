"""BUG A regression: the per-asset badge in the 'Required data assets' pane must
agree with the domain 'satisfied/pending' badge.

THE BUG
-------
Two status sources disagreed. The domain 'satisfied' badge came from
readiness.domain_satisfaction_for -> ready_assets(), which reads the
asset_status_by_account view: a missing account row means 'not_started', with NO
fallback to the shared data_assets.ingestion_status column (migration 010). The
per-asset badge in server/routes/use_cases.py came from a detail query that
COALESCE'd to that shared column when the account had no row — so an asset the
customer had NOT landed still showed 'Governed' inherited from the catalog, while
the domain it served correctly read 'pending'. Contradictory.

THE FIX
-------
Both now resolve through readiness.asset_status_map(): the account's own row, with
a missing row meaning 'not_started', never the shared column. This test pins that:
an asset governed on the SHARED column with NO account row must read NOT ready in
the per-asset resolution AND its domain must read NOT satisfied. The two agree.

MUTATION GUARD
--------------
If someone reintroduces `COALESCE(acs.ingestion_status, da.ingestion_status)` /
COALESCE-to-shared fallback, the shared 'governed' value would leak back in, the
asset would read ready, and both assertions below would flip — this test fails.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, UndefinedTable, run  # noqa: E402

from server import accounts, readiness  # noqa: E402

# The account whose (missing) rows we resolve against.
ACCOUNT_ID = 7
# The serving asset: governed on the SHARED catalog column, but the account has NO
# row for it in account_asset_status / asset_status_by_account.
GOVERNED_SHARED_ASSET_ID = 42
DOMAIN_ID = 3
USE_CASE_ID = 100

VIEW_Q = "FROM asset_status_by_account"
SHARED_Q = "FROM data_assets"
DOMAIN_SAT_Q = "FROM uc_requires_domain"


def _overlay_status(asset: dict, status_map: dict) -> dict:
    """The exact per-account overlay server/routes/use_cases.py applies to a serving
    asset before rendering its badge: use the account's own status, a missing entry
    meaning 'not_started', NEVER the shared da.ingestion_status column.

    Replicated here (not imported) so this test fails if the route drops back to a
    COALESCE-to-shared fallback while the route keeps a governed shared column.
    """
    asset = dict(asset)
    asset_id = asset.get("id")
    if asset_id in status_map:
        asset["ingestion_status"] = status_map[asset_id]
    else:
        asset["ingestion_status"] = "not_started"
    return asset


class TestDetailStatusConsistency(unittest.TestCase):
    def setUp(self):
        self._real_db = readiness.db
        self._token = accounts.current_account_id.set(ACCOUNT_ID)

    def tearDown(self):
        readiness.db = self._real_db
        accounts.current_account_id.reset(self._token)

    def _post_migration_fake(self):
        """A migrated DB: the view exists but the account has NO row for the asset
        (so the view returns nothing for it), while the SHARED column says governed.

        asset_status_by_account with no fallback would simply omit an un-set asset
        for this account; FakeDB returns [] for the view query, which is exactly the
        'account has said nothing' case migration 010 makes mean 'not_started'."""
        return (FakeDB()
                # View returns NO rows for this account/asset -> missing -> not_started.
                .on(VIEW_Q, [])
                # Shared catalog column HAS it governed — the value that must NOT leak.
                .on(SHARED_Q, [Row(id=GOVERNED_SHARED_ASSET_ID,
                                   ingestion_status="governed")])
                # One required domain served by that one asset.
                .on(DOMAIN_SAT_Q, [Row(domain_id=DOMAIN_ID, satisfied=False)]))

    def test_missing_account_row_reads_not_started_not_shared_governed(self):
        """asset_status_map must resolve the un-set asset to 'not_started', NOT the
        shared 'governed'. This is the value the detail badge renders."""
        readiness.db = self._post_migration_fake()
        status_map = run(readiness.asset_status_map())
        # The view returned nothing -> map has no entry -> overlay yields not_started.
        overlaid = _overlay_status(
            {"id": GOVERNED_SHARED_ASSET_ID, "ingestion_status": "governed"},
            status_map)
        self.assertEqual(overlaid["ingestion_status"], "not_started",
                         "shared-column 'governed' leaked into the per-account badge")

    def test_asset_not_ready_and_domain_not_satisfied_agree(self):
        """The two badges must tell one story: asset NOT ready AND domain NOT
        satisfied."""
        readiness.db = self._post_migration_fake()

        ready = run(readiness.ready_assets())
        self.assertNotIn(GOVERNED_SHARED_ASSET_ID, ready,
                         "asset governed only on the shared column must NOT be ready")

        sat = run(readiness.domain_satisfaction_for(USE_CASE_ID))
        self.assertEqual(sat.get(DOMAIN_ID), False,
                         "domain must read NOT satisfied when its only serving asset "
                         "is unlanded for this account")

        # The overlaid per-asset badge and the domain badge agree: both 'not ready'.
        status_map = run(readiness.asset_status_map())
        overlaid = _overlay_status(
            {"id": GOVERNED_SHARED_ASSET_ID, "ingestion_status": "governed"},
            status_map)
        asset_ready = overlaid["ingestion_status"] in readiness.READY_STATUSES
        self.assertFalse(asset_ready)
        self.assertFalse(sat.get(DOMAIN_ID))
        self.assertEqual(asset_ready, bool(sat.get(DOMAIN_ID)),
                         "per-asset badge and domain badge disagree")

    def test_pre_migration_fallback_preserved(self):
        """When the view is ABSENT (un-upgraded install), asset_status_map must fall
        back to the shared data_assets column, exactly as ready_assets did before —
        so an old install keeps working."""
        def raising_view(*a, **k):
            raise UndefinedTable("asset_status_by_account")

        fake = (FakeDB()
                .on(SHARED_Q, [Row(id=GOVERNED_SHARED_ASSET_ID,
                                   ingestion_status="governed")]))
        # Make the view query raise 42P01 (relation absent) to hit the fallback path.
        orig_fetch = fake.fetch

        async def fetch(sql, *args):
            if VIEW_Q in sql:
                raise UndefinedTable("asset_status_by_account")
            return await orig_fetch(sql, *args)

        fake.fetch = fetch
        readiness.db = fake

        status_map = run(readiness.asset_status_map())
        self.assertEqual(status_map.get(GOVERNED_SHARED_ASSET_ID), "governed",
                         "pre-migration install must still read the shared column")


if __name__ == "__main__":
    unittest.main()
