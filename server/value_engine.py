"""Parameterized value model (mirrors the parent app's businessValue engine).

Each use case's hypothesized_value_json holds a driver formula:
{
  "driver": "...",
  "components": [
    {"name","calculationDisplay","multiplier","assumptionKeys":[...],
     "lowCoeff","highCoeff"},
    ...
  ],
  "roiMonths": <int|null>
}

Per component annual value = multiplier * product(assumption[key] for key in assumptionKeys)
Low  = sum(component * lowCoeff)     High = sum(component * highCoeff)
Values are in $M (millions). This is a SAFE evaluator — pure multiplication of
resolved numbers; no eval() of arbitrary strings.

Changing any global assumption in value_assumptions re-quantifies every use case.
"""
from __future__ import annotations

import json

# NOTE: `from .db import db` is intentionally NOT imported at module scope.
# db.py imports asyncpg (a running-app-only dependency absent from the scripts/
# CLI environment). The pure helpers below — including calibrate_components and
# compute_value_range — must import cleanly with no asyncpg so that
# scripts/recalibrate_value_models.py can reuse the SAME calibration logic
# instead of duplicating it. The three async DB helpers import `db` lazily.


def _as_dict(formula):
    """Coerce a formula (dict or jsonb string) to a dict, or None if it isn't one.

    The type check has to happen AFTER parsing, not just on the dict path: a jsonb
    column holding a valid JSON array parses fine and then fails on `.get`. Because
    computed_value_map() maps over every use case, that turned one malformed row
    into a 500 for the whole portfolio list instead of a single blank badge.
    """
    if isinstance(formula, str):
        try:
            formula = json.loads(formula)
        except (ValueError, TypeError):
            return None
    return formula if isinstance(formula, dict) else None


def compute_value_range(formula, assumptions: dict[str, float]):
    """Return {low, mid, high} in $M, or None."""
    f = _as_dict(formula)
    if not f:
        return None
    comps = f.get("components") or []
    if not comps:
        if "low_mm" in f and "high_mm" in f:
            return {"low": f.get("low_mm"), "mid": f.get("mid_mm"), "high": f.get("high_mm")}
        return None
    low = high = 0.0
    for c in comps:
        v = float(c.get("multiplier", 0) or 0)
        for k in c.get("assumptionKeys", []) or []:
            v *= float(assumptions.get(k, 0) or 0)
        low += v * float(c.get("lowCoeff", 1) or 1)
        high += v * float(c.get("highCoeff", 1) or 1)
    return {"low": round(low, 2), "mid": round((low + high) / 2, 2), "high": round(high, 2)}


def compute_value(formula, assumptions: dict[str, float]):
    """Return the mid value in $M (scalar) for list/badge display."""
    r = compute_value_range(formula, assumptions)
    return r["mid"] if r else None


def compute_realized(uc_row: dict, assumptions: dict[str, float]):
    """Realized value ($M), two ways:
      - override: realized_override_enabled -> realized_override_amount ($M)
      - parameterized: realized_value_json.components[] using each component's
        (actual) multiplier x Π(assumptions). Uses shared assumptions so global
        edits re-quantify realized too.
    Returns {"mode": "override"|"calculated"|"none", "value": <$M|None>, "note": <str|None>}.
    """
    if uc_row.get("realized_override_enabled"):
        amt = uc_row.get("realized_override_amount")
        return {"mode": "override", "value": (float(amt) if amt is not None else None),
                "note": uc_row.get("realized_override_note")}
    rvj = _as_dict(uc_row.get("realized_value_json"))
    if rvj and (rvj.get("components")):
        total = 0.0
        for c in rvj["components"]:
            v = float(c.get("multiplier", 0) or 0)
            for k in c.get("assumptionKeys", []) or []:
                v *= float(assumptions.get(k, 0) or 0)
            total += v
        return {"mode": "calculated", "value": round(total, 2), "note": None}
    return {"mode": "none", "value": None, "note": None}


async def load_assumptions() -> dict[str, float]:
    """The current account's calibrated assumptions.

    Scoped, because this is THE customer-specific input: Eversource's SAIDI minute
    value and National Grid's are different numbers, and an unscoped read here would
    quantify one utility's portfolio with another's assumptions. Every dollar figure
    in the app flows through this function, so getting the scope wrong would be
    wrong everywhere at once and obvious nowhere.

    Rows with a NULL account_id are shared defaults, and an account's own row wins —
    so a fresh account starts on the seeded 34 and diverges only where it has been
    calibrated.
    """
    from . import accounts
    from .db import db

    account_id = await accounts.current()
    if account_id is None:
        rows = await db.fetch("SELECT key, value FROM value_assumptions")
    else:
        rows = await db.fetch(
            """SELECT DISTINCT ON (key) key, value
               FROM value_assumptions
               WHERE account_id = $1 OR account_id IS NULL
               -- account-specific first, so DISTINCT ON keeps it over the default
               ORDER BY key, (account_id IS NULL)""", account_id)
    return {r["key"]: float(r["value"]) for r in rows}


async def computed_value_map() -> dict[int, float | None]:
    """Map use_case_id -> computed mid hypothesized value ($M) from current assumptions."""
    from .db import db

    assumptions = await load_assumptions()
    rows = await db.fetch("SELECT id, hypothesized_value_json FROM use_cases")
    return {r["id"]: compute_value(r["hypothesized_value_json"], assumptions) for r in rows}


async def computed_range_map() -> dict[int, dict | None]:
    from .db import db

    assumptions = await load_assumptions()
    rows = await db.fetch("SELECT id, hypothesized_value_json FROM use_cases")
    return {r["id"]: compute_value_range(r["hypothesized_value_json"], assumptions) for r in rows}


# ---------------------------------------------------------------------------
# Shared cost/value helpers
# ---------------------------------------------------------------------------
# Lived as byte-identical copies in routes/joint_funding.py and
# routes/source_recommendations.py, including the cost table. Both rank
# investments by value-per-cost, so a change to one copy would have silently
# made the two recommenders disagree about the same asset.

# Directional installed cost to land one source, by t-shirt effort. Used only when
# a data asset has no explicit ingest_cost_low/high; these order options for a
# human rather than pretending to be a quote.
EFFORT_COST: dict[str, tuple[int, int]] = {
    "S": (50_000, 150_000),
    "M": (150_000, 400_000),
    "L": (400_000, 1_000_000),
    "XL": (1_000_000, 2_500_000),
}


def use_case_value(use_case: dict, assumptions: dict) -> float:
    """Mid-point annual $M for a use case, or 0.0 when it has no value model."""
    rng = compute_value_range(use_case.get("hypothesized_value_json"), assumptions)
    return rng["mid"] if rng else 0.0


def asset_cost(asset: dict) -> tuple[float, float]:
    """(low, high) $ to land a data asset.

    Prefers explicit per-asset costs when a customer has entered them; otherwise
    falls back to the effort-based band.
    """
    if asset.get("ingest_cost_low") and asset.get("ingest_cost_high"):
        return float(asset["ingest_cost_low"]), float(asset["ingest_cost_high"])
    low, high = EFFORT_COST.get(asset.get("ingest_effort") or "M", EFFORT_COST["M"])
    return float(low), float(high)


# ---------------------------------------------------------------------------
# Value model magnitude calibration
# ---------------------------------------------------------------------------
def calibrate_components(components: list[dict], assumption_values: dict[str, float],
                        annual_revenue_mm: float | None = None) -> list[dict]:
    """Apply two-layer calibration to value-model components.

    CONTEXT: The value engine computes per-component annual value in $M as:
        multiplier * product(assumption_values) * coeff

    The engine is UNIT-AGNOSTIC — the multiplier must absorb ALL unit conversion.
    Seeded catalog models have hand-calibrated multipliers (~1e-9 to ~8.0 depending
    on units). LLMs cannot reliably compute these magnitudes, and existing persisted
    models may have been generated before calibration was added.

    This function applies deterministic calibration to tame absurd values:

    LAYER 1 (per-component rescaling):
        For each component, compute rawMid = multiplier * product(assumption values).
        If rawMid exceeds the per-component ceiling (5% of annualRevenueMM),
        rescale that component's multiplier to bring it under the ceiling.

    LAYER 2 (global sanity clamp):
        After per-component calibration, compute the total mid value. If it exceeds
        the global ceiling (25% of annualRevenueMM), proportionally scale ALL
        component multipliers down so the total lands at/under the ceiling.

    Args:
        components: List of component dicts with keys: name, multiplier,
                   assumptionKeys, lowCoeff, highCoeff
        assumption_values: Dict mapping assumption keys to their float values
        annual_revenue_mm: Annual revenue in $M for ceiling calculation.
                          Defaults to 5000 if not provided.

    Returns:
        Calibrated copy of components (original list is not mutated).

    IMPORTANT: This is idempotent — running it on an already-calibrated model
    is a no-op because values are already under the ceiling.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Work on a deep copy so we don't mutate the input
    comps = [dict(c) for c in components]

    if not comps:
        return comps

    # Default annual revenue if not provided
    annual_revenue = annual_revenue_mm if annual_revenue_mm is not None else 5000.0

    # ---------------------------------------------------------------------------
    # LAYER 1: Per-component magnitude rescaling
    # ---------------------------------------------------------------------------
    # Cap any single component at 5% of annualRevenueMM (~250 $M for the default
    # 5000 $M revenue). If a component's rawMid (multiplier * product(assumptions))
    # is absurd, rescale its multiplier to bring it under the ceiling.
    max_component_ceiling = 0.05 * annual_revenue  # 5% of revenue per component

    for comp in comps:
        # Compute rawMid = multiplier * product(assumption values for this component)
        raw_mid = comp.get("multiplier", 0)
        if not raw_mid:
            continue

        for key in comp.get("assumptionKeys", []):
            raw_mid *= assumption_values.get(key, 0)

        # If rawMid exceeds the ceiling, rescale the multiplier
        if raw_mid > max_component_ceiling and raw_mid > 0:
            # Compute the scaling factor needed
            scale_factor = max_component_ceiling / raw_mid
            # Apply the scaling to the multiplier
            comp["multiplier"] = comp["multiplier"] * scale_factor
            logger.info(
                "Calibrated component '%s': raw_mid=%.2f $M exceeded ceiling %.2f $M, "
                "scaled multiplier by %.2e to %.2e",
                comp.get("name", "unnamed"), raw_mid, max_component_ceiling,
                scale_factor, comp["multiplier"]
            )

    # ---------------------------------------------------------------------------
    # LAYER 2: Global sanity clamp on total value
    # ---------------------------------------------------------------------------
    # After per-component calibration, compute the total mid value. If it exceeds
    # 25% of annualRevenueMM (or 1250 $M for default 5000 $M revenue),
    # proportionally scale ALL component multipliers down so the total lands
    # at/under the ceiling.
    max_total_ceiling = 0.25 * annual_revenue  # 25% of revenue for whole use case

    # Compute total mid after Layer 1 calibration
    total_mid = 0.0
    for comp in comps:
        comp_value = comp.get("multiplier", 0)
        for key in comp.get("assumptionKeys", []):
            comp_value *= assumption_values.get(key, 0)
        # Use midpoint of low/high coefficients
        low_coeff = comp.get("lowCoeff", 1.0)
        high_coeff = comp.get("highCoeff", 1.0)
        mid_coeff = (low_coeff + high_coeff) / 2.0
        total_mid += comp_value * mid_coeff

    # If total still exceeds the ceiling, proportionally scale ALL components down
    if total_mid > max_total_ceiling and total_mid > 0:
        global_scale_factor = max_total_ceiling / total_mid
        logger.info(
            "Global sanity clamp: total_mid=%.2f $M exceeded ceiling %.2f $M, "
            "scaling ALL multipliers by %.4f",
            total_mid, max_total_ceiling, global_scale_factor
        )
        for comp in comps:
            comp["multiplier"] = comp.get("multiplier", 0) * global_scale_factor

    return comps
