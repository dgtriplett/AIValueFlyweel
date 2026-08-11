#!/usr/bin/env python3
"""Make the seeded reference utility internally consistent.

WHY
---
The seed's default assumptions do not describe one company. They describe a $5,000M-
revenue utility that somehow also owns a 30,000 MW generation fleet and spends $2,000M a
year on O&M:

    annualRevenueMM        5,000     the stated size
    omBudgetMM             2,000     40% of revenue — utilities run 25-30%
    generationFleetMW     30,000     a 30GW fleet belongs to a $15-20B company;
                                     Eversource, at $12.5B, owns almost no generation
    capitalBudgetMM        1,000     20% of revenue, plausible
    distributionCapexMM      600
    transmissionCapexMM      400     dist + trans = $1,000M, consistent with the above

That matters beyond tidiness, because these are the numbers a brand-new install computes
its portfolio from — before anyone has run research to calibrate them. The inflated O&M
figure alone inflates 45 use cases, and it is the single largest driver of the seeded
portfolio total (36.6% of it).

WHAT CHANGES
------------
O&M is set to 28% of revenue, and the generation fleet to a size a company this size
would actually own. Everything else is already coherent and is left alone.

These are DEFAULTS, not claims about a specific customer: the app's research agent
overwrites them per account (see load_assumptions, which prefers an account's own row).
The goal is a plausible starting point rather than an accurate one.

    python3 scripts/fix_seed_assumptions.py --dry-run
    python3 scripts/fix_seed_assumptions.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SEED = Path(__file__).resolve().parent / "seed_data.json"

# key -> (new value, why)
CORRECTIONS = {
    "omBudgetMM": (
        1400.0,
        "28% of $5,000M revenue. Was $2,000M (40%), which is above what any utility "
        "reports and inflated all 45 O&M-denominated use cases.",
    ),
    "generationFleetMW": (
        4000.0,
        "A $5,000M-revenue utility with some generation. Was 30,000 MW — a fleet that "
        "size implies a $15-20B company, so every $/MW use case was scaled to a "
        "different utility than the rest of the model.",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = json.loads(SEED.read_text())
    rows = data.get("value_assumptions") or []
    by_key = {row.get("key"): row for row in rows}

    missing = sorted(set(CORRECTIONS) - set(by_key))
    if missing:
        raise SystemExit(f"seed_data.json has no assumption(s) {missing}")

    revenue = float(by_key["annualRevenueMM"]["value"])
    for key, (value, why) in CORRECTIONS.items():
        row = by_key[key]
        old = float(row["value"])
        if abs(old - value) < 1e-9:
            print(f"  {key}: already {value:,.0f}")
            continue
        row["value"] = value
        # Recorded on the row itself, so anyone reading the assumption in the UI sees
        # why it is what it is rather than assuming it came from research.
        row["source"] = row.get("source") or "default"
        row["source_note"] = why
        print(f"  {key}: {old:,.0f} -> {value:,.0f}")
        print(f"      {why}")

    print(f"\nO&M is now {float(by_key['omBudgetMM']['value']) / revenue * 100:.0f}% "
          f"of ${revenue:,.0f}M revenue")

    if args.dry_run:
        print("dry run — nothing written")
        return 0
    SEED.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {SEED.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
