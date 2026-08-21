"""AI agents (Foundation Model API).

CHUNK: dependency auto-detection for newly-added use cases + the "unlocks"
computation that powers the flywheel highlight moment.

- POST /api/agents/detect-dependencies  -> proposes required data assets and
  downstream enabled use cases for a use case (LLM over the current graph),
  returned as accept/reject suggestions (nothing persisted here).
- GET  /api/use-cases/{id}/unlocks is provided in use_cases.py.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import accounts, portfolio
from ..common import row_to_dict, rows_to_list
from ..config import SERVING_ENDPOINT
from ..db import db
from ..limits import limiter
from ..readiness import readiness_map
from ..value_engine import compute_value_range, load_assumptions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])

# Floor for max_tokens on every LLM call. Reasoning models emit a private
# reasoning block before the answer, and a budget sized only for the answer gets
# consumed by the reasoning — the reply then arrives finish_reason=length with
# nothing usable in it. 6000 leaves room for both on the prompts here.
MIN_OUTPUT_TOKENS = 6000


def flatten_content(content) -> str:
    """Flatten a chat-completion `content` field into answer text.

    Newer endpoints return a LIST of typed blocks rather than a string, and
    reasoning models include a `reasoning` block that is NOT the answer — including
    it makes a JSON extractor parse the model's thinking. Shared with routes/chat.py
    so both paths handle the same shapes identically.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in ("reasoning", "thinking"):
                    continue
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                if getattr(block, "type", None) in ("reasoning", "thinking"):
                    continue
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


class DetectIn(BaseModel):
    use_case_id: int
    max_assets: int = 6
    max_enables: int = 6


CUSTOMER_ENHANCEMENT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "customer_enhancement_agent",
        "schema": {
            "type": "object",
            "properties": {
                "assumption_refinements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "recommended_value": {"type": ["number", "null"]},
                            "confidence": {"type": "string"},
                            "basis": {"type": "string"},
                            "rationale": {"type": "string"},
                            "review_priority": {"type": "string"},
                        },
                        "required": ["key", "confidence", "basis", "rationale"],
                    },
                },
                "app_enhancements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "why": {"type": "string"},
                            "it_delivers": {"type": "string"},
                            "implementation_hint": {"type": "string"},
                        },
                        "required": ["title", "why", "it_delivers",
                                     "implementation_hint"],
                    },
                },
            },
            "required": ["assumption_refinements", "app_enhancements"],
        },
        "strict": False,
    },
}


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


@router.post("/detect-dependencies", dependencies=[Depends(limiter("research"))])
async def detect_dependencies(body: DetectIn):
    uc = await db.fetchrow("SELECT * FROM use_cases WHERE id=$1", body.use_case_id)
    if uc is None:
        raise HTTPException(404, "Use case not found")
    uc = dict(uc)
    assets = [dict(a) for a in await db.fetch("SELECT * FROM data_assets ORDER BY id")]
    ucs = [dict(u) for u in await db.fetch("SELECT id, title, description, lob_id, phase, sub_vertical FROM use_cases")]

    req, ena = await _heuristic_detect(uc, assets, ucs, body.max_assets, body.max_enables)

    # Refine ranking/rationale with the Foundation Model; fall back silently.
    # Routed through _llm_json rather than calling the client directly, so this
    # inherits the optional-parameter negotiation and content-block handling —
    # a second hand-rolled call site is exactly how the sonnet-5 `temperature`
    # rejection kept breaking this endpoint after the shared helper was fixed.
    used_llm = False
    try:
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
        parsed, used_llm, _note = await _llm_json(prompt, max_tokens=1200)
        if not parsed:
            raise ValueError("no parseable response")
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
    except Exception as exc:  # noqa: BLE001
        logger.info("dependency detection fell back to heuristic (%s: %s)",
                    type(exc).__name__, exc)

    return {
        "use_case_id": body.use_case_id,
        "model": SERVING_ENDPOINT if used_llm else "heuristic",
        "used_llm": used_llm,
        "requires": req,
        "enables": ena,
    }


def _normalise_customer_agent(parsed: dict | None, assumptions: list[dict],
                              profile: dict | None) -> dict:
    """Validate model output and fill gaps with deterministic recommendations."""
    known = {row["key"]: row for row in assumptions}
    profile_name = (profile or {}).get("company_name") or "this customer"

    refinements: list[dict] = []
    if isinstance(parsed, dict):
        for item in parsed.get("assumption_refinements") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key not in known:
                continue
            raw_value = item.get("recommended_value")
            value = raw_value if isinstance(raw_value, (int, float)) and raw_value >= 0 else None
            refinements.append({
                "key": key,
                "label": known[key].get("label") or key,
                "unit": known[key].get("unit"),
                "current_value": known[key].get("value"),
                "recommended_value": value,
                "confidence": (str(item.get("confidence") or "low").lower()
                               if str(item.get("confidence") or "").lower()
                               in {"high", "medium", "low"} else "low"),
                "basis": str(item.get("basis") or "").strip()
                         or "model-generated research plan",
                "rationale": str(item.get("rationale") or "").strip()
                             or "Review against customer-specific public filings.",
                "review_priority": str(item.get("review_priority") or "medium").lower(),
            })

    if not refinements:
        priority_keys = [
            "customerCount", "annualRevenueMM", "omBudgetMM", "capitalBudgetMM",
            "tdLineMiles", "currentSAIDI", "saidiMinuteValueMM",
            "generationFleetMW", "annualFuelSpendMM", "capacityPriceMWDay",
        ]
        for key in priority_keys:
            row = known.get(key)
            if not row:
                continue
            refinements.append({
                "key": key,
                "label": row.get("label") or key,
                "unit": row.get("unit"),
                "current_value": row.get("value"),
                "recommended_value": row.get("value"),
                "confidence": "low",
                "basis": f"Review against {profile_name} annual report, 10-K, FERC Form 1, rate-case filings, or state reliability reports.",
                "rationale": "This is a high-leverage value-engine assumption; leaving it generic can materially distort every use-case value.",
                "review_priority": "high",
            })

    enhancements: list[dict] = []
    if isinstance(parsed, dict):
        for item in parsed.get("app_enhancements") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            enhancements.append({
                "title": title[:140],
                "why": str(item.get("why") or "").strip(),
                "it_delivers": str(item.get("it_delivers") or "").strip(),
                "implementation_hint": str(item.get("implementation_hint") or "").strip(),
            })

    fallback = [
        ("Assumption evidence drawer",
         "Value assumptions need visible provenance before finance or operations will trust them.",
         "A per-assumption evidence view showing source, confidence, last review date, and affected use cases.",
         "Reuse assumption_research rows and add an impact query that lists use cases whose formula references each key."),
        ("Prerequisite impact panel",
         "Awaiting-prerequisite use cases should explain exactly what must be built first.",
         "A one-click list of required prerequisite use cases, their owners, status, value, and unblock date.",
         "Extend the readiness payload already returned by readiness_map with status/owner/value for pending_prereqs."),
        ("Value sensitivity simulator",
         "Customers challenge assumptions by asking which inputs move the answer most.",
         "A tornado chart showing which assumptions have the biggest effect on portfolio value.",
         "Perturb each value_assumption +/-10% and recompute affected use-case value ranges."),
        ("Data gap acquisition plan",
         "Readiness gaps should translate into source-system work, not just red badges.",
         "A backlog grouped by missing data domain, source system, owner, and value at risk.",
         "Aggregate pending_domains and module requirements against the coverage matrix."),
        ("Executive export pack",
         "Sponsors need a concise artifact to socialize the portfolio outside the app.",
         "A PDF or PPT-ready export with portfolio value, buildable value, top blockers, and next actions.",
         "Render the existing dashboard and roadmap rollups through a server-side markdown/HTML export."),
        ("Use-case owner workflow",
         "Tracking use cases needs accountability, not just status.",
         "Owner, sponsor, due date, next milestone, and stale-status alerts per use case.",
         "Add account-scoped owner fields and a status_age query."),
        ("Benefits realization ledger",
         "Realized value should be auditable after go-live.",
         "Monthly actuals, evidence links, variance to hypothesis, and finance approval status.",
         "Extend value_records with period/evidence/approval metadata and chart variance."),
        ("Scenario portfolios",
         "Customers often compare budget-constrained roadmap options.",
         "Named scenarios such as conservative, accelerated, and data-platform-first with side-by-side value/readiness.",
         "Snapshot roadmap/use-case selections into scenario tables and reuse current value_engine calculations."),
        ("Source-system confidence scoring",
         "Discovery data is uneven; users need to know which mappings are weak.",
         "Confidence badges on data-source-to-use-case mappings and a review queue for low-confidence links.",
         "Persist enrichment attribution confidence and expose it in Data Assets and dependency graph views."),
        ("Customer benchmark calibration",
         "Generic industry averages should be replaced by peer-size, region, and utility-type benchmarks.",
         "A calibration page that compares current assumptions to peer bands and flags outliers.",
         "Store benchmark min/p50/max per assumption and compare against the active account values."),
    ]
    seen = {item["title"].lower() for item in enhancements}
    for title, why, delivers, hint in fallback:
        if len(enhancements) >= 10:
            break
        if title.lower() in seen:
            continue
        enhancements.append({
            "title": title,
            "why": why,
            "it_delivers": delivers,
            "implementation_hint": hint,
        })
        seen.add(title.lower())

    return {
        "assumption_refinements": refinements[:12],
        "app_enhancements": enhancements[:10],
    }


@router.get("/customer-enhancements",
            dependencies=[Depends(limiter("research"))])
async def customer_enhancement_agent():
    """Research-oriented agent for customer-specific assumption and app improvements.

    It does not apply assumption changes. Existing /api/research/company and
    /api/research/apply remain the write path because recalibration changes every
    dollar figure in the portfolio.
    """
    account_id = await accounts.current()
    profile_row = await db.fetchrow(
        "SELECT * FROM company_profile WHERE account_id = $1", account_id)
    profile = row_to_dict(profile_row) if profile_row else None
    assumptions = rows_to_list(await db.fetch(
        """SELECT DISTINCT ON (key) key, label, value, unit, category, source,
                  source_note, confidence
           FROM value_assumptions
           WHERE $1::int IS NULL OR account_id = $1 OR account_id IS NULL
           ORDER BY key, (account_id IS NULL)""",
        account_id))
    assumptions.sort(key=lambda row: (row.get("category") or "", row.get("key") or ""))
    condition, params = await portfolio.portfolio_condition("uc")
    use_cases = rows_to_list(await db.fetch(
        f"""SELECT uc.id, uc.title, uc.status, uc.hypothesized_value_json, uc.lob_id
           FROM use_cases uc
           WHERE {condition}
           ORDER BY id
           LIMIT 80""", *params))
    try:
        rmap = await readiness_map()
        value_assumptions = await load_assumptions()
        for row in use_cases:
            row["readiness"] = (rmap.get(row["id"]) or {}).get("readiness")
            rng = compute_value_range(row.get("hypothesized_value_json"),
                                      value_assumptions)
            row["computed_value"] = rng["mid"] if rng else 0
    except Exception:  # noqa: BLE001 - value context is helpful, not required
        for row in use_cases:
            row["computed_value"] = 0
    use_cases = sorted(use_cases, key=lambda row: row.get("computed_value") or 0,
                       reverse=True)[:25]

    generic = [row for row in assumptions if (row.get("source") or "seed") != "research"]
    company = (profile or {}).get("company_name") or "the configured customer"
    prompt = (
        "You are a Power & Utilities value-engineering product agent. Based on the "
        "configured customer profile, current value assumptions, and portfolio, "
        "recommend customer-specific assumption refinements and exactly 10 practical "
        "enhancements to this app. Do not invent assumption keys. If a recommended "
        "assumption value is not defensible from company-specific facts, set it to "
        "null and explain the research needed. Return STRICT JSON matching the schema.\n\n"
        f"CUSTOMER PROFILE: {json.dumps(profile or {'company_name': company})}\n"
        f"CURRENT ASSUMPTIONS: {json.dumps(assumptions[:40])}\n"
        f"GENERIC OR UNCALIBRATED ASSUMPTIONS: {json.dumps([a['key'] for a in generic])}\n"
        f"TOP PORTFOLIO USE CASES: {json.dumps(use_cases)}\n"
    )
    parsed, used_llm, note = await _llm_json(
        prompt, max_tokens=7000, response_schema=CUSTOMER_ENHANCEMENT_SCHEMA)
    normalised = _normalise_customer_agent(parsed, assumptions, profile)
    return {
        "company": profile,
        "researched": bool(profile),
        "model": SERVING_ENDPOINT if used_llm else "heuristic",
        "used_llm": used_llm,
        "fallback_note": note,
        "generic_assumption_count": len(generic),
        "next": "Use /api/research/company to create audited assumption proposals, then /api/research/apply to stage selected changes for confirmation.",
        **normalised,
    }


# ---------------------------------------------------------------------------
# Shared LLM JSON helper
# ---------------------------------------------------------------------------
async def _llm_json(prompt: str, max_tokens: int = 1600, response_schema: dict | None = None):
    """Call the Foundation Model and parse a JSON object from the reply.

    Returns (parsed|None, used_llm, note). `note` is a short reason when we fall
    back so the UI can show 'AI temporarily unavailable — showing heuristic'.

    When `response_schema` is given, the endpoint is asked to enforce it
    server-side, which is far more reliable than scraping braces out of prose.

    OPTIONAL-PARAMETER NEGOTIATION
    ------------------------------
    Endpoints disagree about which optional parameters they accept, and they
    reject an unsupported one with a hard 400 rather than ignoring it. Observed in
    practice: `databricks-claude-sonnet-5` refuses `temperature` outright
    ("Model us.anthropic.claude-sonnet-5 does not support the temperature
    parameter"), while other endpoints refuse `response_format`.

    So the call is retried with progressively fewer optional parameters, dropping
    whichever one the error names. A newer model must never silently degrade the
    agents to heuristics just because it tightened its parameter validation.
    """
    from ..llm import get_llm_client

    # Reasoning models (claude-sonnet-5 and later) spend output tokens on a
    # private reasoning block BEFORE emitting the answer. Observed live: a
    # 1400-token budget was entirely consumed by reasoning, so the reply came
    # back finish_reason=length with an empty answer and every value estimate
    # silently fell back to a heuristic. Floor the budget so there is always room
    # for the answer itself.
    effective_max_tokens = max(max_tokens, MIN_OUTPUT_TOKENS)

    async def _call(*, with_schema: bool, with_temperature: bool):
        client = get_llm_client()
        kwargs = {
            "model": SERVING_ENDPOINT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": effective_max_tokens,
        }
        if with_temperature:
            # Low temperature for parseable, repeatable structured output.
            kwargs["temperature"] = 0.2
        if with_schema and response_schema:
            kwargs["response_format"] = response_schema
        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content

    _as_text = flatten_content

    def _extract(content):
        """Parse the reply, tolerating content blocks, a code fence, or prose."""
        text = _as_text(content)
        if not text.strip():
            raise ValueError("empty response")
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1] if text.count("```") >= 2 else text
            text = text.split("\n", 1)[1] if "\n" in text else text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in response")
        return json.loads(text[start:end + 1])

    # Most capable first, then drop whichever optional parameter the error names.
    # The last attempt sends only model/messages/max_tokens, which every
    # chat-completions endpoint accepts.
    attempts = [
        {"with_schema": True, "with_temperature": True},
        {"with_schema": True, "with_temperature": False},
        {"with_schema": False, "with_temperature": True},
        {"with_schema": False, "with_temperature": False},
    ]
    last_exc: Exception | None = None
    tried: set[tuple] = set()

    for _ in range(len(attempts)):
        # Pick the best remaining option that is still consistent with what the
        # errors so far have told us is unsupported.
        options = [
            a for a in attempts
            if tuple(sorted(a.items())) not in tried
            and not (a["with_schema"] and _schema_rejected(last_exc))
            and not (a["with_temperature"] and _temperature_rejected(last_exc))
        ]
        if not options:
            break
        choice = options[0]
        tried.add(tuple(sorted(choice.items())))
        try:
            return _extract(await _call(**choice)), True, None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # A failure that isn't about an unsupported parameter (auth, quota,
            # a genuinely unparseable reply) won't be fixed by retrying with
            # fewer parameters, so stop rather than burning three more calls.
            if not (_schema_rejected(exc) or _temperature_rejected(exc)):
                break

    note = ("AI temporarily unavailable — showing heuristic ranking. "
            f"({type(last_exc).__name__})")
    # Warning, not info: every fallback means a user saw heuristic numbers where
    # they expected model output, and the cause is usually configuration.
    logger.warning("LLM JSON call fell back to heuristic after %d attempt(s): "
                   "%s: %s", len(tried), type(last_exc).__name__, last_exc)
    return None, False, note


def _temperature_rejected(exc: Exception | None) -> bool:
    return exc is not None and "temperature" in str(exc).lower()


def _schema_rejected(exc: Exception | None) -> bool:
    if exc is None:
        return False
    message = str(exc).lower()
    return any(token in message for token in
               ("response_format", "responseformat", "json_schema"))


async def llm_text(prompt: str, max_tokens: int = 1600) -> tuple[str | None, bool, str | None]:
    """Ask for PROSE. Returns (text|None, used_llm, note).

    A sibling to _llm_json for callers that want markdown rather than a JSON
    object. Exists so no caller has to hand-roll `chat.completions.create`: doing so
    is how the claude-sonnet-5 issues (temperature rejected, content returned as
    typed blocks, reasoning exhausting a small token budget) kept resurfacing in
    endpoints that had been written before the shared path was fixed.
    """
    try:
        parsed_or_text = await _llm_raw(prompt, max_tokens)
        return parsed_or_text, True, None
    except Exception as exc:  # noqa: BLE001
        note = f"AI temporarily unavailable — showing a generated summary. ({type(exc).__name__})"
        logger.warning("LLM prose call fell back (%s: %s)",
                       type(exc).__name__, exc)
        return None, False, note


async def _llm_raw(prompt: str, max_tokens: int) -> str:
    """Shared call path: negotiates optional params, flattens content blocks."""
    from ..llm import get_llm_client

    budget = max(max_tokens, MIN_OUTPUT_TOKENS)
    last: Exception | None = None
    for with_temperature in (True, False):
        if with_temperature and _temperature_rejected(last):
            continue
        try:
            client = get_llm_client()
            kwargs = {
                "model": SERVING_ENDPOINT,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": budget,
            }
            if with_temperature:
                kwargs["temperature"] = 0.4
            resp = await client.chat.completions.create(**kwargs)
            text = flatten_content(resp.choices[0].message.content)
            if not text.strip():
                raise ValueError("empty response")
            return text
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _temperature_rejected(exc):
                break
    raise last or RuntimeError("no response")


async def _portfolio_context():
    from ..readiness import readiness_map
    from ..value_engine import compute_value_range, load_assumptions
    # Portfolio-scoped: roadmap + next-best recommender operate on the confirmed set.
    condition, params = await portfolio.portfolio_condition("uc")
    ucs = [dict(u) for u in await db.fetch(
        f"SELECT uc.* FROM use_cases uc WHERE {condition} ORDER BY uc.id", *params)]
    lobs = {lob["id"]: lob["name"] for lob in await db.fetch("SELECT id, name FROM lobs")}
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


@router.post("/recommend", dependencies=[Depends(limiter("research"))])
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
    lobs = {lob["id"]: lob["name"] for lob in await db.fetch("SELECT id, name FROM lobs")}
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
        account_id = await accounts.current()
        # replace agent-generated roadmap rows for these UCs
        for it in items:
            if account_id is not None:
                await db.execute(
                    """INSERT INTO roadmap_items
                       (account_id, use_case_id, horizon, wave, notes)
                       VALUES ($1,$2,$3,$4,$5)""",
                    account_id, it["use_case_id"], it["horizon"], it["wave"],
                    f"Auto-sequenced by roadmap agent (opportunity {it['opportunity']}).")
            else:
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


@router.post("/estimate-value", dependencies=[Depends(limiter("research"))])
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


@router.post("/decompose-source", dependencies=[Depends(limiter("research"))])
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
