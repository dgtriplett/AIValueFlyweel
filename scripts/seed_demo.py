"""Seed the FULL DEMO Lakebase state (populated, for walkthroughs).

Reference library + in-flight demo state (realized values, roadmap, funding,
linked Databricks assets, live/in-progress use cases). One transaction — a
failure rolls back cleanly, never a half-populated DB.

For the pristine shipped state use scripts/seed_clean.py.

Usage:
  python scripts/seed_demo.py [--profile <profile>] [--project <lakebase-project>] [--db app]
  (defaults: profile fe-vm-grid-ops-demo, project grid-atlas-db, db app)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from seed_lib import run  # noqa: E402

if __name__ == "__main__":
    run(clean=False)
