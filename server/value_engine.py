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

from .db import db


def _as_dict(formula):
    if isinstance(formula, str):
        try:
            return json.loads(formula)
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
    rows = await db.fetch("SELECT key, value FROM value_assumptions")
    return {r["key"]: float(r["value"]) for r in rows}


async def computed_value_map() -> dict[int, float | None]:
    """Map use_case_id -> computed mid hypothesized value ($M) from current assumptions."""
    assumptions = await load_assumptions()
    rows = await db.fetch("SELECT id, hypothesized_value_json FROM use_cases")
    return {r["id"]: compute_value(r["hypothesized_value_json"], assumptions) for r in rows}


async def computed_range_map() -> dict[int, dict | None]:
    assumptions = await load_assumptions()
    rows = await db.fetch("SELECT id, hypothesized_value_json FROM use_cases")
    return {r["id"]: compute_value_range(r["hypothesized_value_json"], assumptions) for r in rows}
