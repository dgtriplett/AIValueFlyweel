"""Company research: profile, lines of business, and VALUE-ASSUMPTION calibration.

WHY THE ASSUMPTIONS ARE THE POINT
---------------------------------
The 34 value assumptions drive every dollar figure the app produces. Shipped as
generic P&U defaults they describe a hypothetical 2-million-customer utility, so a
first conversation at a real account opens with "where did these numbers come
from?" — and the honest answer is "nowhere". Every downstream number is then
directionally meaningless.

So this module's main job is not generating use cases (the generation agent already
does that, grounded in real data availability). It is calibrating the assumptions to
a named company, and being explicit about how confident each one is:

    high    directly attested — a figure the model recalls from a filing
    medium  derived from an attested figure (per-customer ratio x customer count)
    low     industry-typical for a utility of this size and type

A `low`-confidence number is still far better than a generic default, because it is
at least scaled to the right company — but the badge tells a reviewer which numbers
to check first, which is the difference between a defensible model and a black box.

DERIVATION GUIDANCE
-------------------
`ASSUMPTION_GUIDE` gives the model, per key, what the number means and how to get
there. Without it the model guesses at semantics: `saidiMinuteValueMM` is not
"SAIDI in minutes", it is the $M value of avoiding one system-average minute of
interruption, and a model left to infer that produces a number three orders of
magnitude wrong. Every key here was written by reading how value_engine.py
actually uses it.

NOTHING IS WRITTEN DIRECTLY. Research produces a proposal; applying it goes
through the propose/confirm gate, because recalibrating the assumptions
re-quantifies the entire portfolio.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# What each assumption means and how to derive it
#   key -> (unit, meaning, how to derive)
# ---------------------------------------------------------------------------
ASSUMPTION_GUIDE: dict[str, tuple[str, str, str]] = {
    # --- Utility profile: the anchors. Get these right and ratios do the rest. ---
    "customerCount": (
        "count", "Total retail customer accounts served (electric).",
        "Usually published in the 10-K, FERC Form 1, or the company website. This "
        "is the single most important anchor — many other figures are derived from "
        "it. Generation-only or T-only companies: use 0."),
    "annualRevenueMM": (
        "$M", "Total annual operating revenue.",
        "From the most recent 10-K or annual report. Use the utility subsidiary's "
        "revenue, not a diversified parent's consolidated total."),
    "omBudgetMM": (
        "$M", "Annual operations & maintenance expense (not capital).",
        "From the income statement's O&M line. If unavailable, O&M typically runs "
        "25-40% of revenue for a vertically integrated utility."),
    "generationFleetMW": (
        "MW", "Owned/operated generation nameplate capacity.",
        "Sum of the generation fleet. A wires-only utility (no generation) is 0 — "
        "say so rather than inventing a figure."),
    "tdLineMiles": (
        "miles", "Combined transmission + distribution line miles.",
        "From the 10-K or state commission filings. Roughly 0.03-0.06 line miles "
        "per customer for a typical US IOU if no figure is available."),
    "transformerCount": (
        "count", "Distribution transformers in service.",
        "Rarely published. A common ratio is 0.2-0.3 distribution transformers per "
        "customer; derive from customerCount and say so."),

    # --- Labor rates: regional, and usually not company-specific ---
    "fieldCrewRate": (
        "$/hr", "Fully-loaded hourly cost of a field crew member (lineworker).",
        "Fully loaded = wages + benefits + overhead, typically 1.5-2x base wage. "
        "Union agreements and region drive this; $70-110/hr is the usual US range."),
    "engineerRate": (
        "$/hr", "Fully-loaded hourly cost of an internal engineer.",
        "Typically $100-150/hr fully loaded in the US."),
    "dataScienceRate": (
        "$/hr", "Fully-loaded hourly cost of a data scientist / ML engineer.",
        "Typically $150-200/hr fully loaded; higher in high-cost metros."),

    # --- Revenue & customer ---
    "avgResidentialRevenue": (
        "$/yr", "Average annual revenue per residential customer.",
        "Average monthly bill x 12. Derive from the state's average residential "
        "rate (c/kWh) x typical annual usage (~10,500 kWh US average, materially "
        "higher in hot-summer states, lower in mild coastal climates)."),
    "avgCommercialRevenue": (
        "$/yr", "Average annual revenue per commercial customer.",
        "Typically 6-12x the residential figure. Scale with the territory's "
        "commercial/industrial mix."),
    "customerChurnPct": (
        "%", "Annual customer attrition.",
        "In a regulated monopoly territory this is near-zero (customer moves, not "
        "competitive loss) — 0.5-3%. In a retail-choice state it is materially "
        "higher and worth calling out."),

    # --- Grid & operations ---
    "transformerReplaceCost": (
        "$", "Installed cost to replace one distribution transformer.",
        "Equipment + labor + outage cost. $8k-25k for a typical distribution unit; "
        "much higher for substation power transformers. Use the distribution figure "
        "unless the utility is transmission-only."),
    "avgStormCostMM": (
        "$M", "Average total cost of one major storm event (restoration + damage).",
        "From storm-cost recovery filings, which utilities file after major events. "
        "Scales strongly with territory size and exposure — coastal/hurricane and "
        "ice-storm territories are far higher."),
    "stormsPerYear": (
        "count", "Major storm events per year (ones that trigger emergency response).",
        "From reliability reports. Typically 2-6; higher on the Gulf and "
        "Atlantic coasts."),
    "saidiMinuteValueMM": (
        "$M", "$M value of avoiding ONE system-average-interruption-duration minute.",
        "NOT a duration. The economic value of reducing SAIDI by one minute across "
        "the whole customer base — restoration cost avoided, plus regulatory "
        "performance incentive, plus customer-outage cost. Scale with "
        "customerCount: larger utilities have a much larger per-minute value."),
    "currentSAIDI": (
        "min", "Current System Average Interruption Duration Index, annual minutes.",
        "Reported to state commissions and IEEE. US IOU average is ~100-200 min "
        "excluding major events; higher including them. Say which basis you used."),

    # --- Fuel & generation (0 for a wires-only utility) ---
    "fuelCostPerMMBTU": (
        "$/MMBtu", "Delivered fuel cost.",
        "Mostly natural gas for US utilities; use the delivered cost including "
        "transport, not Henry Hub. 0 if the utility owns no thermal generation."),
    "annualFuelSpendMM": (
        "$M", "Annual fuel + purchased power expense.",
        "From the income statement. Often the largest single O&M line for a "
        "vertically integrated utility. 0 for wires-only."),
    "capacityPriceMWDay": (
        "$/MW-day", "Capacity market clearing price in the utility's zone.",
        "From the relevant ISO/RTO capacity auction (PJM RPM, ISO-NE FCA, MISO "
        "PRA). 0 in regions without a capacity market (most of the West, ERCOT)."),

    # --- Financial ---
    "badDebtPct": (
        "% revenue", "Uncollectible accounts as a percent of revenue.",
        "Typically 0.5-3%; higher in territories with high energy burden or a "
        "disconnection moratorium."),
    "capitalBudgetMM": (
        "$M", "Annual total capital expenditure.",
        "From the 10-K or the rate case capital plan. Frequently 1.5-3x annual O&M "
        "for a utility in a heavy grid-modernization cycle."),
    "procurementSpendMM": (
        "$M", "Annual third-party procurement / supply chain spend.",
        "Materials + contracted services. Often 20-40% of combined O&M and capex."),

    # --- Safety & workforce ---
    "oshaIncidentCostK": (
        "$K", "Average fully-loaded cost of one OSHA-recordable incident.",
        "Direct medical + indirect (lost time, replacement labor, investigation). "
        "$100-300K is the usual range for utility work."),
    "oshaRecordablesPerYear": (
        "count", "OSHA-recordable incidents per year.",
        "Derive from workforce size and the utility DART/TRIR rate (~1-2 "
        "recordables per 100 workers per year is a common industry figure)."),
    "avgReplacementCostK": (
        "$K", "Cost to recruit, hire, and train a replacement for one employee.",
        "For skilled utility roles (lineworker, operator) frequently 50-150% of "
        "annual salary once training and apprenticeship are included."),
    "workforceSize": (
        "count", "Total employees.",
        "From the 10-K. Roughly 1 employee per 300-600 customers for a vertically "
        "integrated utility."),
    "retirementEligiblePct": (
        "%", "Percent of the workforce eligible to retire within ~5 years.",
        "The utility industry skews old; 25-45% is the common range and is often "
        "discussed explicitly in workforce-planning disclosures."),

    # --- Regulatory ---
    "rateCasePrepCostMM": (
        "$M", "Cost to prepare and litigate one general rate case.",
        "Internal labor + outside counsel + consultants + testimony. $2-10M "
        "depending on jurisdiction and contentiousness."),
    "regulatoryPenaltyRiskMM": (
        "$M", "Annual expected exposure to regulatory penalties.",
        "Reliability performance penalties, NERC CIP violations, safety findings. "
        "Scale with size and the jurisdiction's enforcement posture."),

    # --- Call center ---
    "callsPerYear": (
        "count", "Annual inbound contact-center call volume.",
        "Commonly 2-6 calls per customer per year; multiply by customerCount."),
    "costPerCall": (
        "$", "Fully-loaded cost to handle one call.",
        "Agent time + systems + overhead. $5-12 for a utility contact center."),

    # --- Scaling: split the capital budget across the wires businesses ---
    "transmissionCapexMM": (
        "$M", "Annual transmission capital spend.",
        "A share of capitalBudgetMM. Transmission is typically 20-40% of wires "
        "capex; 0 for a distribution-only utility."),
    "distributionCapexMM": (
        "$M", "Annual distribution capital spend.",
        "A share of capitalBudgetMM, typically the largest wires component."),
}

CONFIDENCE_LEVELS = ("high", "medium", "low")
SEGMENTS = ("generation", "transmission", "distribution", "retail")

# A calibrated value that is orders of magnitude from the shipped default usually
# means the model misread the unit rather than found a surprising company. Flagged
# for review rather than rejected, because a genuinely huge or tiny utility exists.
IMPLAUSIBLE_RATIO = 1000.0


class ResearchError(ValueError):
    """Malformed or unusable research output."""


def build_profile_prompt(company_name: str) -> str:
    """Establish who the company is. Deliberately separate from the assumption
    pass: the profile grounds those numbers, and asking for both at once produces
    a worse answer to each."""
    return f"""You are a Power & Utilities industry analyst researching {company_name}.

Report what you actually know about this company. If you are unsure whether a
detail applies to this specific company rather than its parent or a similarly named
utility, say so in `description` rather than guessing.

Return STRICT JSON:
{{
  "company_name": "the canonical legal/operating name",
  "utility_type": "investor-owned | municipal | cooperative | G&T cooperative | generation-only | transmission-only | federal/state authority",
  "segments": ["generation","transmission","distribution","retail"],
  "service_territory": "states/regions served, and approximate size",
  "regulator": "primary economic regulator(s), e.g. 'MA DPU, FERC'",
  "iso_rto": "the ISO/RTO market they operate in, or 'none (vertically integrated, non-market)'",
  "description": "3-5 sentences: what they do, scale, notable characteristics, and anything that makes their economics unusual",
  "confidence": "high | medium | low",
  "caveats": "anything you are unsure about, or an empty string"
}}

`segments` must contain ONLY the segments this company actually operates — that
determines which use cases even apply, so an inflated list does real damage."""


def build_assumption_prompt(company_name: str, profile: dict,
                            assumptions: list[dict]) -> str:
    """Calibrate the value assumptions to this company.

    Every key carries its meaning and derivation guidance, because the names alone
    are ambiguous in ways that produce wildly wrong numbers (see
    ASSUMPTION_GUIDE's note on saidiMinuteValueMM). Current values are shown as
    generic defaults to be replaced, not as hints to nudge.
    """
    profile_lines = "\n".join(
        f"  {k}: {v}" for k, v in profile.items()
        if k in ("utility_type", "segments", "service_territory", "regulator",
                 "iso_rto", "description") and v)

    entries = []
    for assumption in assumptions:
        key = assumption["key"]
        guide = ASSUMPTION_GUIDE.get(key)
        current = assumption.get("value")
        if guide:
            unit, meaning, how = guide
            entries.append(
                f'- {key} ({unit}) — {meaning}\n'
                f'    how to derive: {how}\n'
                f'    current generic default: {current}')
        else:
            entries.append(
                f'- {key} ({assumption.get("unit") or "?"}) — '
                f'{assumption.get("label") or key}\n'
                f'    current generic default: {current}')
    listed = "\n".join(entries)

    return f"""You are a Power & Utilities value-engineering analyst calibrating a
financial model to {company_name}.

COMPANY PROFILE
{profile_lines or "  (limited profile available)"}

The model below ships with GENERIC industry defaults that describe a hypothetical
utility. Replace each with your best estimate FOR THIS COMPANY. Every downstream
dollar figure in the application depends on these, so scale matters more than
precision — being within 30% is useful, being an order of magnitude out is worse
than useless.

RULES
1. Give a number for EVERY key. Never omit one.
2. If a key does not apply to this company (fuel cost for a wires-only utility,
   capacity price outside a capacity market), return 0 and say why in `rationale`.
3. Set `confidence` honestly:
     "high"   — you recall this specific figure for this company (a filing, a
                published report, an annual report)
     "medium" — you derived it from a figure you do recall (a per-customer ratio
                applied to a customer count you are confident in)
     "low"    — an industry-typical value for a utility of this size and type
   A low-confidence number scaled to the right company is valuable. A number you
   label "high" that you actually guessed is corrosive, because a reviewer will
   trust it.
4. `basis` names the source or derivation, e.g. "2024 10-K", "FERC Form 1",
   "derived: 0.25 transformers/customer x 3.6M customers", "industry typical for a
   mid-size IOU".
5. `rationale` is one or two sentences shown verbatim next to the number, so it has
   to make sense on its own.

ASSUMPTIONS TO CALIBRATE
{listed}

Return STRICT JSON:
{{"assumptions": [
  {{"key": "customerCount", "value": 3600000, "confidence": "high",
    "basis": "2024 10-K", "rationale": "Serves about 3.6 million electric customers across three states."}}
]}}"""


def parse_profile(parsed: dict | None) -> dict:
    """Validate a profile response."""
    if not parsed:
        raise ResearchError("The model returned no company profile.")
    name = str(parsed.get("company_name") or "").strip()
    if not name:
        raise ResearchError("The model returned a profile with no company name.")

    raw_segments = parsed.get("segments") or []
    if isinstance(raw_segments, str):
        raw_segments = [s.strip() for s in raw_segments.split(",")]
    segments = [s for s in (str(x).strip().lower() for x in raw_segments)
                if s in SEGMENTS]

    confidence = str(parsed.get("confidence") or "").lower()
    return {
        "company_name": name,
        "utility_type": str(parsed.get("utility_type") or "").strip() or None,
        "segments": segments,
        "service_territory": str(parsed.get("service_territory") or "").strip() or None,
        "regulator": str(parsed.get("regulator") or "").strip() or None,
        "iso_rto": str(parsed.get("iso_rto") or "").strip() or None,
        "description": str(parsed.get("description") or "").strip() or None,
        "confidence": confidence if confidence in CONFIDENCE_LEVELS else "low",
        "caveats": str(parsed.get("caveats") or "").strip() or None,
    }


def _coerce_number(value) -> float | None:
    """Accept the shapes models emit for numbers: 3600000, "3.6M", "$1,200".

    Models are told to return bare numbers and mostly do, but a formatted string
    slips through often enough that discarding it would lose good answers.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "").replace("$", "")
    if not text:
        return None
    multiplier = 1.0
    match = re.fullmatch(r"(-?\d*\.?\d+)\s*([kKmMbB])?", text)
    if not match:
        return None
    if match.group(2):
        multiplier = {"k": 1e3, "m": 1e6, "b": 1e9}[match.group(2).lower()]
    try:
        return float(match.group(1)) * multiplier
    except ValueError:
        return None


def parse_assumptions(parsed: dict | None,
                      current: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate calibrated assumptions. Returns (proposals, warnings).

    Only keys that exist in this install are accepted — an invented key would write
    a value the engine never reads while looking like it worked. Missing keys are
    reported rather than silently defaulted, because a partially calibrated model
    mixing real and generic figures is the most misleading possible state.
    """
    warnings: list[str] = []
    if not parsed:
        return [], ["The model returned no calibrated assumptions."]

    from .generation import unwrap_list
    items = unwrap_list(parsed, "assumptions")
    if not items:
        return [], ["The model returned no calibrated assumptions."]

    by_key = {a["key"]: a for a in current}
    proposals: list[dict] = []
    seen: set[str] = set()
    unknown: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in by_key:
            if key:
                unknown.append(key)
            continue
        if key in seen:
            continue
        seen.add(key)

        value = _coerce_number(item.get("value"))
        if value is None:
            warnings.append(f"{key}: could not read a number from "
                            f"{item.get('value')!r} — left at the default.")
            continue
        if value < 0:
            warnings.append(f"{key}: negative value ({value}) rejected.")
            continue

        confidence = str(item.get("confidence") or "").lower()
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "low"

        before = float(by_key[key].get("value") or 0)
        # A huge swing is far more often a unit misread than a real finding, so it
        # is surfaced for review rather than trusted or dropped.
        if before > 0 and value > 0:
            ratio = max(value / before, before / value)
            if ratio >= IMPLAUSIBLE_RATIO:
                warnings.append(
                    f"{key}: proposed {value:,.0f} vs default {before:,.0f} "
                    f"({ratio:,.0f}x) — check the unit "
                    f"({by_key[key].get('unit') or '?'}) before applying.")
                confidence = "low"

        proposals.append({
            "key": key,
            "label": by_key[key].get("label") or key,
            "unit": by_key[key].get("unit"),
            "value_before": before,
            "value_proposed": value,
            "confidence": confidence,
            "basis": str(item.get("basis") or "").strip() or None,
            "rationale": str(item.get("rationale") or "").strip() or None,
            "pct_change": (round(100 * (value - before) / before, 1)
                           if before else None),
        })

    if unknown:
        warnings.append(
            "Ignored assumption keys this install does not have: "
            + ", ".join(sorted(set(unknown))[:8]))
    missing = sorted(set(by_key) - seen)
    if missing:
        warnings.append(
            f"{len(missing)} assumption(s) were not calibrated and keep their "
            f"generic default: {', '.join(missing[:6])}"
            + ("…" if len(missing) > 6 else ""))

    return proposals, warnings


def summarize_assumptions(proposals: list[dict]) -> dict:
    """Rollup for the review card."""
    by_confidence = {level: 0 for level in CONFIDENCE_LEVELS}
    for proposal in proposals:
        by_confidence[proposal["confidence"]] += 1
    changed = [p for p in proposals
               if p["value_before"] != p["value_proposed"]]
    return {
        "total": len(proposals),
        "changed": len(changed),
        "unchanged": len(proposals) - len(changed),
        "by_confidence": by_confidence,
        # The number a reviewer should actually act on.
        "needs_review": by_confidence["low"],
    }


RESPONSE_SCHEMA_PROFILE = {
    "type": "json_schema",
    "json_schema": {
        "name": "company_profile",
        "schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"},
                "utility_type": {"type": "string"},
                "segments": {"type": "array", "items": {"type": "string"}},
                "service_territory": {"type": "string"},
                "regulator": {"type": "string"},
                "iso_rto": {"type": "string"},
                "description": {"type": "string"},
                "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
                "caveats": {"type": "string"},
            },
            "required": ["company_name", "utility_type", "segments", "description"],
        },
        "strict": True,
    },
}

RESPONSE_SCHEMA_ASSUMPTIONS = {
    "type": "json_schema",
    "json_schema": {
        "name": "calibrated_assumptions",
        "schema": {
            "type": "object",
            "properties": {
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "number"},
                            "confidence": {"type": "string",
                                           "enum": list(CONFIDENCE_LEVELS)},
                            "basis": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["key", "value", "confidence", "rationale"],
                    },
                },
            },
            "required": ["assumptions"],
        },
        "strict": True,
    },
}
