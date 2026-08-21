#!/usr/bin/env python3
"""Rewrite the value multipliers to individually defensible percentages.

THE PROBLEM
-----------
The portfolio totalled $3,498M/yr against Eversource's $12,500M revenue — 28% of
revenue from analytics, which no CFO will accept and no benchmark supports.

The cause was not one bad number. Every individual multiplier looked reasonable in
isolation: "2.5% of the distribution capital plan" is a fine claim for one use case.
The error is that TWENTY-EIGHT use cases each claimed a slice of that same base, and
nobody ever summed them:

    distributionCapexMM   28 use cases   claiming  67.2% of $2,200M  = $1,426M
    transmissionCapexMM   22 use cases   claiming  55.0% of $1,200M  = $  640M

Read plainly, the portfolio asserted that analytics would eliminate two-thirds of the
distribution capital plan and half of transmission. Those two families alone were 59%
of the portfolio total.

WHY NOT A GLOBAL HAIRCUT
------------------------
Scaling everything by one factor (or adding a "realization %" dial) hides the actual
mistake and leaves the per-use-case numbers indefensible — the DER use case would still
be claiming 6% of the capital plan, just multiplied by something. And a uniform 0.089x
scale, which is what capping the total mechanically requires, produces figures nobody
would defend either: DER & EV Charging Management at 0.22% of capex is as wrong as 6%.

So each use case is assigned its own percentage, by what it actually does.

THE TIERS
---------
Anchored on what the aggregate can credibly be: utility analytics portfolios defer on
the order of 3-8% of a distribution capital plan IN TOTAL, and a comparable share of
transmission. Within that budget:

  ~1.0-1.5%   The genuine capital-deferral use cases. Hosting-capacity analysis, NWA
              planning and DER/EV management directly avoid or defer specific reinforcement
              projects; dynamic line rating defers transmission build by releasing
              existing thermal headroom. These are the ones with published deferral cases.
  ~0.3-0.5%   Optimization with real but indirect capital impact — volt-VAR/CVR, feeder
              load forecasting, network digital twin, asset-condition analytics. They
              change what gets built and when, but the deferral is a second-order effect.
  ~0.1-0.25%  Reliability, inspection and work-management analytics. Mostly avoided O&M
              and improved targeting; the capital effect is small.
  ~0.05-0.1%  Reporting, dashboards and data quality. Real value, almost none of it
              capital. Keeping them denominated in capex at 1.5% was the least
              defensible part of the old model.

WHAT ELSE THIS FIXES
--------------------
Correcting capex alone left the total at $1,648M, and moved the implausibility rather
than removing it: the top of the list became revenue-denominated features, where
"Proactive Outage Communication & ETA" claimed 0.3% of TOTAL REVENUE — $37.5M/yr for
outage notifications. So two more families and three individual rates are corrected:

  * annualRevenueMM (23 use cases, was 4.1% of revenue in aggregate). Revenue-side value
    is real for theft/revenue protection and rate design, and near zero for notification
    and dashboard features — those pay off in satisfaction and cost-to-serve, which are
    counted elsewhere. Now 0.92%.
  * capitalBudgetMM (4 use cases claiming 5.3% of the entire $4,200M capital plan between
    them, for planning and reporting tools). Now 1.0%.
  * Three improvement rates that overstated what is achievable: 50% SAIDI elimination
    (utilities report 20-30% on the automated portion), 35% call deflection (deployed
    systems reach 15-20%), and 15% bad-debt reduction for a regulated utility.

Net effect: $3,498M -> $1,024M, from 28% of revenue to 8.2%. Median use case $4.0M,
largest $34.2M.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
  * omBudgetMM (45 use cases, 17.6% of a $2,000M O&M budget). Utilities publicly target
    10-20% O&M efficiency from digital programs, so an aggregate 17.6% spread across 45
    use cases is within the range people actually claim — and no single one of them
    claims more than 0.6%. Aggressive, not indefensible.
  * The low/high coefficient bands. They are wide (0.4x-1.5x), which is honest about the
    uncertainty. Note the mid-coefficient averages ~0.95, so `mid` sits near the full
    claim rather than below it — that is why fixing the multipliers, not the bands, is
    what moves the headline.

Re-runnable and idempotent: matches on use-case id AND the assumption key, writes only
the components it has an explicit mapping for, and reports any it does not recognize
rather than guessing.

    python3 scripts/recalibrate_capex_value.py --dry-run
    python3 scripts/recalibrate_capex_value.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Percentage of the DISTRIBUTION capital plan each use case may claim.
# Comment on each line is the reason, so a customer challenging a number gets an answer.
DISTRIBUTION = {
    41:  0.010,   # DER & EV Charging Management — the flagship deferral case: managed
                  # charging and DER dispatch avoid specific feeder reinforcements
    124: 0.008,   # Non-Wires Alternatives Planning — exists to defer capital by design
    45:  0.005,   # Hosting Capacity Analysis — releases headroom, avoids upgrades
    138: 0.004,   # Self-Optimizing Volt-VAR & CVR — defers voltage-driven reinforcement
    137: 0.004,   # Autonomous DERMS — closed-loop DER orchestration
    14:  0.0035,  # Load Forecasting at Feeder Level — better siting/sizing of upgrades
    71:  0.003,   # Distribution Network Digital Twin — planning accuracy
    113: 0.003,   # Distribution Transformer Loading Insights — defers replacements
    126: 0.0025,  # Wildfire Risk & PSPS Optimization — targets hardening spend
    139: 0.0025,  # Autonomous Outage Triage & Multi-Agent Dispatch
    44:  0.0025,  # Voltage Optimization & Loss Reduction
    105: 0.002,   # Autonomous Gas Leak Triage & Dispatch
    130: 0.002,   # Permanent Fault Prediction from Momentary Patterns
    217: 0.002,   # VVO/CVR Energy & Loss Optimization
    42:  0.0015,  # Grid Model Clean-up & Connectivity Inference — enables the rest
    220: 0.0015,  # Vegetation Risk & Trim Optimization
    102: 0.0015,  # AI-Assisted Leak Localization & Quantification
    96:  0.0015,  # Gas Pressure & Flow Anomaly Monitoring
    91:  0.0015,  # Gas Leak Detection & Prediction
    73:  0.0015,  # Automated Work Order Creation & Crew Dispatching
    75:  0.001,   # Hyper-local Probabilistic Risk Pricing
    103: 0.001,   # Hydrogen Blending & Injection Planning
    95:  0.001,   # Cathodic Protection Analytics
    72:  0.001,   # Automated Work Package Generation for Crews
    127: 0.001,   # Customer-to-Transformer Connectivity Data Quality
    114: 0.001,   # Voltage Compliance & Power Quality Reporting — reporting
    237: 0.001,   # Distribution State Estimation & Observability
    112: 0.0008,  # SAIDI / SAIFI / CAIDI Reliability Dashboard — reporting
}

# Percentage of the TRANSMISSION capital plan.
TRANSMISSION = {
    7:   0.010,   # Dynamic Line Rating & Capacity Optimization — releases existing
                  # thermal headroom instead of building; the clearest deferral case
    8:   0.005,   # Predictive Maintenance for Lines & Substations — life extension
    10:  0.004,   # Transmission Asset Condition Analytics — defers replacement
    70:  0.0035,  # Autonomous Transmission Asset Management
    214: 0.003,   # Dynamic Line Rating Analytics
    38:  0.003,   # Transmission Planning Scenario Modeling — avoids overbuild
    39:  0.0025,  # Vegetation Management Optimization
    67:  0.0025,  # Wide-area Grid Digital Twin
    36:  0.0025,  # Grid Stability & Contingency Analytics
    68:  0.002,   # Autonomous Grid Stress Testing
    69:  0.002,   # Real-time Co-optimization with Neighboring Grids
    6:   0.002,   # Load & Congestion Forecasting
    123: 0.002,   # Drone-Based AI Asset Inspection
    66:  0.002,   # AI-accelerated Engineering Design for Transmission
    106: 0.002,   # Gas Pipeline System Digital Twin
    100: 0.002,   # Predictive Pipeline Failure Analytics
    37:  0.0015,  # Automated Fault Location & Switching Guidance
    101: 0.0015,  # Compressor Station Optimization
    94:  0.0015,  # Pipeline Integrity Management (DIMP/TIMP)
    40:  0.001,   # Transmission Corridor Risk Scoring
    110: 0.0008,  # Line Loading & Congestion Report — reporting
    111: 0.0008,  # Substation KPI Reporting — reporting
}

# Percentage of TOTAL ANNUAL REVENUE. This family was the other implausible one: at
# $12,500M revenue, 0.3% is $37.5M/yr, which was being claimed for outage notifications
# and a "life-event agent". Revenue-denominated value is real for theft/revenue
# protection and rate design, and close to zero for notification and dashboard features —
# those show up as satisfaction and cost-to-serve, not revenue.
REVENUE = {
    46:  0.0015,  # Dynamic Pricing & Personalized Rate Design — genuinely revenue-side
    221: 0.0012,  # Revenue Protection / Energy-Theft Detection — recovers real revenue
    19:  0.0010,  # Billing Accuracy, Anomaly & Theft Detection
    98:  0.0008,  # Gas Meter & Theft Anomaly Detection
    125: 0.0008,  # Virtual Power Plant / DER Aggregation — new market participation
    76:  0.0005,  # Behind-the-Meter Optimization
    79:  0.0004,  # Personalized Grid Services Marketplace — new revenue, unproven
    194: 0.0004,  # Wholesale Counterparty Credit Risk Scoring — avoided loss
    116: 0.0003,  # Customer Segmentation & Profile Analytics — program targeting
    47:  0.0002,  # Load Disaggregation & Appliance Insights
    192: 0.0002,  # Wholesale Off-taker & PPA Performance Dashboard
    50:  0.0002,  # Customer Journey Orchestration
    80:  0.0002,  # Autonomous Customer Experience Orchestration
    193: 0.0001,  # REC Tracking & Settlement
    212: 0.0001,  # Settlement Shadow-Billing & Dispute Analytics
    30:  0.0008,  # IT/OT Security Anomaly Detection — breach-risk avoidance; left near
                  # its old 0.08% because avoided-breach cost genuinely scales with revenue
    78:  0.0001,  # Holistic Life-event & Transition Agent — satisfaction, not revenue
    49:  0.0001,  # Proactive Outage Communication & ETA — was 0.3% ($37.5M) for
                  # notifications; the value here is CSAT and call deflection, and the
                  # call-deflection case is already counted under callsPerYear
    48:  0.0001,  # Customer Carbon Footprint & Savings Coach
    115: 0.00005,  # Customer Experience & NPS Dashboard — reporting
    117: 0.00005,  # Energy Efficiency Program Enrollment Tracker — reporting
    145: 0.00005,  # Low-Income & CARE Program Enrollment Dashboard — reporting
    146: 0.00005,  # Move-In / Move-Out & Customer Lifecycle Dashboard — reporting
}

# Percentage of the TOTAL CAPITAL BUDGET ($4,200M). Four use cases claiming 5.3% of the
# entire capital plan between them, for planning and reporting tools. Planning analytics
# do improve capital allocation, but the credible claim is a fraction of a percent of
# better-directed spend, not 2%.
CAPITAL_BUDGET = {
    21:  0.004,   # Enterprise Load and Resource Planning Analytics — drives what gets
                  # built; the strongest of the four, was 2.0% ($79.8M)
    27:  0.003,   # Capital Portfolio Analytics — prioritization across the plan
    23:  0.002,   # Regulatory Policy Scenario Simulation
    232: 0.001,   # Capital Project Cost & Schedule Risk
}

# Multi-key components, keyed by (use_case_id, the tuple of assumption keys). These are
# not percentages of a base — the multiplier IS the improvement rate — so each one is a
# claim about physical or operational performance and has to be judged on its own.
# Only the ones that overclaim are listed; the rest were already credible.
IMPROVEMENT_RATES = {
    # Utilities with fully deployed FLISR report 20-30% SAIDI reduction on the
    # automated portion of the system, not half of enterprise SAIDI. 50% was the
    # single largest remaining overclaim in the portfolio ($66.0M).
    (74, ("saidiMinuteValueMM", "currentSAIDI")): 0.20,

    # 35% call deflection is a best-case vendor figure. Deployed utility self-service
    # deflects on the order of 15-20%, and some of that overlaps the AHT reduction
    # already counted by "Knowledge Agents for Call Center".
    (77, ("callsPerYear", "costPerCall")): 1.8e-07,

    # 15% bad-debt reduction is optimistic for a scoring model on a regulated utility
    # with limited disconnection latitude; 8-10% is the defensible range.
    (20, ("annualRevenueMM", "badDebtPct")): 0.0009,

    # Two near-duplicate storm use cases each took a share of the SAME storm budget —
    # 12% and 8%, so 20% of all storm restoration cost between them, for what is
    # substantially one capability (predict the storm, pre-stage the crews). Crew
    # pre-staging does measurably cut restoration cost, but 20% is the whole program's
    # ceiling, not each half's. Split to 7% and 4%.
    (43,  ("avgStormCostMM", "stormsPerYear")): 0.07,
    (218, ("avgStormCostMM", "stormsPerYear")): 0.04,
}

# Families where the individual claims are already the right size and only the COUNT is
# the problem, so a proportional scale is the correct fix rather than 59 hand-assigned
# numbers.
#
# annualFuelSpendMM: 59 use cases (mostly the nuclear and generation catalog) each claim
# a 1-2% fuel-efficiency improvement. Every one of those is a defensible figure on its
# own — 1% fuel burn is exactly what plant-optimization vendors publish — but together
# they claim 65% of the fuel bill. Unlike the capex families, there is no meaningful
# ranking to impose here: these use cases genuinely are near-equivalent small efficiency
# gains on the same budget, and pretending otherwise by inventing a hierarchy would be
# less honest than scaling them. Target 12% in aggregate, which is a strong-but-arguable
# claim for a full generation-analytics program.
PROPORTIONAL = {
    "annualFuelSpendMM": 0.12,
}

# Single-key components whose multiplier is a $/unit RATE rather than a share of a
# budget. There is no denominator to cap these against, so they are corrected by id like
# the percentage families, using the same (id, keys) signature as IMPROVEMENT_RATES.
PER_UNIT = {
    # "Zero-unplanned-outage Generation Fleet" claimed $4M per GW from ELIMINATING forced
    # outages — $120M/yr on a 30GW fleet, and 10x the two sibling use cases that do the
    # same job (predictive maintenance and outage prediction, both ~$0.4M/GW). The name
    # is the tell: nothing takes a generation fleet to zero unplanned outages. Set to
    # $0.6M/GW, i.e. modestly better than its siblings rather than an order of magnitude.
    (65, ("generationFleetMW",)): 0.0006,
}
PER_UNIT_DISPLAYS = {
    (65, ("generationFleetMW",)):
        "Fleet GW × ~$0.6M/GW from fewer forced outages",
}


TARGETS = {
    "distributionCapexMM": DISTRIBUTION,
    "transmissionCapexMM": TRANSMISSION,
    "annualRevenueMM": REVENUE,
    "capitalBudgetMM": CAPITAL_BUDGET,
}

_BASE_LABELS = {
    "distributionCapexMM": "Dist capex",
    "transmissionCapexMM": "Trans capex",
    "annualRevenueMM": "Annual revenue",
    "capitalBudgetMM": "Capital budget",
    "annualFuelSpendMM": "Annual fuel spend",
}


# The corrected prose for each improvement-rate change, written out rather than derived.
# The display strings are hand-authored sentences ("$/SAIDI-min × current SAIDI × 50%
# elimination") and the percentage in them does not always correspond to the multiplier
# in an inferable way — the call-deflection multiplier is a per-call factor of 3.5e-07,
# whose "35%" is only recoverable if you know the unit conversion. Guessing with a regex
# risks leaving an authoritative-looking sentence that contradicts the formula, so each
# is stated.
RATE_DISPLAYS = {
    (74, ("saidiMinuteValueMM", "currentSAIDI")):
        "$/SAIDI-min × current SAIDI × 20% reduction",
    (77, ("callsPerYear", "costPerCall")):
        "Calls × $/call × 18% deflection",
    (20, ("annualRevenueMM", "badDebtPct")):
        "Annual rev × bad-debt% × 9% reduction",
    (43, ("avgStormCostMM", "stormsPerYear")):
        "Storm cost × storms/yr × 7% reduction",
    (218, ("avgStormCostMM", "stormsPerYear")):
        "Storm cost × storms/yr × 4% reduction",
}
# IMPROVEMENT_RATES and PER_UNIT are kept separate above because they mean different
# things — one is an improvement percentage, one is a dollars-per-unit rate — but they are
# applied identically, so they are merged into one lookup rather than duplicating the
# handling (and the risk of updating only one of them).
FIXED_RATES = {**IMPROVEMENT_RATES, **PER_UNIT}
FIXED_RATE_DISPLAYS = {**RATE_DISPLAYS, **PER_UNIT_DISPLAYS}


def _percent_display(key: str, multiplier: float) -> str:
    # 3 decimals, because the revenue percentages are small enough that 2 would render
    # several distinct values as the same "0.01%".
    return f"{_BASE_LABELS[key]} × {multiplier * 100:.3f}%".replace(".000%", "%")


def _coerce(formula):
    """A formula as a dict, or None. Lakebase returns jsonb as a dict, but a seed file
    edited by hand may hold a string."""
    if isinstance(formula, str):
        try:
            formula = json.loads(formula)
        except ValueError:
            return None
    return formula if isinstance(formula, dict) else None


def proportional_factors(formulas) -> dict[str, float]:
    """Scale factor per PROPORTIONAL family: target / what the family currently claims.

    Computed across the whole catalog, because that is the only level at which the
    problem is visible — no single use case can tell that it is the 59th claim on the
    same fuel budget. `formulas` is an iterable of formula dicts.
    """
    claimed: dict[str, float] = {}
    for formula in formulas:
        for component in formula.get("components") or []:
            keys = component.get("assumptionKeys") or []
            if len(keys) == 1 and keys[0] in PROPORTIONAL:
                claimed[keys[0]] = claimed.get(keys[0], 0.0) + float(
                    component.get("multiplier") or 0)
    factors = {}
    for key, target in PROPORTIONAL.items():
        current = claimed.get(key, 0.0)
        # Only ever scales DOWN. If a family is already inside its target the numbers are
        # left exactly as they are — inflating them to hit a ceiling would be absurd.
        factors[key] = min(1.0, target / current) if current > 0 else 1.0
    return factors


def rewrite_formula(formula: dict, use_case_id: int,
                    factors: dict[str, float] | None = None) -> tuple[dict, list[str]]:
    """Return (formula, notes). Only touches components this module has a rule for."""
    notes: list[str] = []
    factors = factors or {}
    for component in formula.get("components") or []:
        keys = component.get("assumptionKeys") or []
        old = float(component.get("multiplier") or 0)

        # Multi-key improvement rates: judged individually, and the display string is
        # left alone because it describes the calculation in words the author chose.
        signature = (use_case_id, tuple(keys))
        rate = FIXED_RATES.get(signature)
        if rate is not None:
            if abs(old - rate) > 1e-12:
                component["multiplier"] = rate
                display = FIXED_RATE_DISPLAYS.get(signature)
                if display:
                    component["calculationDisplay"] = display
                notes.append(f"{'+'.join(keys)}: {old} -> {rate}")
            continue

        # Proportionally scaled families.
        if len(keys) == 1 and keys[0] in PROPORTIONAL:
            factor = factors.get(keys[0], 1.0)
            new = round(old * factor, 6)
            if abs(old - new) > 1e-12:
                component["multiplier"] = new
                display = component.get("calculationDisplay") or ""
                # The prose names the old percentage; regenerate it rather than leave a
                # sentence that contradicts the arithmetic.
                component["calculationDisplay"] = _percent_display(keys[0], new)
                notes.append(f"{keys[0]}: {old * 100:.3f}% -> {new * 100:.3f}% "
                             f"(x{factor:.4f} family scale)")
            continue

        if len(keys) != 1 or keys[0] not in TARGETS:
            continue
        key = keys[0]
        new = TARGETS[key].get(use_case_id)
        if new is None:
            # Reported, never guessed. A capex-denominated component with no assigned
            # percentage means the catalog gained a use case since this script was
            # written, and it needs a human to decide what it may claim.
            notes.append(f"NO MAPPING for id={use_case_id} on {key} — left unchanged")
            continue
        if abs(old - new) < 1e-12:
            continue
        component["multiplier"] = new
        component["calculationDisplay"] = _percent_display(key, new)
        notes.append(f"{key}: {old * 100:.3f}% -> {new * 100:.3f}%")

    # Keep the static low/mid/high in step. They are only read when a formula has no
    # components, but leaving them at the old figures would mean the row disagrees with
    # itself — and a future reader cannot tell which number is authoritative.
    if formula.get("components") and "mid_mm" in formula:
        formula["_static_recalibrated"] = True
    return formula, notes


# Title for every id referenced above, so the same mapping can be applied to
# scripts/seed_data.json (which has no ids — rows are keyed by title, and migration 008
# added the uniqueness constraint that makes matching on title safe). Generated from the
# live catalog rather than hand-typed; a title that no longer matches is reported as
# unmapped instead of silently skipped.
_ID_TITLES = {
    6: "Load & Congestion Forecasting",
    43: "Storm Impact Prediction & Crew Pre-Staging",
    65: "Zero-unplanned-outage Generation Fleet",
    218: "Storm Outage Prediction & Crew Pre-Staging",
    7: "Dynamic Line Rating & Capacity Optimization",
    8: "Predictive Maintenance for Lines & Substations",
    10: "Transmission Asset Condition Analytics",
    14: "Load Forecasting at Feeder Level",
    19: "Billing Accuracy, Anomaly & Theft Detection",
    20: "Payment Risk Prediction",
    21: "Enterprise Load and Resource Planning Analytics",
    23: "Regulatory Policy Scenario Simulation",
    27: "Capital Portfolio Analytics",
    30: "IT/OT Security Anomaly Detection",
    36: "Grid Stability & Contingency Analytics",
    37: "Automated Fault Location & Switching Guidance",
    38: "Transmission Planning Scenario Modeling",
    39: "Vegetation Management Optimization",
    40: "Transmission Corridor Risk Scoring",
    41: "DER & EV Charging Management",
    42: "Grid Model Clean-up & Connectivity Inference",
    44: "Voltage Optimization & Loss Reduction",
    45: "Hosting Capacity Analysis",
    46: "Dynamic Pricing & Personalized Rate Design",
    47: "Load Disaggregation & Appliance Insights",
    48: "Customer Carbon Footprint & Savings Coach",
    49: "Proactive Outage Communication & ETA",
    50: "Customer Journey Orchestration",
    66: "AI-accelerated Engineering Design for Transmission",
    67: "Wide-area Grid Digital Twin",
    68: "Autonomous Grid Stress Testing",
    69: "Real-time Co-optimization with Neighboring Grids",
    70: "Autonomous Transmission Asset Management",
    71: "Distribution Network Digital Twin",
    72: "Automated Work Package Generation for Crews",
    73: "Automated Work Order Creation & Crew Dispatching",
    74: "Self-healing Distribution Networks",
    75: "Hyper-local Probabilistic Risk Pricing",
    76: "Behind-the-Meter Optimization",
    77: "AI Customer Self-Service",
    78: "Holistic Life-event & Transition Agent",
    79: "Personalized Grid Services Marketplace",
    80: "Autonomous Customer Experience Orchestration",
    91: "Gas Leak Detection & Prediction",
    94: "Pipeline Integrity Management (DIMP/TIMP)",
    95: "Cathodic Protection Analytics",
    96: "Gas Pressure & Flow Anomaly Monitoring",
    98: "Gas Meter & Theft Anomaly Detection",
    100: "Predictive Pipeline Failure Analytics",
    101: "Compressor Station Optimization",
    102: "AI-Assisted Leak Localization & Quantification",
    103: "Hydrogen Blending & Injection Planning",
    105: "Autonomous Gas Leak Triage & Dispatch",
    106: "Gas Pipeline System Digital Twin",
    110: "Line Loading & Congestion Report",
    111: "Substation KPI Reporting",
    112: "SAIDI / SAIFI / CAIDI Reliability Dashboard",
    113: "Distribution Transformer Loading Insights",
    114: "Voltage Compliance & Power Quality Reporting",
    115: "Customer Experience & NPS Dashboard",
    116: "Customer Segmentation & Profile Analytics",
    117: "Energy Efficiency Program Enrollment Tracker",
    123: "Drone-Based AI Asset Inspection (Lines, Towers, Insulators)",
    124: "Non-Wires Alternatives (NWA) Planning",
    125: "Virtual Power Plant (VPP) / DER Aggregation",
    126: "Wildfire Risk & PSPS Optimization",
    127: "Customer-to-Transformer Connectivity Data Quality",
    130: "Permanent Fault Prediction from Momentary Fault Patterns",
    137: "Autonomous DERMS — Closed-Loop DER Orchestration",
    138: "Self-Optimizing Volt-VAR & CVR",
    139: "Autonomous Outage Triage & Multi-Agent Dispatch",
    145: "Low-Income & CARE Program Enrollment Dashboard",
    146: "Move-In / Move-Out & Customer Lifecycle Dashboard",
    192: "Wholesale Off-taker & PPA Performance Dashboard",
    193: "Renewable Energy Certificate (REC) Tracking & Settlement",
    194: "Wholesale Counterparty Credit Risk Scoring",
    212: "Settlement Shadow-Billing & Dispute Analytics",
    214: "Dynamic Line Rating Analytics",
    217: "VVO/CVR Energy & Loss Optimization",
    220: "Vegetation Risk & Trim Optimization",
    221: "Revenue Protection / Energy-Theft Detection",
    232: "Capital Project Cost & Schedule Risk",
    237: "Distribution State Estimation & Observability",
}


def recalibrate_seed(seed_path: Path, *, write: bool) -> tuple[float, float, int]:
    """Apply the same rewrite to scripts/seed_data.json.

    The database and the seed have to be corrected together. Fixing only the live
    instance leaves the shipped catalog claiming 67% of a capital plan, so the next
    `deploy.py --skip-seed=false` — or any new customer install — reintroduces the whole
    problem silently.

    The seed has no use-case ids (rows are keyed by `parent_id` / `title` and ids are
    assigned at insert time), so the mapping is bridged by TITLE. Titles are unique in
    the catalog — migration 008 added the constraint that guarantees it — which is what
    makes this safe.
    """
    data = json.loads(seed_path.read_text())
    assumptions = {row["key"]: float(row.get("value") or 0)
                   for row in data.get("value_assumptions") or []
                   if row.get("key")}
    title_to_id = _title_to_id()
    factors = proportional_factors(
        f for f in (_coerce(row.get("hypothesized_value_json"))
                    for row in data.get("use_cases") or []) if f)

    before = after = 0.0
    changed = 0
    for row in data.get("use_cases") or []:
        formula = row.get("hypothesized_value_json")
        if isinstance(formula, str):
            formula = json.loads(formula)
        if not isinstance(formula, dict) or not formula.get("components"):
            continue

        old_mid = _mid(formula, assumptions)
        before += old_mid
        # -1 for a use case not in the id mappings. The per-id rules then find nothing,
        # which is correct, but the PROPORTIONAL families still apply — they are keyed by
        # assumption, not by use case. Skipping the row entirely (as an earlier version
        # did) silently exempted all 59 fuel-spend use cases from their family scale.
        use_case_id = title_to_id.get((row.get("title") or "").strip(), -1)

        updated, notes = rewrite_formula(json.loads(json.dumps(formula)), use_case_id,
                                         factors)
        new_mid = _mid(updated, assumptions)
        after += new_mid
        if not notes:
            continue
        changed += 1
        row["hypothesized_value_json"] = updated

        # The seed also carries denormalized value_low/mid/high_mm columns and a static
        # low_mm/mid_mm/high_mm inside the formula. Left stale they would contradict the
        # components — and value_engine falls back to the static band whenever a formula
        # has no components, so a stale figure is not merely cosmetic.
        band_low = band_high = 0.0
        for component in updated["components"]:
            value = float(component.get("multiplier") or 0)
            for key in component.get("assumptionKeys") or []:
                value *= float(assumptions.get(key, 0) or 0)
            band_low += value * float(component.get("lowCoeff") or 1)
            band_high += value * float(component.get("highCoeff") or 1)
        for field, value in (("value_low_mm", band_low),
                             ("value_mid_mm", new_mid),
                             ("value_high_mm", band_high)):
            if field in row:
                row[field] = round(value, 2)
        for field, value in (("low_mm", band_low), ("mid_mm", new_mid),
                            ("high_mm", band_high)):
            if field in updated:
                updated[field] = round(value, 2)

    if write:
        seed_path.write_text(json.dumps(data, indent=1) + "\n")
    return before, after, changed


def _check_mappings() -> None:
    """Every rule must have a title, and every title a rule.

    This has already failed twice in development, both times silently: an id with no
    title never matches a seed row, so the correction is simply skipped and the reported
    total looks fine. Asserted at import so the script cannot run half-applied.
    """
    referenced = set()
    for mapping in (DISTRIBUTION, TRANSMISSION, REVENUE, CAPITAL_BUDGET):
        referenced |= set(mapping)
    referenced |= {use_case_id for use_case_id, _ in FIXED_RATES}

    missing_title = sorted(referenced - set(_ID_TITLES))
    if missing_title:
        raise SystemExit(
            f"_ID_TITLES has no entry for id(s) {missing_title}. Without a title these "
            "rules are silently skipped when rewriting seed_data.json.")
    unused = sorted(set(_ID_TITLES) - referenced)
    if unused:
        raise SystemExit(
            f"_ID_TITLES carries id(s) {unused} that no rule references — either a rule "
            "was removed or the title is stale.")
    for signature in FIXED_RATES:
        if signature not in FIXED_RATE_DISPLAYS:
            raise SystemExit(
                f"{signature} has a corrected multiplier but no display text; the UI "
                "would keep showing the old percentage in prose.")


def _title_to_id() -> dict[str, int]:
    """Title -> use-case id, inverted from the per-base mappings above.

    The mappings are keyed by id because that is what the database uses and what a
    reviewer can look up. The seed needs titles. Rather than maintaining both, the
    lookup is built from a single title list checked against the id maps, so a typo
    surfaces as an unmapped title in the report instead of a silent skip.
    """
    return {title: use_case_id for use_case_id, title in _ID_TITLES.items()}


def _mid(formula: dict, assumptions: dict[str, float]) -> float:
    """The mid figure, computed the way value_engine computes it.

    Reimplemented in three lines rather than imported, because importing server.* pulls
    in asyncpg and the app's config; every other script in scripts/ talks to Lakebase
    through psycopg2 and seed_lib instead. Kept deliberately identical to
    value_engine.compute_value_range() — the recalibration is verified against the live
    app's own /api figures afterwards, which is the check that matters.
    """
    total = 0.0
    for component in formula.get("components") or []:
        value = float(component.get("multiplier") or 0)
        for key in component.get("assumptionKeys") or []:
            value *= float(assumptions.get(key, 0) or 0)
        low = value * float(component.get("lowCoeff") or 1)
        high = value * float(component.get("highCoeff") or 1)
        total += (low + high) / 2
    return total


_check_mappings()


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_lib

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report every change without writing")
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--project", default="grid-atlas-pg")
    parser.add_argument("--branch", default="production")
    parser.add_argument("--endpoint", default="primary")
    parser.add_argument("--db", default="app")
    parser.add_argument("--seed-only", action="store_true",
                        help="rewrite scripts/seed_data.json and skip the database "
                             "(no Lakebase credentials needed)")
    args = parser.parse_args()

    seed_path = Path(__file__).resolve().parent / "seed_data.json"
    if args.seed_only:
        before, after, changed = recalibrate_seed(seed_path, write=not args.dry_run)
        print(f"seed: {changed} use case(s) changed, "
              f"${before:,.1f}M -> ${after:,.1f}M")
        if args.dry_run:
            print("dry run — nothing written")
        return 0

    conn = seed_lib.get_conn(args.profile, args.project, args.branch,
                             args.endpoint, args.db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM value_assumptions "
                    "WHERE account_id IS NULL OR account_id = "
                    "(SELECT id FROM accounts ORDER BY id LIMIT 1)")
        assumptions = {key: float(value) for key, value in cur.fetchall()}

        cur.execute("SELECT id, title, hypothesized_value_json FROM use_cases "
                    "WHERE hypothesized_value_json IS NOT NULL ORDER BY id")
        rows = cur.fetchall()

        # Family scale factors need the whole catalog, so they are computed before the
        # per-use-case pass rather than inside it.
        factors = proportional_factors(
            f for f in (_coerce(formula) for _, _, formula in rows) if f)
        for key, factor in factors.items():
            if factor < 1.0:
                print(f"family scale: {key} x{factor:.4f} "
                      f"(target {PROPORTIONAL[key] * 100:.0f}% in aggregate)")

        before = after = 0.0
        updates: list[tuple[str, int]] = []
        printed: list[str] = []

        for use_case_id, title, formula in rows:
            formula = _coerce(formula)
            if formula is None:
                continue

            before += _mid(formula, assumptions)
            updated, notes = rewrite_formula(json.loads(json.dumps(formula)),
                                             use_case_id, factors)
            after += _mid(updated, assumptions)

            if notes:
                for note in notes:
                    printed.append(f"  id={use_case_id:4d} {title[:46]:48s} {note}")
                updates.append((json.dumps(updated), use_case_id))

        print("\n".join(printed))
        print(f"\nuse cases changed: {len(updates)} of {len(rows)}")
        print(f"portfolio total:   ${before:,.1f}M  ->  ${after:,.1f}M")
        revenue = assumptions.get("annualRevenueMM") or 0
        if revenue:
            print(f"as % of revenue:   {before / revenue * 100:.1f}%  ->  "
                  f"{after / revenue * 100:.1f}%")

        if args.dry_run:
            print("\ndry run — nothing written")
            return 0

        # One transaction: a partial rewrite would leave the portfolio mixing old and
        # new percentages, which is the one state harder to explain than either.
        cur.executemany(
            "UPDATE use_cases SET hypothesized_value_json = %s::jsonb, "
            "updated_at = now() WHERE id = %s", updates)
        conn.commit()
        print(f"\nwrote {len(updates)} use case(s) to the database")

        # And the shipped catalog, or the next install reintroduces the whole problem.
        seed_before, seed_after, seed_changed = recalibrate_seed(seed_path, write=True)
        print(f"wrote {seed_changed} use case(s) to seed_data.json "
              f"(${seed_before:,.1f}M -> ${seed_after:,.1f}M at seeded assumptions)")
        print("Capture a snapshot in the app (Trend → Snapshot now) so the drop is a "
              "recorded event with a reason, not an unexplained cliff in the chart.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
