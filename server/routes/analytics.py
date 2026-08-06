"""Analytics aggregations for the value dashboards (Phase 7a).

Everything value-related is computed through the parameterized value engine so
the dashboards react live to assumption edits.
"""
from fastapi import APIRouter

from ..db import db
from ..readiness import readiness_map
from ..value_engine import compute_realized, compute_value_range, load_assumptions

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
async def dashboard():
    assumptions = await load_assumptions()
    # Portfolio-scoped: dashboards reflect the customer's confirmed set only.
    ucs = [dict(u) for u in await db.fetch(
        "SELECT * FROM use_cases WHERE in_portfolio = true ORDER BY id")]
    lobs = {l["id"]: l["name"] for l in await db.fetch("SELECT id, name FROM lobs")}
    rmap = await readiness_map()

    # per-UC value
    for u in ucs:
        rng = compute_value_range(u.get("hypothesized_value_json"), assumptions)
        u["_hyp"] = rng["mid"] if rng else 0
        u["_real"] = (compute_realized(u, assumptions) or {}).get("value") or 0
        u["_readiness"] = (rmap.get(u["id"]) or {}).get("readiness")
        u["_lob"] = lobs.get(u["lob_id"], "Unassigned")

    total_hyp = round(sum(u["_hyp"] or 0 for u in ucs), 2)
    # buildable = value from use cases that are NOT data-blocked (readiness other
    # than 'blocked' — includes awaiting_prerequisites, whose data is in place).
    _buildable = ("shovel_ready", "nearly_ready", "awaiting_prerequisites")
    total_hyp_buildable = round(
        sum(u["_hyp"] or 0 for u in ucs if u["_readiness"] in _buildable), 2)
    total_real = round(sum(u["_real"] or 0 for u in ucs), 2)

    # waterfall: hypothesized by status bucket -> realized
    status_order = ["not_started", "scoping", "in_progress", "live", "value_realized"]
    by_status = {s: 0.0 for s in status_order}
    for u in ucs:
        by_status[u["status"]] = round(by_status.get(u["status"], 0) + (u["_hyp"] or 0), 2)

    # LOB coverage: count + hyp + realized per domain
    lob_cov = {}
    for u in ucs:
        d = lob_cov.setdefault(u["_lob"], {"lob": u["_lob"], "count": 0, "hyp": 0.0, "real": 0.0, "live": 0})
        d["count"] += 1
        d["hyp"] = round(d["hyp"] + (u["_hyp"] or 0), 2)
        d["real"] = round(d["real"] + (u["_real"] or 0), 2)
        if u["status"] in ("live", "value_realized"):
            d["live"] += 1

    # readiness heatmap: domain x phase -> count
    heatmap = {}
    for u in ucs:
        key = (u["_lob"], u.get("phase") or 0)
        heatmap[key] = heatmap.get(key, 0) + 1
    heatmap_rows = [{"lob": k[0], "phase": k[1], "count": v} for k, v in heatmap.items()]

    # readiness distribution
    readiness_dist = {"shovel_ready": 0, "awaiting_prerequisites": 0, "nearly_ready": 0, "blocked": 0}
    for u in ucs:
        if u["_readiness"] in readiness_dist:
            readiness_dist[u["_readiness"]] += 1

    # status distribution
    status_dist = {s: 0 for s in status_order}
    for u in ucs:
        status_dist[u["status"]] = status_dist.get(u["status"], 0) + 1

    # data-asset utilization: how many UCs require each asset (top 15)
    util = await db.fetch(
        """SELECT da.id, da.source_category, da.module, da.ingestion_status,
                  COUNT(ura.use_case_id) AS uc_count
           FROM data_assets da
           LEFT JOIN uc_requires_asset ura ON ura.data_asset_id = da.id
           GROUP BY da.id, da.source_category, da.module, da.ingestion_status
           ORDER BY uc_count DESC, da.id LIMIT 15""")
    utilization = [{"label": f"{r['source_category'] or ''} · {r['module']}",
                    "uc_count": int(r["uc_count"] or 0), "ingestion_status": r["ingestion_status"]}
                   for r in util]

    # cumulative realized by fiscal period (from value_records)
    vr = await db.fetch(
        "SELECT fiscal_period, COALESCE(SUM(amount),0) AS amt FROM value_records "
        "WHERE kind='realized' GROUP BY fiscal_period ORDER BY fiscal_period")
    realized_by_period = [{"period": r["fiscal_period"] or "—", "amount": float(r["amt"])} for r in vr]

    return {
        "totals": {"hypothesized_mm": total_hyp,
                   "hypothesized_buildable_mm": total_hyp_buildable,
                   "realized_mm": total_real,
                   "capture_pct": round(100 * total_real / total_hyp, 1) if total_hyp else 0},
        "waterfall_by_status": [{"status": s, "hyp_mm": by_status[s]} for s in status_order],
        "lob_coverage": sorted(lob_cov.values(), key=lambda x: -x["hyp"]),
        "heatmap": heatmap_rows,
        "readiness_dist": readiness_dist,
        "status_dist": status_dist,
        "utilization": utilization,
        "realized_by_period": realized_by_period,
    }
