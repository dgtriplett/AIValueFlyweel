"""Joint-funding business-case builder.

Turns "assets benefiting >=2 LOBs" into ranked, funded opportunities with a real
business case: what a single data asset unlocks (by LOB), readiness delta if it
goes not_started->governed, combined + per-LOB value (via the value engine),
ingestion cost/ROI, a fair value-proportional cost-share split, and an
LLM-generated one-page funding brief.
"""
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts, portfolio
from ..common import current_user, write_audit
from ..db import db
from ..value_engine import load_assumptions, EFFORT_COST, asset_cost, use_case_value
# Import the readiness rule rather than restating it: a local copy would let
# this module silently disagree with how readiness is actually computed.
from ..readiness import BUILT_SQL_LIST, READY_STATUSES as READY

router = APIRouter(prefix="/joint-funding", tags=["joint_funding"])



async def _context():
    assumptions = await load_assumptions()
    assets = [dict(a) for a in await db.fetch("SELECT * FROM data_assets ORDER BY id")]
    # Joint-funding value totals are portfolio-scoped (confirmed use cases only).
    condition, params = await portfolio.portfolio_condition("uc")
    ucs = {u["id"]: dict(u) for u in await db.fetch(
        f"SELECT uc.* FROM use_cases uc WHERE {condition}", *params)}
    lobs = {lob["id"]: lob["name"] for lob in await db.fetch("SELECT id, name FROM lobs")}
    requires = await db.fetch("SELECT use_case_id, data_asset_id, criticality FROM uc_requires_asset")
    # asset -> list of (uc_id, criticality)
    by_asset: dict[int, list] = {}
    for r in requires:
        by_asset.setdefault(r["data_asset_id"], []).append((r["use_case_id"], r["criticality"]))
    # uc -> its required assets + statuses (for readiness delta)
    req_by_uc: dict[int, list] = {}
    for r in requires:
        if r["criticality"] == "required":
            req_by_uc.setdefault(r["use_case_id"], []).append(r["data_asset_id"])
    # uc -> whether all its DIRECT prerequisite use cases are built (live/value_realized).
    # A UC only becomes truly shovel-ready when data AND prereqs are in place.
    prereq_rows = await db.fetch(
        f"""SELECT e.to_use_case_id AS uc_id,
                  bool_and(up.status IN ({BUILT_SQL_LIST})) AS all_built
           FROM uc_enables_uc e JOIN use_cases up ON up.id = e.from_use_case_id
           GROUP BY e.to_use_case_id""")
    prereqs_built_by_uc = {r["uc_id"]: bool(r["all_built"]) for r in prereq_rows}
    return assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built_by_uc




def _asset_status_map(assets):
    return {a["id"]: a["ingestion_status"] for a in assets}


def _build_case(asset, assumptions, ucs, lobs, by_asset, req_by_uc, status_map,
                prereqs_built_by_uc=None):
    """Compute the full business case for landing ONE asset."""
    prereqs_built_by_uc = prereqs_built_by_uc or {}
    uc_entries = by_asset.get(asset["id"], [])
    unlocked = []
    per_lob_value: dict[int, float] = {}
    becomes_ready = 0          # flips to TRUE shovel-ready (data + prereqs)
    awaiting_prereqs = 0       # data completes but upstream UCs still unbuilt
    delivery_cost_mid = 0.0
    for (uc_id, crit) in uc_entries:
        uc = ucs.get(uc_id)
        if not uc:
            continue
        full_v = use_case_value(uc, assumptions)
        # Attribute UC value PROPORTIONALLY across the (required) assets it needs, so
        # a UC needing 3 assets contributes ~1/3 of its value to each asset's case —
        # no double-counting when the same UC appears under several shared assets.
        n_req = max(1, len(req_by_uc.get(uc_id, [])) or 1)
        attributed_v = round(full_v / n_req, 3)
        lob = uc.get("lob_id")
        reqs = req_by_uc.get(uc_id, [])
        now_ready = all(status_map.get(a) in READY for a in reqs) if reqs else True
        hypo_ready = all((a == asset["id"]) or (status_map.get(a) in READY) for a in reqs) if reqs else True
        prereqs_ok = prereqs_built_by_uc.get(uc_id, True)  # no prereqs => True
        # Landing this asset flips the UC to TRUE shovel-ready only if its data
        # completes AND its prerequisite use cases are already built. If data
        # completes but prereqs aren't built, it becomes awaiting_prerequisites.
        data_completes = hypo_ready and not now_ready
        flips = data_completes and prereqs_ok
        awaits = data_completes and not prereqs_ok
        if flips:
            becomes_ready += 1
        if awaits:
            awaiting_prereqs += 1
        # FULL delivery cost to actually build & realize this UC (effort -> $).
        # Not divided: you must deliver the WHOLE use case to capture its value,
        # so the program to realize this asset's enablement carries every UC's
        # full build cost (this is what makes payback/ROI credible to a CFO).
        d_lo, d_hi = EFFORT_COST.get(uc.get("effort_tshirt") or "M", EFFORT_COST["M"])
        delivery_cost_mid += (d_lo + d_hi) / 2
        unlocked.append({"id": uc_id, "title": uc["title"], "lob_id": lob,
                         "lob": lobs.get(lob, "Unassigned"),
                         "value_mm": attributed_v, "full_value_mm": full_v,
                         "criticality": crit, "becomes_ready": flips,
                         "awaiting_prereqs": awaits})
        if lob is not None:
            per_lob_value[lob] = round(per_lob_value.get(lob, 0) + attributed_v, 3)

    # "annual value unlocked/enabled" = attributed (non-double-counted) sum at FULL run-rate
    combined = round(sum(u["value_mm"] for u in unlocked), 2)
    benefiting = sorted(per_lob_value.keys())
    ing_lo, ing_hi = asset_cost(asset)
    ing_mid = (ing_lo + ing_hi) / 2
    # One-time BUILD cost = data ingestion + FULL delivery cost of every unlocked UC.
    build_cost = round(ing_mid + delivery_cost_mid, 0)
    # Ongoing ANNUAL run/opex — platform, licensing, model ops, support & the
    # data-quality/change effort to keep the value flowing (~30% of build/yr).
    RUN_RATE = 0.30
    annual_run = round(build_cost * RUN_RATE, 0)
    program_cost_mid = build_cost  # kept for back-compat (build) in the response
    combined_abs = combined * 1_000_000
    # Value ramps over a multi-year adoption curve — a portfolio of N use cases is
    # not realized in year 1. Use a conservative Year-1 realization factor for
    # payback, and 3-year cumulative capture for ROI (yr1 35%, yr2 70%, yr3 100%).
    YR1_FACTOR = 0.35
    yr1_net = combined_abs * YR1_FACTOR - annual_run
    payback_months = round(build_cost / (yr1_net / 12), 1) if yr1_net > 0 else None
    if payback_months is not None and payback_months < 1:
        payback_months = None if build_cost <= 0 else max(payback_months, round(build_cost / (combined_abs / 12), 1))
    cum_3yr_value = combined_abs * (YR1_FACTOR + 0.70 + 1.0)  # ramp yr1/yr2/yr3
    tco_3yr = build_cost + 3 * annual_run
    roi = round((cum_3yr_value - tco_3yr) / tco_3yr * 100, 0) if tco_3yr else None

    # cost-share split of the INGESTION cost (the shared line item), proportional to value
    total_v = sum(per_lob_value.values()) or 1
    split = {str(lid): round(ing_mid * (per_lob_value[lid] / total_v), 0) for lid in benefiting}

    unlocked.sort(key=lambda x: -x["value_mm"])
    impact = round(combined * max(1, len(benefiting)) * (1 + becomes_ready * 0.15), 2)
    return {
        "asset": {"id": asset["id"], "source_category": asset.get("source_category"),
                  "module": asset["module"], "vendor": asset.get("vendor"),
                  "ingestion_status": asset["ingestion_status"],
                  "ingest_effort": asset.get("ingest_effort")},
        "benefiting_lobs": [{"id": lid, "name": lobs.get(lid, ""), "value_mm": per_lob_value[lid]} for lid in benefiting],
        "lob_count": len(benefiting),
        "unlocked": unlocked,
        "uc_count": len(unlocked),
        "becomes_shovel_ready": becomes_ready,
        "awaiting_prerequisites": awaiting_prereqs,          # data completes but prereqs unbuilt
        "combined_value_mm": combined,                       # attributed annual value unlocked
        "ingest_cost_low": ing_lo, "ingest_cost_high": ing_hi, "ingest_cost_mid": ing_mid,
        "delivery_cost_mid": round(delivery_cost_mid, 0),
        "build_cost": build_cost,                            # one-time build (ingestion + delivery)
        "annual_run": annual_run,                            # ongoing run/opex per year
        "program_cost_mid": program_cost_mid,               # == build_cost (back-compat)
        # keep cost_low/high/mid as the INGESTION cost (the shared, co-funded line)
        "cost_low": ing_lo, "cost_high": ing_hi, "cost_mid": ing_mid,
        "roi_pct": roi, "roi_basis": "3-year TCO", "payback_months": payback_months,
        "cost_share": split,
        "impact_score": impact,
    }


@router.get("/opportunities")
async def opportunities():
    """Ranked joint-funding opportunities (assets serving >=2 LOBs)."""
    assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built = await _context()
    status_map = _asset_status_map(assets)
    out = []
    for a in assets:
        case = _build_case(a, assumptions, ucs, lobs, by_asset, req_by_uc, status_map, prereqs_built)
        if case["lob_count"] >= 2 and case["uc_count"] >= 1:
            # one-line pitch — enablement framing (attributed annual value)
            top_lobs = ", ".join(lob["name"] for lob in case["benefiting_lobs"][:3])
            case["pitch"] = (f"Landing {a.get('source_category')} · {a['module']} enables "
                             f"{case['uc_count']} use cases (~${case['combined_value_mm']:.0f}M/yr attributed value) across "
                             f"{case['lob_count']} LOBs — {top_lobs}"
                             + (f"; {case['becomes_shovel_ready']} become shovel-ready." if case['becomes_shovel_ready'] else ".")
                             + (f" {case['awaiting_prerequisites']} more are data-ready but await prerequisite builds." if case['awaiting_prerequisites'] else ""))
            out.append(case)
    out.sort(key=lambda x: -x["impact_score"])
    return {"opportunities": out}


@router.get("/opportunity/{asset_id}")
async def opportunity(asset_id: int):
    assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built = await _context()
    asset = next((a for a in assets if a["id"] == asset_id), None)
    if not asset:
        raise HTTPException(404, "Data asset not found")
    return _build_case(asset, assumptions, ucs, lobs, by_asset, req_by_uc, _asset_status_map(assets), prereqs_built)


class BriefIn(BaseModel):
    asset_id: int


@router.post("/brief")
async def brief(body: BriefIn):
    """LLM-generated one-page funding brief (markdown) for the opportunity."""
    assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built = await _context()
    asset = next((a for a in assets if a["id"] == body.asset_id), None)
    if not asset:
        raise HTTPException(404, "Data asset not found")
    case = _build_case(asset, assumptions, ucs, lobs, by_asset, req_by_uc, _asset_status_map(assets), prereqs_built)

    lob_lines = "; ".join(
        f"{lob['name']}: ${lob['value_mm']:.1f}M/yr "
        f"(share ${case['cost_share'].get(str(lob['id']), 0):,.0f})"
        for lob in case["benefiting_lobs"])
    top_ucs = "; ".join(f"{u['title']} (${u['value_mm']:.1f}M{', becomes shovel-ready' if u['becomes_ready'] else ''})"
                        for u in case["unlocked"][:8])
    facts = (
        f"Data asset: {asset.get('source_category')} · {asset['module']}"
        f"{(' (' + asset['vendor'] + ')') if asset.get('vendor') else ''}, currently {asset['ingestion_status']}.\n"
        f"Beneficiary LOBs & value: {lob_lines}.\n"
        f"Use cases enabled ({case['uc_count']}): {top_ucs}.\n"
        f"Annual value unlocked (attributed, no double-count): ${case['combined_value_mm']:.0f}M/yr. "
        f"Shared data-ingestion cost (the co-funded line item): "
        f"${case['ingest_cost_low']:,.0f}–${case['ingest_cost_high']:,.0f}. "
        f"Estimated use-case delivery cost to realize the value: ~${case['delivery_cost_mid']:,.0f}. "
        f"One-time build cost: ~${case['build_cost']:,.0f}; ongoing run/opex ~${case['annual_run']:,.0f}/yr. "
        f"3-year ROI: {case['roi_pct']}% · payback {case['payback_months']} months "
        f"(payback nets out ongoing run cost; ROI on 3-year total cost of ownership). "
        f"{case['becomes_shovel_ready']} use cases become shovel-ready. "
        f"Note: value is 'value enabled' — attributed proportionally across each use case's required data, "
        f"not a promise that ingestion alone delivers it."
    )
    prompt = (
        "You are a Power & Utilities data strategy advisor writing a ONE-PAGE joint-funding "
        "business case a champion can circulate to LOB leaders. Use the facts below. Structure as markdown: "
        "a punchy title, a 2-sentence executive summary, 'What it unlocks' (by LOB), 'The numbers' "
        "(combined + per-LOB value, cost, ROI/payback), 'Proposed cost-share' (the split, framed as fair "
        "because proportional to value received), and 'Why now' (tie to fast-follows / flywheel momentum). "
        "Be concrete and concise. FACTS:\n" + facts
    )
    used_llm = False
    brief_md = None
    # Routed through the shared helper so it inherits optional-parameter
    # negotiation, content-block flattening, and the reasoning-model token floor.
    # This call site predated those fixes and silently fell back on sonnet-5.
    from ..config import SERVING_ENDPOINT
    from .agents import llm_text

    brief_md, used_llm, _note = await llm_text(prompt, max_tokens=1400)
    if not brief_md:
        brief_md = (f"# Joint-Funding Business Case — {asset.get('source_category')} · {asset['module']}\n\n"
                    f"{case['pitch'] if 'pitch' in case else facts}\n\n## The numbers\n{facts}\n")
    return {"model": SERVING_ENDPOINT if used_llm else "heuristic", "used_llm": used_llm,
            "brief_md": brief_md, "case": case}


class FundIn(BaseModel):
    data_asset_id: int
    requesting_lob_id: int | None = None
    co_funding_lobs: list[int] = []
    combined_value: float | None = None
    sponsor: str | None = None
    cost_share: dict | None = None
    brief_md: str | None = None
    status: str = "proposed"


@router.post("/request")
async def create_request(body: FundIn, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            """INSERT INTO funding_requests
               (account_id, data_asset_id, requesting_lob_id, co_funding_lobs,
                combined_value, status, sponsor, cost_share_json, brief_md)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9) RETURNING *""",
            account_id, body.data_asset_id, body.requesting_lob_id,
            body.co_funding_lobs, body.combined_value, body.status,
            body.sponsor or actor,
            json.dumps(body.cost_share) if body.cost_share else None, body.brief_md)
    else:
        row = await db.fetchrow(
            """INSERT INTO funding_requests
               (data_asset_id, requesting_lob_id, co_funding_lobs, combined_value, status,
                sponsor, cost_share_json, brief_md)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8) RETURNING *""",
            body.data_asset_id, body.requesting_lob_id, body.co_funding_lobs, body.combined_value,
            body.status, body.sponsor or actor,
            json.dumps(body.cost_share) if body.cost_share else None, body.brief_md)
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("funding_request", row["id"], "create", actor, {"data_asset_id": body.data_asset_id})
    return dict(row)


class StatusIn(BaseModel):
    status: str
    sponsor: str | None = None


@router.put("/request/{req_id}")
async def update_request(req_id: int, body: StatusIn, request: Request):
    if body.status not in ("proposed", "committed", "funded", "declined"):
        raise HTTPException(422, "invalid status")
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            """UPDATE funding_requests SET status=$1, sponsor=COALESCE($2,sponsor)
               WHERE id=$3 AND account_id=$4 RETURNING *""",
            body.status, body.sponsor, req_id, account_id)
    else:
        row = await db.fetchrow(
            "UPDATE funding_requests SET status=$1, sponsor=COALESCE($2,sponsor) WHERE id=$3 RETURNING *",
            body.status, body.sponsor, req_id)
    if row is None:
        raise HTTPException(404, "Funding request not found")
    await write_audit("funding_request", req_id, "status", actor, {"status": body.status})
    return dict(row)
