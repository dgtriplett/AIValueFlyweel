"""Seed the PRISTINE day-1 state (the shipped default).

Deterministically produces the clean reference-library state in ONE transaction:
  - 6 domains (LOBs), the full data-source catalog (all 146 assets, not_started),
    all reference use cases loaded INTO the portfolio (in_portfolio=true) but
    PRISTINE (all not_started, status_source manual, realized cleared, hypothesized
    value models kept), reference dependency edges, computed phases, value
    assumptions, benchmark library.
  - NO roadmap_items / funding_requests / value_records / comments / linked assets.

A first-time user therefore opens the app to the FULL portfolio and FULL data-asset
catalog pre-loaded and ready to work — just with nothing yet ingested or in-flight.

Idempotent and safe to re-run. Never loads in-flight demo data and never leaves
a half-populated state on error (rolls back).

Usage:
  python scripts/seed_clean.py [--profile <profile>] [--project <lakebase-project>] [--db app]
  (defaults: profile fe-vm-grid-ops-demo, project grid-atlas-db, db app)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from seed_lib import run  # noqa: E402

if __name__ == "__main__":
    run(clean=True)
