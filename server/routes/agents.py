"""AI agents (Foundation Model API).

CHUNK: dependency auto-detection for newly-added use cases + the "unlocks"
computation that powers the flywheel highlight moment.

- POST /api/agents/detect-dependencies  -> proposes required data assets and
  downstream enabled use cases for a use case (LLM over the current graph),
  returned as accept/reject suggestions (nothing persisted here).
- GET  /api/use-cases/{id}/unlocks is provided in use_cases.py.
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import SERVING_ENDPOINT
from ..db import db

router = APIRouter(prefix="/agents", tags=["agents"])


class DetectIn(BaseModel):
    use_case_id: int
    max_assets: int = 6
    max_enables: int = 6


def _keyword_score(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(1 for kw in terms if kw and kw.lower() in t)


async def _heuristic_detect(uc: dict, assets: list, ucs: list, max_assets: int, max_enables: int):
    """Deterministic fallback (also the grounding shortlist for the LLM)."""
    text = f"{uc['title']} {uc.get('description') or ''} {' '.join(uc.get('risk_tags') or [])}"
    tl = text.lower()
    # score data assets by token overlap with module/category/description
    scored_assets = []
    for a in assets:
        terms = f"{a.get('source_category') or ''} {a['module']} {a.get('description') or ''}".lower().split()
        score = sum(1 for w in set(terms) if len(w) > 3 and w in tl)
        if score:
            scored_assets.append((score, a))
    scored_assets.sort(key=lambda x: -x[0])
    req = [{"data_asset_id": a["id"], "label": f"{a.get('source_category') or a['source_system']} · {a['module']}",
            "criticality": "required" if i < 2 else "helpful", "rationale": "Domain/keyword match to the use case."}
           for i, (_s, a) in enumerate(scored_assets[:max_assets])]

    # enabled UCs: same domain/sub-vertical, higher phase (built later), sharing tags
    enables = []
    for o in ucs:
        if o["id"] == uc["id"]:
            continue
        if o.get("lob_id") == uc.get("lob_id") and (o.get("phase") or 0) >= (uc.get("phase") or 0):
            overlap = _keyword_score(f"{o['title']} {o.get('description') or ''}", (uc.get("risk_tags") or []) + [uc["title"].split()[0]])
            if overlap or o.get("sub_vertical") == uc.get("sub_vertical"):
                enables.append((overlap, o))
    enables.sort(key=lambda x: -x[0])
    ena = [{"to_use_case_id": o["id"], "label": o["title"],
            "rationale": "Same domain and builds on the data this use case lands."}
           for _s, o in enables[:max_enables]]
    return req, ena


@router.post("/detect-dependencies")
async def detect_dependencies(body: DetectIn):
    uc = await db.fetchrow("SELECT * FROM use_cases WHERE id=$1", body.use_case_id)
    if uc is None:
        raise HTTPException(404, "Use case not found")
    uc = dict(uc)
    assets = [dict(a) for a in await db.fetch("SELECT * FROM data_assets ORDER BY id")]
    ucs = [dict(u) for u in await db.fetch("SELECT id, title, description, lob_id, phase, sub_vertical FROM use_cases")]

    req, ena = await _heuristic_detect(uc, assets, ucs, body.max_assets, body.max_enables)

    # Try to refine ranking/rationale with the Foundation Model; fall back silently.
    used_llm = False
    try:
        from ..llm import get_llm_client
        client = get_llm_client()
        shortlist_assets = [{"id": r["data_asset_id"], "label": r["label"]} for r in req]
        shortlist_ucs = [{"id": e["to_use_case_id"], "label": e["label"]} for e in ena]
        prompt = (
            "You are a Power & Utilities data/AI architect. Given a NEW use case and a shortlist of "
            "candidate data assets it may require and candidate downstream use cases it may enable, "
            "return STRICT JSON: {\"requires\":[{\"data_asset_id\":int,\"criticality\":\"required|helpful\",\"rationale\":str}],"
            "\"enables\":[{\"to_use_case_id\":int,\"rationale\":str}]}. Only use ids from the shortlists.\n\n"
            f"NEW USE CASE: {uc['title']} — {uc.get('description') or ''}\n"
            f"CANDIDATE ASSETS: {json.dumps(shortlist_assets)}\n"
            f"CANDIDATE DOWNSTREAM USE CASES: {json.dumps(shortlist_ucs)}\n"
        )
        resp = await client.chat.completions.create(
            model=SERVING_ENDPOINT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200, temperature=0.2,
        )
        content = resp.choices[0].message.content
        start, end = content.find("{"), content.rfind("}")
        parsed = json.loads(content[start:end + 1])
        asset_label = {r["data_asset_id"]: r["label"] for r in req}
        uc_label = {e["to_use_case_id"]: e["label"] for e in ena}
        if parsed.get("requires"):
            req = [{"data_asset_id": r["data_asset_id"], "label": asset_label.get(r["data_asset_id"], ""),
                    "criticality": r.get("criticality", "required"), "rationale": r.get("rationale", "")}
                   for r in parsed["requires"] if r.get("data_asset_id") in asset_label]
        if parsed.get("enables"):
            ena = [{"to_use_case_id": e["to_use_case_id"], "label": uc_label.get(e["to_use_case_id"], ""),
                    "rationale": e.get("rationale", "")}
                   for e in parsed["enables"] if e.get("to_use_case_id") in uc_label]
        used_llm = True
    except Exception as exc:  # noqa: BLE001
        print(f"[agents] LLM detect fell back to heuristic: {exc}")

    return {
        "use_case_id": body.use_case_id,
        "model": SERVING_ENDPOINT if used_llm else "heuristic",
        "used_llm": used_llm,
        "requires": req,
        "enables": ena,
    }


# ---------------------------------------------------------------------------
# Shared LLM JSON helper
# ---------------------------------------------------------------------------
async def _llm_json(prompt: str, max_tokens: int = 1600):
    """Call the Foundation Model and parse a JSON object from the reply.
    Returns (parsed|None, used_llm, note). `note` is a short reason when we fall
    back so the UI can show 'AI temporarily unavailable — showing heuristic'."""
    try:
        from ..llm import get_llm_client
        client = get_llm_client()
        resp = await client.chat.completions.create(
            model=SERVING_ENDPOINT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=0.2,
        )
        content = resp.choices[0].message.content
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start:end + 1]), True, None
    except Exception as exc:  # noqa: BLE001
        note = f"AI temporarily unavailable — showing heuristic ranking. ({type(exc).__name__})"
        print(f"[agents] LLM call fell back: {exc}")
        return None, False, note


async def _portfolio_context():
    from ..readiness import readiness_map
    from ..value_engine import compute_value_range, load_assumptions
    # Portfolio-scoped: roadmap + next-best recommender operate on the confirmed set.
    ucs = [dict(u) for u in await db.fetch(
        "SELECT * FROM use_cases WHERE in_portfolio = true ORDER BY id")]
    lobs = {l["id"]: l["name"] for l in await db.fetch("SELECT id, name FROM lobs")}
    rmap = await readiness_map()
    assumptions = await load_assumptions()
    for u in ucs:
        rd = rmap.get(u["id"]) or {}
        u["readiness"] = rd.get("readiness")
        u["pending_prereqs"] = rd.get("pending_prereqs") or []
        rng = compute_value_range(u.get("hypothesized_value_json"), assumptions)
        u["value_mm"] = rng["mid"] if rng else 0
        u["lob_name"] = lobs.get(u["lob_id"], "")
    return ucs, lobs


# ---------------------------------------------------------------------------
# 2) Next-best-use-case recommender
# ---------------------------------------------------------------------------
class RecommendIn(BaseModel):
    top_n: int = 6


@router.post("/recommend")
async def recommend(body: RecommendIn):
    ucs, _ = await _portfolio_context()
    live = [u for u in ucs if u["status"] in ("live", "value_realized")]
    candidates = [u for u in ucs if u["status"] in ("not_started", "scoping")]

    effort_w = {"S": 1, "M": 2, "L": 3, "XL": 4}
    # awaiting_prerequisites: data is ready but upstream UCs aren't built yet, so it
    # is a FAST-FOLLOW, not "do now" — weighted between shovel-ready and nearly-ready.
    ready_w = {"shovel_ready": 1.0, "awaiting_prerequisites": 0.7, "nearly_ready": 0.6, "blocked": 0.25}

    def score(u):
        r = ready_w.get(u.get("readiness"), 0.3)
        e = effort_w.get(u.get("effort_tshirt"), 2)
        return round((u["value_mm"] or 0) * r / e, 2)

    def track(u):
        return "do_now" if u.get("readiness") == "shovel_ready" else \
               "fast_follow" if u.get("readiness") == "awaiting_prerequisites" else "prep"

    ranked = sorted(candidates, key=score, reverse=True)[: max(body.top_n * 2, 12)]
    live_titles = [u["title"] for u in live][:12]
    cand_payload = [{"id": u["id"], "title": u["title"], "lob": u["lob_name"],
                     "readiness": u.get("readiness"), "value_mm": u["value_mm"],
                     "effort": u.get("effort_tshirt"), "opportunity_score": score(u),
                     "track": track(u),
                     "pending_prereqs": [p["title"] for p in u.get("pending_prereqs", [])]} for u in ranked]

    prompt = (
        "You are a Power & Utilities data/AI strategy advisor. The customer has these use cases LIVE: "
        f"{json.dumps(live_titles)}. From the candidate list below (already scored by value/effort/readiness), "
        f"pick the top {body.top_n} NEXT-BEST use cases. Each candidate has a 'track': 'do_now' means data AND "
        "prerequisites are ready — recommend building it now; 'fast_follow' means data is ready but the listed "
        "pending_prereqs use cases must be built first — frame it as 'unlocked after you build X'. For each, give a "
        "one-sentence rationale and (for fast_follow) a narrative naming the prerequisite(s). "
        "Return STRICT JSON: {\"recommendations\":[{\"id\":int,\"rationale\":str,\"narrative\":str}]}. "
        f"CANDIDATES: {json.dumps(cand_payload)}"
    )
    parsed, used_llm, note = await _llm_json(prompt)
    by_id = {u["id"]: u for u in ranked}
    recs = []

    def _pend(u):
        return [p["title"] for p in u.get("pending_prereqs", [])]

    def _fallback_narrative(u):
        pend = _pend(u)
        if u.get("readiness") == "awaiting_prerequisites" and pend:
            return f"Fast-follow — unlocked once you build: {', '.join(pend[:3])}."
        return ""

    if parsed and parsed.get("recommendations"):
        for r in parsed["recommendations"]:
            u = by_id.get(r.get("id"))
            if u:
                recs.append({**{k: u[k] for k in ("id", "title", "lob_name", "readiness", "value_mm", "effort_tshirt")},
                             "opportunity_score": score(u), "track": track(u),
                             "pending_prereqs": _pend(u),
                             "rationale": r.get("rationale", ""),
                             "narrative": r.get("narrative", "") or _fallback_narrative(u)})
    if not recs:  # heuristic fallback
        for u in ranked[: body.top_n]:
            recs.append({**{k: u[k] for k in ("id", "title", "lob_name", "readiness", "value_mm", "effort_tshirt")},
                         "opportunity_score": score(u), "track": track(u),
                         "pending_prereqs": _pend(u),
                         "rationale": f"High value (${u['value_mm']}M) at {u.get('readiness')} readiness and {u.get('effort_tshirt')} effort.",
                         "narrative": _fallback_narrative(u)})
    return {"model": SERVING_ENDPOINT if used_llm else "heuristic", "used_llm": used_llm,
            "fallback_note": note, "recommendations": recs[: body.top_n]}


# ---------------------------------------------------------------------------
# 2b) Catalog recommender — "ideas to consider" (the EXPAND motion)
# ---------------------------------------------------------------------------
@router.get("/recommend-catalog")
async def recommend_catalog(top_n: int = 8):
    """Rank CATALOG use cases NOT yet in the portfolio by opportunity, framed as
    'ideas to consider you may not be thinking about'. Distinct from the next-best
    recommender (which closes the loop on the customer's own confirmed set)."""
    from ..readiness import readiness_map
    from ..value_engine import compute_value_range, load_assumptions
    lobs = {l["id"]: l["name"] for l in await db.fetch("SELECT id, name FROM lobs")}
    rmap = await readiness_map()
    assumptions = await load_assumptions()
    rows = await db.fetch(
        "SELECT * FROM use_cases WHERE origin='catalog' AND in_portfolio=false ORDER BY id")

    effort_w = {"S": 1, "M": 2, "L": 3, "XL": 4}
    ready_w = {"shovel_ready": 1.0, "awaiting_prerequisites": 0.7, "nearly_ready": 0.6, "blocked": 0.25}
    out = []
    for r in rows:
        u = dict(r)
        rd = rmap.get(u["id"]) or {}
        readiness = rd.get("readiness")
        pending = [p["title"] for p in (rd.get("pending_prereqs") or [])]
        rng = compute_value_range(u.get("hypothesized_value_json"), assumptions)
        value_mm = rng["mid"] if rng else 0
        rw = ready_w.get(readiness, 0.3)
        ew = effort_w.get(u.get("effort_tshirt"), 2)
        score = round((value_mm or 0) * rw / ew, 2)
        lob = lobs.get(u["lob_id"], "Unassigned")
        track = "do_now" if readiness == "shovel_ready" else \
                "fast_follow" if readiness == "awaiting_prerequisites" else "prep"
        if track == "fast_follow" and pending:
            rationale = (f"~${value_mm:.0f}M/yr in {lob}; data is ready — a fast-follow "
                         f"unlocked once you build: {', '.join(pending[:2])}.")
        else:
            rationale = (f"~${value_mm:.0f}M/yr potential in {lob} at {readiness or 'unknown'} "
                         f"readiness ({u.get('effort_tshirt') or 'M'} effort) — an idea worth considering.")
        out.append({"id": u["id"], "title": u["title"], "lob_name": lob,
                    "sub_vertical": u.get("sub_vertical"), "phase": u.get("phase"),
                    "readiness": readiness, "value_mm": value_mm,
                    "effort_tshirt": u.get("effort_tshirt"), "track": track,
                    "pending_prereqs": pending,
                    "opportunity_score": score, "rationale": rationale})
    out.sort(key=lambda x: -x["opportunity_score"])
    return {"recommendations": out[:top_n], "total_candidates": len(out)}


# ---------------------------------------------------------------------------
# 3) Roadmap generator (dependency-respecting waves)
# ---------------------------------------------------------------------------
class RoadmapIn(BaseModel):
    persist: bool = False
    actor: str | None = None


def _topo_waves(ucs, enables):
    """Assign each UC a wave = 1 + max(wave of its prerequisites)."""
    upstream: dict[int, list[int]] = {}
    for e in enables:
        upstream.setdefault(e["to_use_case_id"], []).append(e["from_use_case_id"])
    wave: dict[int, int] = {}

    def compute(uid, stack):
        if uid in wave:
            return wave[uid]
        if uid in stack:  # cycle guard
            return 1
        stack.add(uid)
        preds = upstream.get(uid, [])
        w = 1 if not preds else 1 + max((compute(p, stack) for p in preds), default=0)
        stack.discard(uid)
        wave[uid] = w
        return w

    ids = {u["id"] for u in ucs}
    for u in ucs:
        compute(u["id"], set())
    # clamp waves so they map onto now/next/later (1..3+)
    return {k: v for k, v in wave.items() if k in ids}


@router.post("/roadmap")
async def roadmap(body: RoadmapIn):
    ucs, _ = await _portfolio_context()
    enables = [dict(e) for e in await db.fetch("SELECT from_use_case_id, to_use_case_id FROM uc_enables_uc")]
    waves = _topo_waves(ucs, enables)

    # order within-wave by opportunity (value x readiness)
    ready_w = {"shovel_ready": 1.0, "nearly_ready": 0.6, "blocked": 0.25}
    horizon_of = lambda w: "now" if w <= 1 else "next" if w == 2 else "later"  # noqa: E731

    items = []
    for u in ucs:
        if u["status"] in ("live", "value_realized"):
            continue  # already delivered
        w = waves.get(u["id"], 1)
        items.append({
            "use_case_id": u["id"], "title": u["title"], "lob_name": u["lob_name"],
            "wave": w, "horizon": horizon_of(w), "value_mm": u["value_mm"],
            "readiness": u.get("readiness"),
            "opportunity": round((u["value_mm"] or 0) * ready_w.get(u.get("readiness"), 0.3), 2),
        })
    items.sort(key=lambda x: (x["wave"], -x["opportunity"]))

    persisted = 0
    if body.persist:
        actor = body.actor or "agent"
        # replace agent-generated roadmap rows for these UCs
        for it in items:
            await db.execute(
                """INSERT INTO roadmap_items (use_case_id, horizon, wave, notes)
                   VALUES ($1,$2,$3,$4)""",
                it["use_case_id"], it["horizon"], it["wave"],
                f"Auto-sequenced by roadmap agent (opportunity {it['opportunity']}).")
            persisted += 1
        try:
            await db.execute(
                "INSERT INTO audit_log (entity_type, entity_id, action, actor, diff_json) "
                "VALUES ('roadmap', NULL, 'generate', $1, $2::jsonb)",
                actor, json.dumps({"items": len(items)}))
        except Exception:  # noqa: BLE001
            pass

    horizons = {"now": [], "next": [], "later": []}
    for it in items:
        horizons[it["horizon"]].append(it)
    return {"horizons": horizons, "persisted": persisted,
            "waves": max((i["wave"] for i in items), default=0)}


# ---------------------------------------------------------------------------
# 4) Value estimator (suggest value-model components from benchmarks)
# ---------------------------------------------------------------------------
class EstimateIn(BaseModel):
    use_case_id: int | None = None
    title: str | None = None
    description: str | None = None


@router.post("/estimate-value")
async def estimate_value(body: EstimateIn):
    title, desc = body.title, body.description
    if body.use_case_id is not None:
        uc = await db.fetchrow("SELECT title, description FROM use_cases WHERE id=$1", body.use_case_id)
        if uc:
            title, desc = uc["title"], uc["description"]
    if not title:
        raise HTTPException(422, "Provide use_case_id or title")

    assumptions = [dict(a) for a in await db.fetch("SELECT key, label, unit FROM value_assumptions ORDER BY category")]
    benchmarks = [dict(b) for b in await db.fetch("SELECT * FROM benchmark_library")]
    keys = [a["key"] for a in assumptions]

    prompt = (
        "You are a Power & Utilities value-engineering advisor. Propose a value model for this use case as a set of "
        "components. Each component = {name, calculationDisplay, multiplier (a small float that folds in the % / unit "
        "conversion), assumptionKeys (subset of the allowed keys), lowCoeff, highCoeff}. Annual value in $M = sum over "
        "components of multiplier * product(assumption values) * coeff. Keep it realistic and conservative. "
        "Return STRICT JSON: {\"components\":[...], \"roiMonths\":int, \"notes\":str}. "
        f"USE CASE: {title} — {desc or ''}\n"
        f"ALLOWED ASSUMPTION KEYS: {json.dumps(keys)}\n"
        f"BENCHMARKS (reference ranges): {json.dumps(benchmarks[:15], default=str)}"
    )
    parsed, used_llm, note = await _llm_json(prompt, max_tokens=1400)
    # validate components reference only allowed keys
    comps = []
    if parsed and parsed.get("components"):
        keyset = set(keys)
        for c in parsed["components"]:
            aks = [k for k in (c.get("assumptionKeys") or []) if k in keyset]
            if not aks:
                continue
            comps.append({
                "name": c.get("name", "Value component"),
                "calculationDisplay": c.get("calculationDisplay", ""),
                "multiplier": float(c.get("multiplier", 0) or 0),
                "assumptionKeys": aks,
                "lowCoeff": float(c.get("lowCoeff", 0.7) or 0.7),
                "highCoeff": float(c.get("highCoeff", 1.3) or 1.3),
            })
    if not comps:
        comps = [{"name": "O&M efficiency", "calculationDisplay": "O&M budget x 0.3%",
                  "multiplier": 0.003, "assumptionKeys": ["omBudgetMM"], "lowCoeff": 0.6, "highCoeff": 1.4}]
    return {"model": SERVING_ENDPOINT if used_llm else "heuristic", "used_llm": used_llm,
            "fallback_note": note,
            "value_model": {"driver": title, "components": comps,
                            "roiMonths": (parsed or {}).get("roiMonths", 12),
                            "notes": (parsed or {}).get("notes", "")}}


# ---------------------------------------------------------------------------
# 5) Source decomposition (category/vendor -> modules)
# ---------------------------------------------------------------------------
class DecomposeIn(BaseModel):
    source_category: str
    vendor: str | None = None


@router.post("/decompose-source")
async def decompose_source(body: DecomposeIn):
    prompt = (
        "You are a Power & Utilities data architect. For the given source system category (and optional vendor), "
        "propose the specific modules / subsystems / data streams a utility would decompose it into for a data "
        "catalog. Use VENDOR-NEUTRAL module names. Return STRICT JSON: "
        "{\"modules\":[{\"module\":str,\"description\":str,\"sub_vertical\":\"fossil|hydro|renewables|nuclear|cross\"}]}. "
        f"SOURCE CATEGORY: {body.source_category}\nVENDOR (optional context): {body.vendor or 'unspecified'}"
    )
    parsed, used_llm, note = await _llm_json(prompt, max_tokens=1200)
    mods = []
    if parsed and parsed.get("modules"):
        for m in parsed["modules"]:
            if m.get("module"):
                mods.append({"module": m["module"], "description": m.get("description", ""),
                             "sub_vertical": m.get("sub_vertical", "cross")})
    if not mods:
        mods = [{"module": f"{body.source_category} — Core Feed", "description": "Primary data stream.", "sub_vertical": "cross"}]
    return {"model": SERVING_ENDPOINT if used_llm else "heuristic", "used_llm": used_llm,
            "fallback_note": note,
            "source_category": body.source_category, "vendor": body.vendor, "modules": mods}
