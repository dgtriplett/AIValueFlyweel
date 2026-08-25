#!/usr/bin/env python3
"""Backfill calibration to EXISTING persisted value models.

THE PROBLEM
-----------
Value model calibration was added to build_value_model() in server/routes/agents.py,
which fixes NEW value models (generated via agents or the 'Estimate value' button).
But use cases generated BEFORE calibration was added have uncalibrated models already
persisted in use_cases.hypothesized_value_json (and realized_value_json when present).

Example: use case #241 'Meter Tamper & Theft Detection' shows $2332.89B/yr because
its stored components have absurd multipliers (one component shows
'0.00015 x $5.00B x 2.00M = $1500.00B'). The drawer displays these persisted values
unchanged because compute_value_range() is a pure evaluator — it never mutates models.

THE FIX
-------
This script applies the SAME calibrate_components() logic (imported from
server.value_engine) to every use_cases row with a component-based
hypothesized_value_json (and realized_value_json if present). It:
  1. Loads assumption values
  2. For each use case with components:
     - Extracts the components
     - Calls calibrate_components() (same helper build_value_model uses)
     - Writes the calibrated model back to the DB
  3. Is IDEMPOTENT: re-running on an already-calibrated model is a no-op because
     values are already under the ceiling.

USAGE
-----
    python3 scripts/recalibrate_value_models.py --dry-run \\
        --profile <cli-profile> --project <lakebase-project> --db app

    python3 scripts/recalibrate_value_models.py \\
        --profile <cli-profile> --project <lakebase-project> --db app

SAFETY
------
- Catalog use cases (hand-calibrated tiny multipliers, requires_locked) are left
  UNCHANGED — the idempotent no-op guarantees this.
- Uses the same DB connection pattern as scripts/migrate.py and
  scripts/recalibrate_capex_value.py
- Does NOT touch app.yaml, db.py, accounts.py, or migrations.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def _as_dict(formula):
    """Coerce a formula (dict or jsonb string) to a dict via the SHARED helper."""
    from server.value_engine import _as_dict as _shared

    return _shared(formula)


def _mid(formula: dict, assumptions: dict[str, float]) -> float:
    """Mid value ($M) via the SHARED value_engine evaluator (no divergence)."""
    from server.value_engine import compute_value_range

    rng = compute_value_range(formula, assumptions)
    return rng["mid"] if rng else 0.0


def recalibrate(cursor, assumptions: dict[str, float], dry_run: bool = False) -> dict:
    """Recalibrate all use cases with component-based value models.

    Returns: {"updated": int, "unchanged": int, "changes": [(id, title, before, after)]}
    """
    # Import the SHARED calibration helper so this script and build_value_model()
    # use the EXACT same logic (no divergence). value_engine imports cleanly
    # because its db/asyncpg import is lazy (inside the async DB helpers only).
    from server.value_engine import calibrate_components

    annual_revenue = assumptions.get("annualRevenueMM", 5000.0)

    # Fetch all use cases with a hypothesized_value_json
    cursor.execute("""
        SELECT id, title, hypothesized_value_json, realized_value_json
        FROM use_cases
        WHERE hypothesized_value_json IS NOT NULL
        ORDER BY id
    """)
    rows = cursor.fetchall()

    updated = 0
    unchanged = 0
    changes = []

    for uc_id, title, hyp_json, real_json in rows:
        hyp = _as_dict(hyp_json)
        if not hyp or not hyp.get("components"):
            unchanged += 1
            continue

        # Compute before value
        before_mid = _mid(hyp, assumptions)

        # Calibrate hypothesized_value_json
        calibrated_components = calibrate_components(
            hyp["components"], assumptions, annual_revenue
        )
        calibrated_hyp = dict(hyp)
        calibrated_hyp["components"] = calibrated_components

        # Compute after value
        after_mid = _mid(calibrated_hyp, assumptions)

        # Only update if something changed (idempotent — skip already-calibrated)
        changed = abs(before_mid - after_mid) > 0.01  # tolerance for float precision

        if changed:
            changes.append((uc_id, title, before_mid, after_mid))
            updated += 1

            if not dry_run:
                # Update hypothesized_value_json
                cursor.execute(
                    "UPDATE use_cases SET hypothesized_value_json = %s WHERE id = %s",
                    (json.dumps(calibrated_hyp), uc_id)
                )

                # If realized_value_json also has components, calibrate it too
                real = _as_dict(real_json)
                if real and real.get("components"):
                    calibrated_real_components = calibrate_components(
                        real["components"], assumptions, annual_revenue
                    )
                    calibrated_real = dict(real)
                    calibrated_real["components"] = calibrated_real_components
                    cursor.execute(
                        "UPDATE use_cases SET realized_value_json = %s WHERE id = %s",
                        (json.dumps(calibrated_real), uc_id)
                    )
        else:
            unchanged += 1

    if not dry_run:
        cursor.connection.commit()

    return {"updated": updated, "unchanged": unchanged, "changes": changes}


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_lib

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report every change without writing")
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--project", required=True)
    parser.add_argument("--branch", default="production")
    parser.add_argument("--endpoint", default="primary")
    parser.add_argument("--db", default="app")
    args = parser.parse_args()

    conn = seed_lib.get_conn(args.profile, args.project, args.branch,
                             args.endpoint, args.db)
    try:
        cur = conn.cursor()

        # Load assumptions (same logic as value_engine.load_assumptions but synchronous)
        # Use the first account's assumptions if any, else defaults
        cur.execute("""
            SELECT DISTINCT ON (key) key, value
            FROM value_assumptions
            WHERE account_id IS NULL OR account_id = (
                SELECT id FROM accounts ORDER BY id LIMIT 1
            )
            ORDER BY key, (account_id IS NULL)
        """)
        assumptions = {key: float(value) for key, value in cur.fetchall()}

        print(f"\nRecalibrating value models (dry_run={args.dry_run})...")
        print(f"  Loaded {len(assumptions)} assumptions")
        print(f"  Annual revenue: ${assumptions.get('annualRevenueMM', 5000):.0f}M")
        print(f"  Per-component ceiling: ${0.05 * assumptions.get('annualRevenueMM', 5000):.0f}M (5%)")
        print(f"  Global ceiling: ${0.25 * assumptions.get('annualRevenueMM', 5000):.0f}M (25%)\n")

        result = recalibrate(cur, assumptions, dry_run=args.dry_run)

        print("Results:")
        print(f"  {result['updated']} use case(s) recalibrated")
        print(f"  {result['unchanged']} use case(s) unchanged (already calibrated or no components)\n")

        if result['changes']:
            print("Changed use cases (showing top 10):")
            for uc_id, title, before, after in result['changes'][:10]:
                print(f"  id={uc_id:4d} {title[:50]:52s} ${before:10.2f}M -> ${after:10.2f}M")
            if len(result['changes']) > 10:
                print(f"  ... and {len(result['changes']) - 10} more")

        if args.dry_run:
            print("\nDRY RUN — nothing written to database")
        else:
            print("\n✓ Database updated")

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
