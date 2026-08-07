"""AI "Land these next" data-source recommender.

The inverse of joint-funding / next-best: instead of asking which authored UC to
build next, this ranks the data sources NOT yet landed (ingestion_status not in
curated/governed) by the IMMEDIATE value of landing them:

    immediate value = (# use cases that flip to shovel-ready if this source lands)
                       + (combined hypothesized value unlocked by those flips)

For each source we surface which UCs it unblocks, the value freed, an ingestion
effort/cost estimate, and a short AI rationale ("Land these next").
"""
from fastapi import APIRouter

from ..db import db
from ..value_engine import compute_value_range, load_assumptions, EFFORT_COST, asset_cost, use_case_value
# Import the readiness rule rather than restating it: a local copy would let
# this module silently disagree with how readiness is actually computed.
from ..readiness import READY_STATUSES as READY

router = APIRouter(prefix="/data-sources", tags=["source_recommendations"])





async def _context():
    assumptions = await load_assumptions()
    assets = [dict(a) for a in await db.fetch("SELECT * FROM data_assets ORDER BY id")]
    ucs = {u["id"]: dict(u) for u in await db.fetch("SELECT * FROM use_cases")}
    lobs = {l["id"]: l["name"] for l in await db.fetch("SELECT id, name FROM lobs")}
    requires = await db.fetch(
        "SELECT use_case_id, data_asset_id, criticality FROM uc_requires_asset")
    # asset -> [uc_id, ...] (any criticality)
    by_asset: dict[int, list] = {}
    # uc -> [required asset ids] (criticality='required' drives readiness)
    req_by_uc: dict[int, list] = {}
    for r in requires:
        by_asset.setdefault(r["data_asset_id"], []).append(r["use_case_id"])
        if r["criticality"] == "required":
            req_by_uc.setdefault(r["use_case_id"], []).append(r["data_asset_id"])
    # uc -> whether all its direct prerequisite use cases are built (live/value_realized)
    prereq_rows = await db.fetch(
        """SELECT e.to_use_case_id AS uc_id,
                  bool_and(up.status IN ('live','value_realized')) AS all_built
           FROM uc_enables_uc e JOIN use_cases up ON up.id = e.from_use_case_id
           GROUP BY e.to_use_case_id""")
    prereqs_built_by_uc = {r["uc_id"]: bool(r["all_built"]) for r in prereq_rows}
    return assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built_by_uc


def _score_source(asset, assumptions, ucs, lobs, by_asset, req_by_uc, status_map,
                  prereqs_built_by_uc=None):
    """Value of landing ONE not-yet-ready source (promote it to governed).

    A UC flips to TRUE shovel-ready only if its data completes AND its prerequisite
    use cases are already built. If data completes but prereqs aren't built, it
    becomes 'awaiting prerequisites' — surfaced separately, NOT counted as unlocked.
    """
    prereqs_built_by_uc = prereqs_built_by_uc or {}
    unlocks = []             # UCs that flip to TRUE shovel-ready because of THIS source
    awaiting = []            # data completes but prereq UCs still unbuilt
    partial = []             # UCs helped but data still incomplete afterwards
    value_unlocked = 0.0     # combined hyp value of the truly-flipped UCs
    lob_set = set()
    for uc_id in set(by_asset.get(asset["id"], [])):
        uc = ucs.get(uc_id)
        if not uc:
            continue
        reqs = req_by_uc.get(uc_id, [])
        if asset["id"] not in reqs:
            continue  # only a *required* asset can move readiness
        now_ready = all(status_map.get(a) in READY for a in reqs) if reqs else True
        if now_ready:
            continue  # data already complete; landing this source doesn't change data status
        hypo_ready = all((a == asset["id"]) or (status_map.get(a) in READY) for a in reqs)
        prereqs_ok = prereqs_built_by_uc.get(uc_id, True)
        v = use_case_value(uc, assumptions)
        entry = {"id": uc_id, "title": uc["title"], "lob_id": uc.get("lob_id"),
                 "lob": lobs.get(uc.get("lob_id"), "Unassigned"), "value_mm": round(v, 2)}
        if hypo_ready and prereqs_ok:
            unlocks.append(entry)
            value_unlocked += v
            if uc.get("lob_id") is not None:
                lob_set.add(uc["lob_id"])
        elif hypo_ready and not prereqs_ok:
            # data would be complete, but upstream use cases must be built first
            entry["pending_prereqs"] = len(
                [p for p in (prereqs_built_by_uc.get(uc_id, True),) if p is False])
            awaiting.append(entry)
        else:
            # still data-incomplete afterward, but this source is one asset it needs
            missing = [a for a in reqs if a != asset["id"] and status_map.get(a) not in READY]
            entry["still_needs"] = len(missing)
            partial.append(entry)

    value_unlocked = round(value_unlocked, 2)
    unlocks.sort(key=lambda x: -x["value_mm"])
    awaiting.sort(key=lambda x: -x["value_mm"])
    partial.sort(key=lambda x: (x["still_needs"], -x["value_mm"]))
    ing_lo, ing_hi = asset_cost(asset)
    ing_mid = (ing_lo + ing_hi) / 2
    # Immediate-value score prioritizes TRUE shovel-ready flips (data + prereqs);
    # awaiting-prereqs and still-partial UCs add smaller "sets up" credit so the
    # recommender favors data whose landing genuinely unlocks a use case now.
    score = round(len(unlocks) * 1.0 + value_unlocked + len(awaiting) * 0.15 + len(partial) * 0.1, 3)
    return {
        "asset": {"id": asset["id"], "source_category": asset.get("source_category"),
                  "source_system": asset.get("source_system"), "module": asset["module"],
                  "vendor": asset.get("vendor"),
                  "ingestion_status": asset["ingestion_status"],
                  "ingest_effort": asset.get("ingest_effort")},
        "flips_to_ready": len(unlocks),
        "value_unlocked_mm": value_unlocked,
        "unlocks": unlocks,
        "awaiting_prereqs": awaiting[:8],
        "awaiting_prereqs_count": len(awaiting),
        "sets_up": partial[:8],
        "sets_up_count": len(partial),
        "lob_count": len(lob_set),
        "ingest_cost_low": ing_lo, "ingest_cost_high": ing_hi, "ingest_cost_mid": ing_mid,
        "score": score,
    }


def _rationale(case):
    a = case["asset"]
    label = f"{a.get('source_category') or a.get('source_system') or ''} · {a['module']}".strip(" ·")
    flips = case["flips_to_ready"]
    val = case["value_unlocked_mm"]
    lobs = case["lob_count"]
    awaiting = case.get("awaiting_prereqs_count", 0)
    if flips:
        r = (f"Landing {label} makes {flips} use case{'s' if flips != 1 else ''} shovel-ready, "
             f"unlocking ~${val:.0f}M/yr of hypothesized value")
        r += f" across {lobs} line{'s' if lobs != 1 else ''} of business." if lobs else "."
        if awaiting:
            r += f" {awaiting} more become data-ready but still await prerequisite builds."
    elif awaiting:
        r = (f"Landing {label} completes the data for {awaiting} use "
             f"case{'s' if awaiting != 1 else ''}, but they still await prerequisite use-case builds "
             "before going shovel-ready.")
    elif case["sets_up_count"]:
        r = (f"Landing {label} clears a prerequisite for {case['sets_up_count']} use "
             f"case{'s' if case['sets_up_count'] != 1 else ''} that still need other data before they go shovel-ready.")
    else:
        r = f"Landing {label} has no immediate readiness impact on the current portfolio."
    return r


@router.get("/recommendations")
async def recommendations(limit: int = 12):
    """Rank NOT-yet-landed data sources by the immediate value of landing them."""
    assumptions, assets, ucs, lobs, by_asset, req_by_uc, prereqs_built = await _context()
    status_map = {a["id"]: a["ingestion_status"] for a in assets}
    out = []
    for a in assets:
        if a["ingestion_status"] in READY:
            continue  # already ready -> nothing to "land next"
        case = _score_source(a, assumptions, ucs, lobs, by_asset, req_by_uc, status_map, prereqs_built)
        # only surface sources that actually move the needle
        if case["flips_to_ready"] == 0 and case["awaiting_prereqs_count"] == 0 and case["sets_up_count"] == 0:
            continue
        case["rationale"] = _rationale(case)
        out.append(case)
    out.sort(key=lambda x: (-x["flips_to_ready"], -x["value_unlocked_mm"], -x["score"]))
    total_flip_value = round(sum(c["value_unlocked_mm"] for c in out), 2)
    total_flips = sum(c["flips_to_ready"] for c in out)
    return {
        "recommendations": out[:limit],
        "total": len(out),
        "summary": {"total_flips_available": total_flips,
                    "total_value_unlockable_mm": total_flip_value},
    }
