"""Data domains — the semantic data-need layer.

See `server/migrations/002_domains.sql` for why this layer exists and
`server/readiness.py` for how it feeds readiness scoring (the dual path).

Endpoints
---------
GET    /api/domains                     list domains + coverage/usage rollups
GET    /api/domains/{id}                 one domain with its serving assets + use cases
POST   /api/domains                      create a custom domain
PUT    /api/domains/{id}                 edit (marks is_user_edited)
DELETE /api/domains/{id}                 delete (cascades its mappings)
PUT    /api/domains/{id}/assets          replace the serving-asset set (manual)
GET    /api/domains/gaps                 unsatisfied domains ranked by value blocked
GET    /api/use-cases/{id}/domains       a use case's required domains
PUT    /api/use-cases/{id}/domains       replace them (sets domains_locked)
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/domains", tags=["domains"])

_CATEGORIES = {"operational", "asset", "customer", "grid", "market",
               "financial", "regulatory", "safety", "external", "workforce"}
_NECESSITY = {"required", "helpful"}
_CONFIDENCE = {"high", "medium", "low"}
READY = ("curated", "governed")


class DomainIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str | None = None
    example_attributes: str | None = None
    is_active: bool = True


class DomainAssetsIn(BaseModel):
    data_asset_ids: list[int] = []
    confidence: str = "high"


class UcDomainItem(BaseModel):
    domain_id: int
    necessity: str = "required"
    rationale: str | None = None


class UcDomainsIn(BaseModel):
    domains: list[UcDomainItem] = []
    lock: bool = True


def _validate_category(category: str | None) -> None:
    if category is not None and category not in _CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(_CATEGORIES)}")


# ---------------------------------------------------------------------------
# Domain list + rollups
# ---------------------------------------------------------------------------
@router.get("")
async def list_domains(include_inactive: bool = False):
    """Domains with the counts that make the layer legible: how many assets can
    serve each domain, how many of those have actually landed, and how many use
    cases depend on it. `satisfied` mirrors the readiness rule exactly (ANY
    serving asset curated/governed)."""
    where = "" if include_inactive else "WHERE COALESCE(dd.is_active, true) = true"
    rows = await db.fetch(f"""
        SELECT dd.*,
               COUNT(DISTINCT asd.data_asset_id) AS serving_asset_count,
               COUNT(DISTINCT asd.data_asset_id) FILTER (
                   WHERE da.ingestion_status IN ('curated','governed')
               ) AS ready_asset_count,
               COUNT(DISTINCT urd.use_case_id) AS use_case_count,
               COUNT(DISTINCT urd.use_case_id) FILTER (
                   WHERE urd.necessity = 'required'
               ) AS required_by_count
        FROM data_domains dd
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        LEFT JOIN uc_requires_domain urd ON urd.domain_id = dd.id
        {where}
        GROUP BY dd.id
        ORDER BY dd.category NULLS LAST, dd.label
    """)
    out = rows_to_list(rows)
    for d in out:
        d["satisfied"] = (d.get("ready_asset_count") or 0) > 0
    return out


@router.get("/gaps")
async def domain_gaps(limit: int = 20):
    """Unsatisfied required domains ranked by the hypothesized value they block.

    The complement of /api/data-sources/recommendations: that ranks sources by
    what landing them unlocks, this ranks the semantic needs that are blocking
    value regardless of which product would satisfy them. A domain with zero
    serving assets is the strongest signal — nothing in the catalog can satisfy
    it, so it is a true acquisition gap rather than an ingestion backlog item.
    """
    from ..value_engine import compute_value_range, load_assumptions

    assumptions = await load_assumptions()
    rows = await db.fetch("""
        SELECT dd.id, dd.name, dd.label, dd.category, dd.description,
               COUNT(DISTINCT asd.data_asset_id) AS serving_asset_count,
               COUNT(DISTINCT asd.data_asset_id) FILTER (
                   WHERE da.ingestion_status IN ('curated','governed')
               ) AS ready_asset_count
        FROM data_domains dd
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE COALESCE(dd.is_active, true) = true
        GROUP BY dd.id
    """)
    unsatisfied = [dict(r) for r in rows if not (r["ready_asset_count"] or 0)]
    if not unsatisfied:
        return {"gaps": [], "total": 0,
                "summary": {"total_value_blocked_mm": 0.0, "domains_with_no_source": 0}}

    ids = [d["id"] for d in unsatisfied]
    dependents = await db.fetch("""
        SELECT urd.domain_id, uc.id, uc.title, uc.hypothesized_value_json, l.name AS lob_name
        FROM uc_requires_domain urd
        JOIN use_cases uc ON uc.id = urd.use_case_id
        LEFT JOIN lobs l ON l.id = uc.lob_id
        WHERE urd.domain_id = ANY($1::int[])
          AND urd.necessity = 'required'
          AND uc.in_portfolio = true
    """, ids)

    by_domain: dict[int, list] = {}
    for r in dependents:
        rng = compute_value_range(row_to_dict(r).get("hypothesized_value_json"), assumptions)
        by_domain.setdefault(r["domain_id"], []).append({
            "id": r["id"], "title": r["title"], "lob_name": r["lob_name"],
            "value_mm": round(rng["mid"], 2) if rng else 0.0,
        })

    out = []
    for d in unsatisfied:
        blocked = sorted(by_domain.get(d["id"], []), key=lambda x: -x["value_mm"])
        value_blocked = round(sum(b["value_mm"] for b in blocked), 2)
        no_source = (d["serving_asset_count"] or 0) == 0
        out.append({
            "domain": {"id": d["id"], "name": d["name"], "label": d["label"],
                       "category": d["category"], "description": d["description"]},
            "serving_asset_count": d["serving_asset_count"] or 0,
            "has_no_source": no_source,
            "blocked_use_cases": blocked[:10],
            "blocked_use_case_count": len(blocked),
            "value_blocked_mm": value_blocked,
            "rationale": _gap_rationale(d["label"], len(blocked), value_blocked, no_source),
        })
    # No serving source at all outranks "have it, haven't landed it" at equal value.
    out.sort(key=lambda x: (-x["value_blocked_mm"], -x["blocked_use_case_count"]))
    return {
        "gaps": out[:limit],
        "total": len(out),
        "summary": {
            "total_value_blocked_mm": round(sum(g["value_blocked_mm"] for g in out), 2),
            "domains_with_no_source": sum(1 for g in out if g["has_no_source"]),
        },
    }


def _gap_rationale(label: str, uc_count: int, value_mm: float, no_source: bool) -> str:
    if uc_count == 0:
        return (f"No source in the catalog provides {label}, but no portfolio use case "
                f"requires it yet — informational only.") if no_source else \
               f"{label} is not landed yet, but no portfolio use case requires it."
    plural = "s" if uc_count != 1 else ""
    if no_source:
        return (f"Nothing in the catalog provides {label} — {uc_count} use case{plural} "
                f"holding ~${value_mm:.0f}M/yr cannot proceed until a source is acquired.")
    return (f"{label} is available but not yet curated/governed — landing any serving "
            f"source unblocks {uc_count} use case{plural} worth ~${value_mm:.0f}M/yr.")


@router.get("/{domain_id}")
async def get_domain(domain_id: int):
    row = await db.fetchrow("SELECT * FROM data_domains WHERE id = $1", domain_id)
    if row is None:
        raise HTTPException(404, "Domain not found")
    domain = row_to_dict(row)
    domain["serving_assets"] = rows_to_list(await db.fetch("""
        SELECT da.id, da.source_category, da.vendor, da.module, da.ingestion_status,
               asd.confidence, asd.mapped_by, asd.notes, asd.is_user_edited
        FROM asset_serves_domain asd
        JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE asd.domain_id = $1
        ORDER BY da.source_category, da.module
    """, domain_id))
    domain["use_cases"] = rows_to_list(await db.fetch("""
        SELECT uc.id, uc.title, uc.status, urd.necessity, urd.rationale, urd.mapped_by
        FROM uc_requires_domain urd
        JOIN use_cases uc ON uc.id = urd.use_case_id
        WHERE urd.domain_id = $1
        ORDER BY uc.title
    """, domain_id))
    domain["satisfied"] = any(
        a["ingestion_status"] in READY for a in domain["serving_assets"])
    return domain


@router.post("")
async def create_domain(body: DomainIn, request: Request):
    _validate_category(body.category)
    actor = current_user(request)
    existing = await db.fetchrow("SELECT id FROM data_domains WHERE name = $1", body.name)
    if existing:
        raise HTTPException(409, f"A domain named '{body.name}' already exists")
    row = await db.fetchrow(
        """INSERT INTO data_domains
           (name, label, description, category, example_attributes, is_active,
            origin, is_user_edited)
           VALUES ($1,$2,$3,$4,$5,$6,'custom',true) RETURNING *""",
        body.name, body.label, body.description, body.category,
        body.example_attributes, body.is_active)
    if row is None:
        raise HTTPException(503, "Database unavailable")
    domain = row_to_dict(row)
    await write_audit("data_domain", domain["id"], "create", actor, body.model_dump())
    return domain


@router.put("/{domain_id}")
async def update_domain(domain_id: int, body: DomainIn, request: Request):
    _validate_category(body.category)
    actor = current_user(request)
    # is_user_edited=true pins the row against the discovery job's future writes.
    row = await db.fetchrow(
        """UPDATE data_domains SET
           name=$1, label=$2, description=$3, category=$4, example_attributes=$5,
           is_active=$6, is_user_edited=true, updated_at=now()
           WHERE id=$7 RETURNING *""",
        body.name, body.label, body.description, body.category,
        body.example_attributes, body.is_active, domain_id)
    if row is None:
        raise HTTPException(404, "Domain not found")
    await write_audit("data_domain", domain_id, "update", actor, body.model_dump())
    return row_to_dict(row)


@router.delete("/{domain_id}")
async def delete_domain(domain_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM data_domains WHERE id = $1", domain_id)
    await write_audit("data_domain", domain_id, "delete", actor)
    return {"deleted": res is not None}


@router.put("/{domain_id}/assets")
async def set_domain_assets(domain_id: int, body: DomainAssetsIn, request: Request):
    """Replace the set of assets that serve this domain. Written as mapped_by
    'manual' + is_user_edited so the normalization job never overwrites it."""
    if body.confidence not in _CONFIDENCE:
        raise HTTPException(422, f"confidence must be one of {sorted(_CONFIDENCE)}")
    exists = await db.fetchrow("SELECT id FROM data_domains WHERE id = $1", domain_id)
    if exists is None:
        raise HTTPException(404, "Domain not found")
    actor = current_user(request)
    await db.execute("DELETE FROM asset_serves_domain WHERE domain_id = $1", domain_id)
    for asset_id in dict.fromkeys(body.data_asset_ids):  # de-dupe, keep order
        await db.execute(
            """INSERT INTO asset_serves_domain
               (data_asset_id, domain_id, confidence, mapped_by, is_user_edited)
               VALUES ($1,$2,$3,'manual',true)
               ON CONFLICT (data_asset_id, domain_id) DO UPDATE
               SET confidence=$3, mapped_by='manual', is_user_edited=true,
                   mapped_at=now()""",
            asset_id, domain_id, body.confidence)
    await write_audit("data_domain", domain_id, "set_assets", actor,
                      {"data_asset_ids": body.data_asset_ids})
    return await get_domain(domain_id)


# ---------------------------------------------------------------------------
# Use-case side. Mounted on the same /api prefix but a different path root, so
# these live on their own router exported alongside the domains one.
# ---------------------------------------------------------------------------
uc_router = APIRouter(prefix="/use-cases", tags=["domains"])


@uc_router.get("/{use_case_id}/domains")
async def get_use_case_domains(use_case_id: int):
    """A use case's required domains, each with whether it is currently satisfied
    and which assets could satisfy it. This is what lets the UI say "you need
    work-order history; Maximo Work Order Management would satisfy it" instead of
    naming one specific module."""
    uc = await db.fetchrow(
        "SELECT id, title, domains_locked, requires_locked FROM use_cases WHERE id = $1",
        use_case_id)
    if uc is None:
        raise HTTPException(404, "Use case not found")
    rows = await db.fetch("""
        SELECT dd.id, dd.name, dd.label, dd.category, dd.description,
               urd.necessity, urd.rationale, urd.mapped_by, urd.manual,
               COUNT(DISTINCT asd.data_asset_id) AS serving_asset_count,
               COUNT(DISTINCT asd.data_asset_id) FILTER (
                   WHERE da.ingestion_status IN ('curated','governed')
               ) AS ready_asset_count,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'id', da.id,
                           'label', CONCAT(COALESCE(da.source_category, da.source_system), ' · ', da.module),
                           'ingestion_status', da.ingestion_status)
                       ORDER BY da.source_category, da.module
                   ) FILTER (WHERE da.id IS NOT NULL),
                   '[]'::jsonb
               ) AS serving_assets
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE urd.use_case_id = $1
        GROUP BY dd.id, urd.necessity, urd.rationale, urd.mapped_by, urd.manual
        ORDER BY urd.necessity, dd.label
    """, use_case_id)
    out = rows_to_list(rows)
    for d in out:
        d["satisfied"] = (d.get("ready_asset_count") or 0) > 0
    return {
        "use_case_id": use_case_id,
        "title": uc["title"],
        "domains_locked": uc["domains_locked"],
        "requires_locked": uc["requires_locked"],
        "domains": out,
        # Mirrors readiness.py's path precedence exactly (locked module
        # requirements win over declared domains) so callers can tell which model
        # is authoritative without recomputing it. Keep these two in sync.
        "requirement_model": "domain" if (
            not uc["requires_locked"]
            and any(d["necessity"] == "required" for d in out)
        ) else "module",
    }


@uc_router.put("/{use_case_id}/domains")
async def set_use_case_domains(use_case_id: int, body: UcDomainsIn, request: Request):
    """Replace a use case's required domains. Sets domains_locked by default so
    the extraction agent won't clobber hand-curated requirements — symmetric with
    requires_locked on the module path."""
    uc = await db.fetchrow("SELECT id FROM use_cases WHERE id = $1", use_case_id)
    if uc is None:
        raise HTTPException(404, "Use case not found")
    for item in body.domains:
        if item.necessity not in _NECESSITY:
            raise HTTPException(422, f"necessity must be one of {sorted(_NECESSITY)}")
    actor = current_user(request)
    await db.execute("DELETE FROM uc_requires_domain WHERE use_case_id = $1", use_case_id)
    seen: set[int] = set()
    for item in body.domains:
        if item.domain_id in seen:
            continue
        seen.add(item.domain_id)
        await db.execute(
            """INSERT INTO uc_requires_domain
               (use_case_id, domain_id, necessity, rationale, mapped_by, manual)
               VALUES ($1,$2,$3,$4,'manual',true)
               ON CONFLICT (use_case_id, domain_id) DO UPDATE
               SET necessity=$3, rationale=$4, mapped_by='manual', manual=true,
                   mapped_at=now()""",
            use_case_id, item.domain_id, item.necessity, item.rationale)
    if body.lock:
        await db.execute(
            "UPDATE use_cases SET domains_locked=true, updated_at=now() WHERE id=$1",
            use_case_id)
    await write_audit("use_case", use_case_id, "set_domains", actor,
                      {"domains": [d.model_dump() for d in body.domains],
                       "locked": body.lock})
    return await get_use_case_domains(use_case_id)
