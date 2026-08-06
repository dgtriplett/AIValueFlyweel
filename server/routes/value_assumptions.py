"""Global value assumptions CRUD + portfolio value computation."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, rows_to_list, write_audit
from ..db import db
from ..readiness import readiness_map
from ..value_engine import compute_realized, compute_value_range, load_assumptions

router = APIRouter(prefix="/value-assumptions", tags=["value_assumptions"])


class AssumptionUpdate(BaseModel):
    value: float


@router.get("")
async def list_assumptions():
    rows = await db.fetch("SELECT * FROM value_assumptions ORDER BY category, key")
    return rows_to_list(rows)


@router.put("/{key}")
async def update_assumption(key: str, body: AssumptionUpdate, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        "UPDATE value_assumptions SET value=$1 WHERE key=$2 RETURNING *",
        body.value, key,
    )
    if row is None:
        raise HTTPException(404, "Assumption not found")
    await write_audit("value_assumption", row["id"], "update", actor,
                      {"key": key, "value": body.value})
    return dict(row)


@router.get("/computed/portfolio")
async def portfolio_value():
    """Computed hypothesized + realized value per use case + portfolio totals ($M)."""
    assumptions = await load_assumptions()
    # Portfolio value totals reflect the confirmed set only (in_portfolio=true).
    rows = await db.fetch("SELECT * FROM use_cases WHERE in_portfolio = true")
    rmap = await readiness_map()
    per = {}
    tot_hyp = tot_hyp_buildable = tot_real = 0.0
    for r in rows:
        d = dict(r)
        rng = compute_value_range(d.get("hypothesized_value_json"), assumptions)
        realized = compute_realized(d, assumptions)
        hyp_mid = rng["mid"] if rng else None
        readiness = (rmap.get(r["id"]) or {}).get("readiness")
        per[str(r["id"])] = {"hypothesized": hyp_mid, "realized": realized,
                             "readiness": readiness}
        if hyp_mid:
            tot_hyp += hyp_mid
            if readiness in ("shovel_ready", "nearly_ready", "awaiting_prerequisites"):
                tot_hyp_buildable += hyp_mid
        if realized["value"]:
            tot_real += realized["value"]
    return {
        "assumptions": assumptions,
        "per_use_case": per,
        "total_hypothesized_value": round(tot_hyp, 2),
        "total_hypothesized_buildable_value": round(tot_hyp_buildable, 2),
        "total_realized_value": round(tot_real, 2),
    }
