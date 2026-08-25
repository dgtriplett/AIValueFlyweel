"""Use Cases CRUD (the portfolio core)."""
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts
from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db
from .. import portfolio
from ..readiness import readiness_map, readiness_for
from ..value_engine import (
    compute_realized,
    compute_value_range,
    load_assumptions,
)

router = APIRouter(prefix="/use-cases", tags=["use_cases"])

_STAGES = {"U1", "U2", "U3", "U4", "U5", "U6"}
_SUBS = {"fossil", "hydro", "renewables", "nuclear", "cross"}
_EFFORTS = {"S", "M", "L", "XL"}
_STATUSES = {"not_started", "scoping", "in_progress", "live", "value_realized"}


class UseCaseIn(BaseModel):
    title: str
    description: str | None = None
    lob_id: int | None = None
    sub_vertical: str | None = None
    stage: str | None = None
    phase: int | None = None
    status: str = "not_started"
    category: str | None = None
    effort_tshirt: str | None = None
    priority_score: float | None = None
    risk_tags: list[str] = []
    compliance_tags: list[str] = []
    hypothesized_value_json: dict | None = None
    realized_value_amount: float | None = None
    realized_value_json: dict | None = None
    realized_override_enabled: bool = False
    realized_override_amount: float | None = None
    realized_override_note: str | None = None
    hypothesized_override_enabled: bool = False
    hypothesized_override_amount: float | None = None
    hypothesized_override_note: str | None = None
    status_source: str = "manual"


def _validate(body: UseCaseIn):
    if body.stage and body.stage not in _STAGES:
        raise HTTPException(422, f"stage must be one of {_STAGES}")
    if body.sub_vertical and body.sub_vertical not in _SUBS:
        raise HTTPException(422, f"sub_vertical must be one of {_SUBS}")
    if body.effort_tshirt and body.effort_tshirt not in _EFFORTS:
        raise HTTPException(422, f"effort_tshirt must be one of {_EFFORTS}")
    if body.status and body.status not in _STATUSES:
        raise HTTPException(422, f"status must be one of {_STATUSES}")
    if body.phase is not None and not (0 <= body.phase <= 4):
        raise HTTPException(422, "phase must be 0..4")


@router.get("")
async def list_use_cases(
    scope: str = "portfolio",
    lob_id: int | None = None,
    sub_vertical: str | None = None,
    phase: int | None = None,
    status: str | None = None,
    readiness: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    """List use cases with optional server-side filters + pagination.

    `scope` selects the working set:
      - "portfolio" (default): only in_portfolio=true (the customer's confirmed set)
      - "catalog": only origin='catalog' (the predefined idea library)
      - "all": the full universe (used by the flywheel's Catalog scope toggle)

    SQL-filterable columns (scope/lob_id/sub_vertical/phase/status) are pushed to
    the query; readiness and free-text are applied after enrichment. When any
    filter or limit is provided, the response is an envelope {items, total, limit,
    offset}; otherwise a bare list (back-compat for the frontend).
    """
    if scope not in ("portfolio", "catalog", "all"):
        raise HTTPException(422, "scope must be portfolio|catalog|all")
    where, params = [], []

    def add_filter(column: str, value) -> None:
        """Append an equality predicate bound to the next positional parameter.

        The placeholder number must be read AFTER the append, since asyncpg's $n is
        1-based and positional. Doing both in one helper makes that ordering
        structural instead of a convention a future edit can quietly break by
        inserting a line between the two halves.
        """
        params.append(value)
        where.append(f"{column} = ${len(params)}")

    membership_expr, membership_params = await portfolio.select_membership_expression(
        "use_cases", param_index=1)
    params.extend(membership_params)

    if scope == "portfolio":
        condition, condition_params = await portfolio.portfolio_condition(
            "use_cases", param_index=len(params) + 1)
        where.append(condition)
        params.extend(condition_params)
    elif scope == "catalog":
        where.append("origin = 'catalog'")
    if lob_id is not None:
        add_filter("lob_id", lob_id)
    if sub_vertical:
        add_filter("sub_vertical", sub_vertical)
    if phase is not None:
        add_filter("phase", phase)
    if status:
        add_filter("status", status)
    sql = f"SELECT use_cases.*, {membership_expr} AS in_portfolio FROM use_cases"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    rows = await db.fetch(sql, *params)
    ucs = rows_to_list(rows)

    rmap = await readiness_map()
    assumptions = await load_assumptions()
    for uc in ucs:
        r = rmap.get(uc["id"])
        if r:
            uc.update(r)
        rng = compute_value_range(uc.get("hypothesized_value_json"), assumptions)
        uc["value_range"] = rng
        uc["computed_value"] = rng["mid"] if rng else None
        uc["realized"] = compute_realized(uc, assumptions)

    # post-enrichment filters (readiness is derived; free text spans several fields)
    if readiness:
        ucs = [u for u in ucs if u.get("readiness") == readiness]
    if q:
        ql = q.lower()
        ucs = [u for u in ucs if ql in (
            f"{u['title']} {u.get('description') or ''} "
            f"{' '.join(u.get('risk_tags') or [])} {' '.join(u.get('compliance_tags') or [])}".lower())]

    any_filter = any(v is not None and v != "" for v in
                     (lob_id, sub_vertical, phase, status, readiness, q)) \
                 or limit is not None or scope != "portfolio"
    total = len(ucs)
    if limit is not None:
        ucs = ucs[offset:offset + limit]
    elif offset:
        ucs = ucs[offset:]

    if any_filter:
        return {"items": ucs, "total": total, "limit": limit, "offset": offset}
    return ucs  # bare list — backward compatible for the current frontend


@router.get("/readiness")
async def all_readiness():
    """Map of use_case_id -> readiness info (used for graph coloring/filters)."""
    return await readiness_map()


@router.get("/{uc_id}")
async def get_use_case(uc_id: int):
    row = await db.fetchrow("SELECT * FROM use_cases WHERE id = $1", uc_id)
    if row is None:
        raise HTTPException(404, "Use case not found")
    uc = row_to_dict(row)
    uc.update(await readiness_for(uc_id))
    return uc


@router.get("/{uc_id}/readiness")
async def get_readiness(uc_id: int):
    return await readiness_for(uc_id)


@router.get("/{uc_id}/network")
async def get_network(uc_id: int):
    """Relationship network for the blast radius — matches the ORIGINAL
    data-ai-maturity-assessment RoadmapBlastRadius.tsx logic VERBATIM.

    In the original, each UC has an explicit `dependencies` array (its direct
    prerequisite use cases). Our equivalent: `uc_enables_uc` where
    from --enables--> to means `to.dependencies` includes `from`. So for a focal
    use case S:
      - prerequisites (amber) = S.dependencies                 (DIRECT parents only)
          = { from : (from --enables--> S) }
      - builds_upon  (green)  = { uc : S in uc.dependencies }  (DIRECT children only)
          = { to : (S --enables--> to) }
      - shared_data  (purple) = other UCs (not S, not prereq, not builds) whose
          dependencies OVERLAP S.dependencies — i.e. SIBLINGS that share a common
          prerequisite USE CASE. (NOT shared data assets.)
    """
    exists = await db.fetchrow("SELECT 1 FROM use_cases WHERE id=$1", uc_id)
    if exists is None:
        raise HTTPException(404, "Use case not found")

    enables = await db.fetch("SELECT from_use_case_id, to_use_case_id FROM uc_enables_uc")
    # deps[uc] = set of its DIRECT prerequisite use cases (the original's uc.dependencies)
    deps: dict[int, set[int]] = {}
    for e in enables:
        deps.setdefault(e["to_use_case_id"], set()).add(e["from_use_case_id"])

    sel_deps = deps.get(uc_id, set())            # S.dependencies
    prereqs = set(sel_deps)                       # direct parents only
    builds = {uc for uc, d in deps.items() if uc_id in d}  # direct children only

    shared: set[int] = set()
    if sel_deps:
        for uc, d in deps.items():
            if uc == uc_id or uc in prereqs or uc in builds:
                continue
            if d & sel_deps:                      # shares a common prerequisite UC
                shared.add(uc)

    # Resolve titles for every id we return so the client never has to fall back
    # to a raw id (the related use cases may live outside its loaded scope).
    ids = list(prereqs | builds | shared | {uc_id})
    title_rows = await db.fetch(
        "SELECT id, title FROM use_cases WHERE id = ANY($1::int[])", ids)
    titles = {str(r["id"]): r["title"] for r in title_rows}

    return {
        "focal": uc_id,
        "prerequisites": sorted(prereqs),
        "builds_upon": sorted(builds),
        "shared_data": sorted(shared),
        "titles": titles,
    }


@router.get("/{uc_id}/unlocks")
async def get_unlocks(uc_id: int):
    """The 'unlocks' set for the flywheel highlight moment:
      - newly_enabled: UCs this UC enables (downstream in uc_enables_uc), and
      - becomes_ready: UCs whose readiness would improve/flip to shovel-ready
        because delivering this UC lands its required data assets.
    Returns the unlocked UCs + a value/LOB summary.
    """
    exists = await db.fetchrow("SELECT lob_id FROM use_cases WHERE id=$1", uc_id)
    if exists is None:
        raise HTTPException(404, "Use case not found")

    assumptions = await load_assumptions()

    # newly enabled (direct downstream)
    enabled = await db.fetch(
        """SELECT uc.* FROM uc_enables_uc e JOIN use_cases uc ON uc.id = e.to_use_case_id
           WHERE e.from_use_case_id = $1""", uc_id)

    # readiness flips: assets this UC requires (delivering it lands them)
    my_assets = {r["data_asset_id"] for r in await db.fetch(
        "SELECT data_asset_id FROM uc_requires_asset WHERE use_case_id=$1", uc_id)}
    becomes = []
    awaiting_prereqs: list[int] = []
    if my_assets:
        # for every OTHER uc, recompute required-readiness assuming my_assets are landed(>=curated)
        req_rows = await db.fetch(
            """SELECT ura.use_case_id, ura.data_asset_id, da.ingestion_status
               FROM uc_requires_asset ura JOIN data_assets da ON da.id = ura.data_asset_id
               WHERE ura.criticality='required'""")
        by_uc: dict[int, list] = {}
        for r in req_rows:
            by_uc.setdefault(r["use_case_id"], []).append((r["data_asset_id"], r["ingestion_status"]))
        # Imported, not restated: a local copy of the readiness rule would let this
        # "what does delivering X unlock?" calculation disagree with readiness itself.
        from ..readiness import BUILT_STATUSES, READY_STATUSES as READY

        # A use case only becomes TRULY shovel-ready when its data completes AND its
        # upstream prerequisites are built — that is the definition readiness.py and
        # /api/impact both use. This endpoint previously checked data only, so it
        # reported use cases as "unlocked" that were actually still waiting on
        # upstream work, and disagreed with /api/impact about the same question.
        prereq_rows = await db.fetch(
            """SELECT e.to_use_case_id AS uc_id,
                      bool_and(up.status = ANY($1::text[])) AS all_built
               FROM uc_enables_uc e
               JOIN use_cases up ON up.id = e.from_use_case_id
               GROUP BY e.to_use_case_id""",
            list(BUILT_STATUSES))
        # Absent from the map means no prerequisites, which is trivially satisfied.
        prereqs_built = {r["uc_id"]: bool(r["all_built"]) for r in prereq_rows}

        for other_id, reqs in by_uc.items():
            if other_id == uc_id:
                continue
            now_ready = all(st in READY for (_a, st) in reqs)
            hypo_ready = all((aid in my_assets) or (st in READY) for (aid, st) in reqs)
            if not (hypo_ready and not now_ready):
                continue
            if prereqs_built.get(other_id, True):
                becomes.append(other_id)
            else:
                # Data would complete, but upstream builds are outstanding. Surfaced
                # separately rather than dropped: it is a real fast-follow, just not
                # an immediate unlock.
                awaiting_prereqs.append(other_id)

    enabled_ids = {u["id"] for u in enabled}
    unlocked_ids = enabled_ids | set(becomes)
    # hydrate + value
    unlocked = []
    total_value = 0.0
    lobs_ = set()
    if unlocked_ids:
        rows = await db.fetch("SELECT * FROM use_cases WHERE id = ANY($1::int[])", list(unlocked_ids))
        for r in rows:
            d = dict(r)
            rng = compute_value_range(d.get("hypothesized_value_json"), assumptions)
            v = rng["mid"] if rng else 0
            total_value += v or 0
            if d.get("lob_id"):
                lobs_.add(d["lob_id"])
            unlocked.append({
                "id": d["id"], "title": d["title"], "lob_id": d["lob_id"],
                "phase": d["phase"], "value_mm": v,
                "reason": "enabled" if d["id"] in enabled_ids else "becomes_ready",
            })
    unlocked.sort(key=lambda x: -(x["value_mm"] or 0))
    return {
        "use_case_id": uc_id,
        "newly_enabled": sorted(enabled_ids),
        "becomes_ready": sorted(becomes),
        # Data would complete but upstream builds are still outstanding. Reported
        # separately so the headline "unlocks" count means what it says, while the
        # fast-follows remain visible.
        "awaiting_prerequisites": sorted(awaiting_prereqs),
        "unlocked": unlocked,
        "summary": {
            "count": len(unlocked_ids),
            "awaiting_prerequisites_count": len(awaiting_prereqs),
            "lob_count": len(lobs_),
            "hypothesized_value": round(total_value, 2),
        },
    }


@router.get("/{uc_id}/detail")
async def get_use_case_detail(uc_id: int):
    """Everything the detail drawer needs in one call."""
    row = await db.fetchrow("SELECT * FROM use_cases WHERE id = $1", uc_id)
    if row is None:
        raise HTTPException(404, "Use case not found")
    uc = row_to_dict(row)
    uc.update(await readiness_for(uc_id))
    assumptions = await load_assumptions()
    rng = compute_value_range(uc.get("hypothesized_value_json"), assumptions)
    uc["value_range"] = rng
    uc["computed_value"] = rng["mid"] if rng else None
    uc["realized"] = compute_realized(uc, assumptions)

    account_id = await accounts.current()
    # FIX PART A: Overlay per-account status (not the shared catalog status), and
    # include the new rationale field. Matches the pattern in data_assets.py line 47.
    required = await db.fetch(
        """SELECT da.*,
                  COALESCE(acs.ingestion_status, da.ingestion_status) AS ingestion_status,
                  ura.criticality,
                  ura.rationale
           FROM uc_requires_asset ura
           JOIN data_assets da ON da.id = ura.data_asset_id
           LEFT JOIN account_asset_status acs
                  ON acs.data_asset_id = da.id AND acs.account_id = $2
           WHERE ura.use_case_id = $1
           ORDER BY ura.criticality, da.source_system, da.module""",
        uc_id, account_id,
    )
    enables = await db.fetch(
        """SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale, e.detected_by_agent
           FROM uc_enables_uc e JOIN use_cases uc ON uc.id = e.to_use_case_id
           WHERE e.from_use_case_id = $1 ORDER BY uc.title""",
        uc_id,
    )
    enabled_by = await db.fetch(
        """SELECT uc.id, uc.title, uc.stage, uc.phase, uc.status, e.rationale, e.detected_by_agent
           FROM uc_enables_uc e JOIN use_cases uc ON uc.id = e.from_use_case_id
           WHERE e.to_use_case_id = $1 ORDER BY uc.title""",
        uc_id,
    )
    # FIX PART A.2: Split required vs helpful assets server-side so the count reflects
    # only truly required ones.
    required_list = rows_to_list(required)
    required_assets = [a for a in required_list if a.get("criticality") == "required"]
    helpful_assets = [a for a in required_list if a.get("criticality") == "helpful"]

    # BUG 2 FIX: Include domain-path requirements (uc_requires_domain) so the detail
    # pane matches the readiness badge. A required domain is satisfied when ANY serving
    # asset is curated/governed, mirroring readiness.py's logic. For domains with no
    # serving assets, surface the domain itself as a required item.
    domain_reqs = await db.fetch(
        """SELECT urd.domain_id, urd.necessity, dd.name AS domain_name, dd.label AS domain_label,
                  urd.rationale
           FROM uc_requires_domain urd
           JOIN data_domains dd ON dd.id = urd.domain_id
           WHERE urd.use_case_id = $1
             AND COALESCE(dd.is_active, true) = true
           ORDER BY urd.necessity, dd.label""",
        uc_id,
    )

    # For each domain requirement, resolve its serving assets
    for domain_req in domain_reqs:
        domain_id = domain_req["domain_id"]
        necessity = domain_req["necessity"]
        domain_name = domain_req["domain_name"]
        domain_label = domain_req["domain_label"]
        rationale = domain_req["rationale"] or f"Required domain: {domain_label}"

        # Find all assets that serve this domain, with per-account status overlay
        serving_assets = await db.fetch(
            """SELECT da.*,
                      COALESCE(acs.ingestion_status, da.ingestion_status) AS ingestion_status
               FROM asset_serves_domain asd
               JOIN data_assets da ON da.id = asd.data_asset_id
               LEFT JOIN account_asset_status acs
                      ON acs.data_asset_id = da.id AND acs.account_id = $2
               WHERE asd.domain_id = $1
               ORDER BY da.source_system, da.module""",
            domain_id, account_id,
        )

        if serving_assets:
            # Add each serving asset to the appropriate list (required/helpful)
            for asset_row in serving_assets:
                asset_dict = dict(asset_row)
                asset_dict["criticality"] = necessity
                asset_dict["rationale"] = rationale
                asset_dict["via_domain"] = True  # Mark as coming from domain path
                asset_dict["domain_name"] = domain_name
                asset_dict["domain_label"] = domain_label

                # Check if this asset is already in the list (from module path)
                asset_id = asset_dict["id"]
                already_present = any(a.get("id") == asset_id for a in (required_list if necessity == "required" else []))
                already_present = already_present or any(a.get("id") == asset_id for a in (helpful_assets if necessity == "helpful" else []))

                if not already_present:
                    if necessity == "required":
                        required_assets.append(asset_dict)
                    else:
                        helpful_assets.append(asset_dict)
        else:
            # No serving assets for this domain — surface the DOMAIN itself as a requirement
            # so the user sees WHAT is required rather than an empty list
            domain_item = {
                "id": None,  # No asset id
                "domain_id": domain_id,
                "domain_name": domain_name,
                "domain_label": domain_label,
                "source_system": None,
                "module": f"[Domain] {domain_label}",
                "description": "Required data domain with no serving assets yet",
                "criticality": necessity,
                "rationale": rationale,
                "ingestion_status": "not_started",
                "is_domain_placeholder": True,  # Flag to help frontend render differently
            }
            if necessity == "required":
                required_assets.append(domain_item)
            else:
                helpful_assets.append(domain_item)


    if account_id is not None:
        values = await db.fetch(
            "SELECT * FROM value_records WHERE account_id=$1 AND use_case_id = $2 ORDER BY id",
            account_id, uc_id)
        comments = await db.fetch(
            """SELECT * FROM comments
               WHERE account_id=$1 AND entity_type='use_case' AND entity_id=$2
               ORDER BY created_at""",
            account_id, uc_id)
        # Fetch progression data (target go-live date + event history)
        progress_row = await db.fetchrow(
            """SELECT target_go_live_date, updated_at, updated_by
               FROM account_use_case_progress
               WHERE account_id=$1 AND use_case_id=$2""",
            account_id, uc_id)
        events = await db.fetch(
            """SELECT id, event_type, from_value, to_value, note, created_by, created_at
               FROM use_case_status_events
               WHERE account_id=$1 AND use_case_id=$2
               ORDER BY created_at DESC""",
            account_id, uc_id)
        # Compute at_risk flag: target in past and status not live/value_realized
        at_risk = False
        if progress_row and progress_row["target_go_live_date"]:
            from datetime import date
            target = progress_row["target_go_live_date"]
            status = uc.get("status")
            if target < date.today() and status not in {"live", "value_realized"}:
                at_risk = True
        progression = {
            "target_go_live_date": progress_row["target_go_live_date"].isoformat() if progress_row and progress_row["target_go_live_date"] else None,
            "updated_at": progress_row["updated_at"].isoformat() if progress_row and progress_row["updated_at"] else None,
            "updated_by": progress_row["updated_by"] if progress_row else None,
            "at_risk": at_risk,
            "events": rows_to_list(events),
        }
    else:
        values = await db.fetch(
            "SELECT * FROM value_records WHERE use_case_id = $1 ORDER BY id", uc_id,
        )
        comments = await db.fetch(
            "SELECT * FROM comments WHERE entity_type='use_case' AND entity_id=$1 ORDER BY created_at",
            uc_id,
        )
        progression = {"target_go_live_date": None, "updated_at": None, "updated_by": None, "at_risk": False, "events": []}
    return {
        **uc,
        "required_assets": required_assets,
        "helpful_assets": helpful_assets,
        "enables": rows_to_list(enables),
        "enabled_by": rows_to_list(enabled_by),
        "value_records": rows_to_list(values),
        "comments": rows_to_list(comments),
        "progression": progression,
    }


@router.post("")
async def create_use_case(body: UseCaseIn, request: Request):
    _validate(body)
    actor = current_user(request)
    hv = json.dumps(body.hypothesized_value_json) if body.hypothesized_value_json is not None else None
    rvj = json.dumps(body.realized_value_json) if body.realized_value_json is not None else None
    # Custom-authored use cases go straight into the portfolio.
    row = await db.fetchrow(
        """INSERT INTO use_cases
           (title, description, lob_id, sub_vertical, stage, phase, status, category,
            effort_tshirt, priority_score, risk_tags, compliance_tags,
            hypothesized_value_json, realized_value_amount, realized_value_json,
            realized_override_enabled, realized_override_amount, realized_override_note,
            hypothesized_override_enabled, hypothesized_override_amount, hypothesized_override_note,
            status_source, created_by, origin, in_portfolio)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15::jsonb,
                   $16,$17,$18,$19,$20,$21,$22,$23,'custom',true) RETURNING *""",
        body.title, body.description, body.lob_id, body.sub_vertical, body.stage,
        1, body.status, body.category, body.effort_tshirt, body.priority_score,  # phase derived (no prereqs yet)
        body.risk_tags, body.compliance_tags, hv, body.realized_value_amount, rvj,
        body.realized_override_enabled, body.realized_override_amount, body.realized_override_note,
        body.hypothesized_override_enabled, body.hypothesized_override_amount, body.hypothesized_override_note,
        body.status_source, actor,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await portfolio.set_membership(row["id"], True, actor=actor, source="custom_create")
    await write_audit("use_case", row["id"], "create", actor, {"title": body.title})
    out = row_to_dict(row)
    out["in_portfolio"] = True
    return out


@router.put("/{uc_id}")
async def update_use_case(uc_id: int, body: UseCaseIn, request: Request):
    _validate(body)
    actor = current_user(request)
    prev = await db.fetchrow("SELECT status, title FROM use_cases WHERE id=$1", uc_id)
    if prev is None:
        raise HTTPException(404, "Use case not found")
    prev_status = prev["status"]
    hv = json.dumps(body.hypothesized_value_json) if body.hypothesized_value_json is not None else None
    rvj = json.dumps(body.realized_value_json) if body.realized_value_json is not None else None
    row = await db.fetchrow(
        """UPDATE use_cases SET
           title=$1, description=$2, lob_id=$3, sub_vertical=$4, stage=$5,
           status=$6, category=$7, effort_tshirt=$8, priority_score=$9,
           risk_tags=$10, compliance_tags=$11,
           hypothesized_value_json=COALESCE($12::jsonb, hypothesized_value_json),
           realized_value_amount=$13,
           realized_value_json=COALESCE($14::jsonb, realized_value_json),
           realized_override_enabled=$15, realized_override_amount=$16,
           realized_override_note=$17,
           hypothesized_override_enabled=$18, hypothesized_override_amount=$19,
           hypothesized_override_note=$20, status_source=$21, updated_at=now()
           WHERE id=$22 RETURNING *""",  # phase is DERIVED (not client-settable)
        body.title, body.description, body.lob_id, body.sub_vertical, body.stage,
        body.status, body.category, body.effort_tshirt, body.priority_score,
        body.risk_tags, body.compliance_tags, hv, body.realized_value_amount, rvj,
        body.realized_override_enabled, body.realized_override_amount, body.realized_override_note,
        body.hypothesized_override_enabled, body.hypothesized_override_amount, body.hypothesized_override_note,
        body.status_source, uc_id,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("use_case", uc_id, "update", actor, {"title": body.title, "status": body.status})

    # Addition A: delivering a use case implies its required data has landed.
    delivered = {"live", "value_realized"}
    if body.status in delivered and prev_status not in delivered:
        await _capture_delivered_assets(uc_id, body.title)

    return row_to_dict(row)


class StatusChange(BaseModel):
    status: str | None = None  # explicit target; ignored when advance=True
    advance: bool = False      # move exactly one stage forward


# Lifecycle order used by the inline quick-status control + one-click "Advance".
_STATUS_FLOW = ["not_started", "scoping", "in_progress", "live", "value_realized"]


@router.patch("/{uc_id}/status")
async def change_status(uc_id: int, body: StatusChange, request: Request):
    """Lightweight status-only change (inline quick control + one-click Advance).
    Persists + audits, and still fires the delivered-asset auto-capture."""
    actor = current_user(request)
    prev = await db.fetchrow("SELECT status, title FROM use_cases WHERE id=$1", uc_id)
    if prev is None:
        raise HTTPException(404, "Use case not found")
    prev_status = prev["status"]

    if body.advance:
        try:
            idx = _STATUS_FLOW.index(prev_status)
        except ValueError:
            idx = 0
        if idx >= len(_STATUS_FLOW) - 1:
            raise HTTPException(409, "Already at final stage (value realized)")
        target = _STATUS_FLOW[idx + 1]
    else:
        target = body.status
        if target not in _STATUSES:
            raise HTTPException(422, f"status must be one of {_STATUSES}")

    row = await db.fetchrow(
        "UPDATE use_cases SET status=$1, updated_at=now() WHERE id=$2 RETURNING *",
        target, uc_id,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("use_case", uc_id, "status_change", actor,
                      {"from": prev_status, "to": target,
                       "via": "advance" if body.advance else "inline"})

    delivered = {"live", "value_realized"}
    if target in delivered and prev_status not in delivered:
        await _capture_delivered_assets(uc_id, prev["title"])

    return row_to_dict(row)


class PortfolioToggle(BaseModel):
    in_portfolio: bool


@router.patch("/{uc_id}/portfolio")
async def toggle_portfolio(uc_id: int, body: PortfolioToggle, request: Request):
    """Add a catalog use case to (or remove it from) the active portfolio."""
    actor = current_user(request)
    prev = await db.fetchrow("SELECT title, origin FROM use_cases WHERE id=$1", uc_id)
    if prev is None:
        raise HTTPException(404, "Use case not found")
    await portfolio.set_membership(
        uc_id, body.in_portfolio, actor=actor, source="manual")
    row = await db.fetchrow("SELECT * FROM use_cases WHERE id=$1", uc_id)
    await write_audit("use_case", uc_id, "portfolio_toggle", actor,
                      {"in_portfolio": body.in_portfolio, "title": prev["title"]})
    return row_to_dict(row)


class PortfolioBulk(BaseModel):
    ids: list[int]
    in_portfolio: bool = True


@router.post("/portfolio/bulk")
async def bulk_portfolio(body: PortfolioBulk, request: Request):
    """Bulk add/remove catalog use cases to/from the portfolio."""
    actor = current_user(request)
    if not body.ids:
        return {"updated": 0}
    if body.in_portfolio:
        await portfolio.add_many(body.ids, actor=actor, source="manual_bulk")
    else:
        account_id = await accounts.current()
        if account_id is not None:
            await db.execute(
                "DELETE FROM account_portfolio_use_cases "
                "WHERE account_id=$1 AND use_case_id = ANY($2::int[])",
                account_id, body.ids,
            )
        else:
            await db.execute(
                "UPDATE use_cases SET in_portfolio=false, updated_at=now() "
                "WHERE id = ANY($1::int[])",
                body.ids,
            )
    await write_audit("use_case", 0, "portfolio_bulk", actor,
                      {"in_portfolio": body.in_portfolio, "count": len(body.ids), "ids": body.ids})
    return {"updated": len(body.ids)}


async def _capture_delivered_assets(uc_id: int, uc_title: str) -> None:
    """When a UC is delivered, promote its required assets to >= 'landed'
    (never downgrade) and mark them auto_captured, with an audit trail."""
    rows = await db.fetch(
        """SELECT da.id, da.ingestion_status
           FROM uc_requires_asset ura JOIN data_assets da ON da.id = ura.data_asset_id
           WHERE ura.use_case_id = $1""",
        uc_id,
    )
    note = f"Inferred landed from delivered use case: {uc_title}"
    for r in rows:
        if r["ingestion_status"] == "not_started":
            await db.execute(
                "UPDATE data_assets SET ingestion_status='landed', auto_captured=true, "
                "auto_note=$2, updated_at=now() WHERE id=$1",
                r["id"], note,
            )
            await write_audit("data_asset", r["id"], "auto_landed", "system",
                              {"reason": note, "from": "not_started", "to": "landed"})


class ProgressionTargetDate(BaseModel):
    target_go_live_date: str | None  # ISO date string or null to clear
    reason: str | None = None  # Required when moving date later (slippage)


class ProgressionNote(BaseModel):
    note: str


@router.get("/{uc_id}/progression")
async def get_progression(uc_id: int):
    """Get the progression data for a use case (target date + event history)."""
    exists = await db.fetchrow("SELECT 1 FROM use_cases WHERE id=$1", uc_id)
    if exists is None:
        raise HTTPException(404, "Use case not found")

    account_id = await accounts.current()
    if account_id is None:
        return {"target_go_live_date": None, "updated_at": None, "updated_by": None, "at_risk": False, "events": []}

    progress_row = await db.fetchrow(
        """SELECT target_go_live_date, updated_at, updated_by
           FROM account_use_case_progress
           WHERE account_id=$1 AND use_case_id=$2""",
        account_id, uc_id)
    events = await db.fetch(
        """SELECT id, event_type, from_value, to_value, note, created_by, created_at
           FROM use_case_status_events
           WHERE account_id=$1 AND use_case_id=$2
           ORDER BY created_at DESC""",
        account_id, uc_id)

    # Compute at_risk flag
    at_risk = False
    if progress_row and progress_row["target_go_live_date"]:
        from datetime import date
        target = progress_row["target_go_live_date"]
        uc_row = await db.fetchrow("SELECT status FROM use_cases WHERE id=$1", uc_id)
        status = uc_row["status"] if uc_row else None
        if target < date.today() and status not in {"live", "value_realized"}:
            at_risk = True

    return {
        "target_go_live_date": progress_row["target_go_live_date"].isoformat() if progress_row and progress_row["target_go_live_date"] else None,
        "updated_at": progress_row["updated_at"].isoformat() if progress_row and progress_row["updated_at"] else None,
        "updated_by": progress_row["updated_by"] if progress_row else None,
        "at_risk": at_risk,
        "events": rows_to_list(events),
    }


@router.put("/{uc_id}/progression/target-date")
async def set_target_date(uc_id: int, body: ProgressionTargetDate, request: Request):
    """Set or update the target go-live date for a use case. Records slippage events when date moves later."""
    exists = await db.fetchrow("SELECT 1 FROM use_cases WHERE id=$1", uc_id)
    if exists is None:
        raise HTTPException(404, "Use case not found")

    account_id = await accounts.current()
    if account_id is None:
        raise HTTPException(403, "Account required for progression tracking")

    actor = current_user(request)

    # Parse the new target date
    from datetime import date as date_type
    new_date: date_type | None = None
    if body.target_go_live_date:
        try:
            new_date = date_type.fromisoformat(body.target_go_live_date)
        except ValueError:
            raise HTTPException(422, "Invalid date format; use ISO YYYY-MM-DD")

    # Fetch current progress
    prev_row = await db.fetchrow(
        "SELECT target_go_live_date FROM account_use_case_progress WHERE account_id=$1 AND use_case_id=$2",
        account_id, uc_id)
    prev_date = prev_row["target_go_live_date"] if prev_row else None

    # Determine event type and validate
    if prev_date is None and new_date is not None:
        event_type = "date_set"
        from_value = None
        to_value = new_date.isoformat()
    elif prev_date is not None and new_date is None:
        event_type = "date_cleared"
        from_value = prev_date.isoformat()
        to_value = None
    elif prev_date is not None and new_date is not None and prev_date != new_date:
        event_type = "date_change"
        from_value = prev_date.isoformat()
        to_value = new_date.isoformat()
        # SLIPPAGE: when date moves LATER, require a reason
        if new_date > prev_date and not body.reason:
            raise HTTPException(422, "Reason required when target date moves later (slippage)")
    else:
        # No change
        return await get_progression(uc_id)

    # Upsert progress row
    await db.execute(
        """INSERT INTO account_use_case_progress (account_id, use_case_id, target_go_live_date, updated_at, updated_by)
           VALUES ($1, $2, $3, now(), $4)
           ON CONFLICT (account_id, use_case_id)
           DO UPDATE SET target_go_live_date=$3, updated_at=now(), updated_by=$4""",
        account_id, uc_id, new_date, actor)

    # Record the event
    await db.execute(
        """INSERT INTO use_case_status_events
           (account_id, use_case_id, event_type, from_value, to_value, note, created_by, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, now())""",
        account_id, uc_id, event_type, from_value, to_value, body.reason, actor)

    await write_audit("use_case", uc_id, "progression_date", actor,
                      {"event_type": event_type, "from": from_value, "to": to_value, "reason": body.reason})

    return await get_progression(uc_id)


@router.post("/{uc_id}/progression/note")
async def add_progression_note(uc_id: int, body: ProgressionNote, request: Request):
    """Add a free-text note to the progression history."""
    exists = await db.fetchrow("SELECT 1 FROM use_cases WHERE id=$1", uc_id)
    if exists is None:
        raise HTTPException(404, "Use case not found")

    account_id = await accounts.current()
    if account_id is None:
        raise HTTPException(403, "Account required for progression tracking")

    actor = current_user(request)

    if not body.note or not body.note.strip():
        raise HTTPException(422, "Note cannot be empty")

    await db.execute(
        """INSERT INTO use_case_status_events
           (account_id, use_case_id, event_type, from_value, to_value, note, created_by, created_at)
           VALUES ($1, $2, 'note', NULL, NULL, $3, $4, now())""",
        account_id, uc_id, body.note.strip(), actor)

    await write_audit("use_case", uc_id, "progression_note", actor, {"note": body.note.strip()})

    return await get_progression(uc_id)


@router.delete("/{uc_id}")
async def delete_use_case(uc_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM use_cases WHERE id = $1", uc_id)
    await write_audit("use_case", uc_id, "delete", actor)
    return {"deleted": res is not None}
