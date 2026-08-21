"""Cross-app catalog and roadmap synchronization packages.

Value Flywheel is the operational system of record for use cases, data needs,
dependencies, roadmap status, and value assumptions. These endpoints expose that
canonical package to companion apps and accept roadmap packages back from the
maturity assessment app without scraping UI state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts
from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db
from .. import portfolio
from ..roadmap_asset_resolution import canonical_asset_target
from ..readiness import readiness_map
from ..value_engine import compute_value_range, load_assumptions

router = APIRouter(prefix="/sync", tags=["sync"])


class RoadmapPackageIn(BaseModel):
    package: dict[str, Any]
    dry_run: bool = True


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clean_label(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


async def _optional_rows(sql: str, *args) -> list[dict]:
    try:
        return rows_to_list(await db.fetch(sql, *args))
    except Exception:
        return []


@router.get("/value-flywheel-catalog")
async def export_value_flywheel_catalog(scope: str = "all"):
    """Canonical package for the Data & AI Maturity Assessment app.

    `scope=all` exports the shared library plus the active portfolio. The
    receiving app keeps the Value Flywheel id as the external source id, so later
    runs update the same records.
    """
    if scope not in {"all", "portfolio", "catalog"}:
        raise HTTPException(422, "scope must be all|portfolio|catalog")

    where = ""
    params = []
    if scope == "portfolio":
        condition, params = await portfolio.portfolio_condition("uc")
        where = f"WHERE {condition}"
    elif scope == "catalog":
        where = "WHERE uc.origin = 'catalog'"

    rows = await db.fetch(f"""
        SELECT uc.*, l.name AS lob_name
        FROM use_cases uc
        LEFT JOIN lobs l ON l.id = uc.lob_id
        {where}
        ORDER BY uc.id
    """, *params)
    use_cases = rows_to_list(rows)
    assumptions = await load_assumptions()
    rmap = await readiness_map()

    req_assets = await _optional_rows("""
        SELECT ura.use_case_id, ura.data_asset_id, ura.criticality,
               da.source_category, da.vendor, da.source_system, da.module,
               da.description, da.sub_vertical, da.uc_catalog, da.uc_schema
        FROM uc_requires_asset ura
        JOIN data_assets da ON da.id = ura.data_asset_id
        ORDER BY ura.use_case_id, da.module
    """)
    req_domains = await _optional_rows("""
        SELECT urd.use_case_id, urd.domain_id, urd.necessity, urd.rationale,
               dd.name, dd.label, dd.description, dd.category
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        ORDER BY urd.use_case_id, dd.label
    """)
    enables = await _optional_rows("""
        SELECT from_use_case_id, to_use_case_id, detected_by_agent, rationale
        FROM uc_enables_uc
        ORDER BY from_use_case_id, to_use_case_id
    """)

    assets_by_uc: dict[int, list[dict]] = {}
    for row in req_assets:
        assets_by_uc.setdefault(row["use_case_id"], []).append(row)
    domains_by_uc: dict[int, list[dict]] = {}
    for row in req_domains:
        domains_by_uc.setdefault(row["use_case_id"], []).append(row)
    deps_by_uc: dict[int, list[int]] = {}
    for edge in enables:
        deps_by_uc.setdefault(edge["to_use_case_id"], []).append(edge["from_use_case_id"])

    for uc in use_cases:
        uc_id = uc["id"]
        rng = compute_value_range(uc.get("hypothesized_value_json"), assumptions)
        uc["computedValueMM"] = rng["mid"] if rng else None
        uc["valueRangeMM"] = rng
        uc["readiness"] = rmap.get(uc_id)
        uc["requiredDataAssets"] = assets_by_uc.get(uc_id, [])
        uc["requiredDataDomains"] = domains_by_uc.get(uc_id, [])
        uc["prerequisiteUseCaseIds"] = deps_by_uc.get(uc_id, [])

    account_id = await accounts.current()
    account_row = None
    if account_id is not None:
        account_row = row_to_dict(await db.fetchrow(
            "SELECT id, slug, name, utility_type FROM accounts WHERE id=$1",
            account_id,
        ))

    roadmap_where = ""
    roadmap_args: tuple = ()
    if account_id is not None:
        roadmap_where = "WHERE ri.account_id=$1"
        roadmap_args = (account_id,)
    roadmap = rows_to_list(await db.fetch(f"""
        SELECT ri.*, uc.title AS use_case_title
        FROM roadmap_items ri
        JOIN use_cases uc ON uc.id = ri.use_case_id
        {roadmap_where}
        ORDER BY ri.wave NULLS LAST, ri.id
    """, *roadmap_args))

    return {
        "schemaVersion": "grid-atlas.sync.v1",
        "packageType": "value_flywheel_canonical_catalog",
        "sourceApp": "grid-atlas",
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "account": account_row,
        "scope": scope,
        "useCases": use_cases,
        "dataAssets": rows_to_list(await db.fetch("SELECT * FROM data_assets ORDER BY id")),
        "dataDomains": await _optional_rows("SELECT * FROM data_domains ORDER BY id"),
        "requires": req_assets,
        "requiresDomains": req_domains,
        "enables": enables,
        "roadmapItems": roadmap,
        "valueAssumptions": rows_to_list(await db.fetch("""
            SELECT DISTINCT ON (key) *
            FROM value_assumptions
            WHERE account_id = $1 OR account_id IS NULL
            ORDER BY key, (account_id IS NULL)
        """, account_id)) if account_id is not None else rows_to_list(await db.fetch(
            "SELECT * FROM value_assumptions ORDER BY category, key"
        )),
    }


def _normalize_maturity_use_case(raw: dict[str, Any]) -> dict[str, Any]:
    title = raw.get("title") or raw.get("name") or raw.get("useCaseName") or "Untitled use case"
    value_model = raw.get("valueModel") or raw.get("hypothesized_value_json")
    return {
        "title": title,
        "description": raw.get("description") or "",
        "category": raw.get("category") or raw.get("domain") or "Roadmap",
        "stage": None,
        "phase": raw.get("phase") if isinstance(raw.get("phase"), int) else None,
        "status": "scoping",
        "effort_tshirt": raw.get("effort") or raw.get("effortTshirt"),
        "priority_score": raw.get("priorityScore"),
        "risk_tags": _as_list(raw.get("riskTags")),
        "compliance_tags": _as_list(raw.get("complianceTags")),
        "hypothesized_value_json": value_model,
    }


def _selected_current_use_case_ids(package: dict[str, Any]) -> set[str]:
    assessment = package.get("assessment") or {}
    answers = assessment.get("answers") or {}
    current = answers.get("currentUseCases") or {}
    return {str(x) for x in _as_list(current.get("selectedUseCases"))}


def _infra_status(package: dict[str, Any], label: str) -> str | None:
    """Infer readiness from maturity assessment infrastructure answers.

    This deliberately only upgrades strong infrastructure signals. Generic dataset
    requirements still start as not_started unless the use case was marked current.
    """
    assessment = package.get("assessment") or {}
    answers = assessment.get("answers") or {}
    infra = answers.get("infrastructure") or {}
    text_value = label.lower()
    checks = [
        (("erp", "sap", "oracle", "workday", "successfactors"), "sapIntegrated"),
        (("scada", "historian", "pi "), "scadaAccessible"),
        (("ami", "meter"), "amiDeployed"),
        (("gis", "geospatial"), "gisIntegrated"),
        (("catalog", "unity catalog", "governance"), "dataCatalog"),
        (("stream", "kafka", "event hub"), "streamingPipelines"),
    ]
    for needles, key in checks:
        if any(needle in text_value for needle in needles):
            answer = str(infra.get(key) or "").lower()
            if answer == "yes":
                return "governed"
            if answer == "partial":
                return "landed"
    if "bi workspace" in text_value or "dashboard" in text_value:
        if str(infra.get("cloudPlatform") or "").lower() in {"yes", "partial"}:
            return "curated"
    return None


async def _lookup_asset(label: str) -> int | None:
    clean = _clean_label(label)
    target = canonical_asset_target(clean)
    if target:
        existing = await db.fetchrow("""
            SELECT id FROM data_assets
            WHERE lower(source_category) = lower($1)
              AND lower(module) = lower($2)
            LIMIT 1
        """, target[0], target[1])
        if existing:
            return int(existing["id"])

    existing = await db.fetchrow("""
        SELECT id FROM data_assets
        WHERE lower(module) = lower($1)
           OR lower(source_system || ' ' || module) = lower($1)
           OR lower(source_category || ' ' || module) = lower($1)
        LIMIT 1
    """, clean)
    if existing:
        return int(existing["id"])
    return None


async def _set_account_asset_status(asset_id: int, status: str, actor: str) -> None:
    if status not in {"not_started", "landed", "curated", "governed"}:
        return
    account_id = await accounts.current()
    if account_id is None:
        await db.execute(
            "UPDATE data_assets SET ingestion_status=$1, updated_at=now() WHERE id=$2",
            status, asset_id)
        return
    await db.execute("""
        INSERT INTO account_asset_status
            (account_id, data_asset_id, ingestion_status, is_user_edited,
             updated_by, updated_at)
        VALUES ($1,$2,$3,false,$4,now())
        ON CONFLICT (account_id, data_asset_id) DO UPDATE SET
            ingestion_status = CASE
                WHEN CASE account_asset_status.ingestion_status
                    WHEN 'not_started' THEN 0 WHEN 'landed' THEN 1
                    WHEN 'curated' THEN 2 WHEN 'governed' THEN 3 ELSE 0 END
                  < CASE EXCLUDED.ingestion_status
                    WHEN 'not_started' THEN 0 WHEN 'landed' THEN 1
                    WHEN 'curated' THEN 2 WHEN 'governed' THEN 3 ELSE 0 END
                THEN EXCLUDED.ingestion_status
                ELSE account_asset_status.ingestion_status
            END,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
    """, account_id, asset_id, status, actor)


async def _import_use_case_requirements(
    local_id: int,
    raw: dict[str, Any],
    package: dict[str, Any],
    actor: str,
) -> dict[str, int]:
    """Persist maturity roadmap dataset labels as source edges.

    Returns counts for the import summary.
    """
    source_id = str(raw.get("id") or raw.get("externalId") or raw.get("name") or raw.get("title"))
    current_ids = _selected_current_use_case_ids(package)
    is_current = source_id in current_ids
    created_edges = 0
    touched_assets = 0
    unresolved = 0

    labels: list[str] = []
    labels.extend(_as_list(raw.get("dataRequirements")))
    labels.extend(_as_list(raw.get("systemPrerequisites")))

    seen: set[str] = set()
    for label in labels:
        clean = _clean_label(label)
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        asset_id = await _lookup_asset(clean)
        if asset_id is None:
            unresolved += 1
            continue
        touched_assets += 1
        edge = await db.fetchrow("""
            INSERT INTO uc_requires_asset
                (use_case_id, data_asset_id, criticality, manual)
            VALUES ($1,$2,'required',false)
            ON CONFLICT (use_case_id, data_asset_id) DO NOTHING
            RETURNING use_case_id
        """, local_id, asset_id)
        if edge:
            created_edges += 1
        status = "governed" if is_current else (_infra_status(package, clean) or "not_started")
        await _set_account_asset_status(asset_id, status, actor)

    if is_current:
        # Existing canonical Value Flywheel mappings should also be ready for use
        # cases the assessment says are already in production/current state.
        rows = await db.fetch(
            "SELECT data_asset_id FROM uc_requires_asset WHERE use_case_id=$1",
            local_id)
        for row in rows:
            await _set_account_asset_status(int(row["data_asset_id"]), "governed", actor)

    return {"assets": touched_assets, "edges": created_edges, "unresolved": unresolved}


async def _lookup_or_create_use_case(raw: dict[str, Any], source_app: str, actor: str) -> tuple[int, str]:
    account_id = await accounts.current()
    source_id = str(raw.get("id") or raw.get("externalId") or raw.get("name") or raw.get("title"))
    mapped = await db.fetchrow("""
        SELECT local_object_id FROM external_object_map
        WHERE account_id IS NOT DISTINCT FROM $1
          AND source_app=$2
          AND source_object_type='use_case'
          AND source_object_id=$3
          AND local_object_type='use_case'
    """, account_id, source_app, source_id)
    if mapped:
        return int(mapped["local_object_id"]), "updated"

    title = raw.get("title") or raw.get("name")
    existing = await db.fetchrow("SELECT id FROM use_cases WHERE lower(title)=lower($1) LIMIT 1", title)
    if existing:
        local_id = int(existing["id"])
        action = "mapped"
    else:
        uc = _normalize_maturity_use_case(raw)
        row = await db.fetchrow("""
            INSERT INTO use_cases
              (title, description, category, stage, phase, status, effort_tshirt,
               priority_score, risk_tags, compliance_tags, hypothesized_value_json,
               origin, in_portfolio, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,'auto',true,$12)
            RETURNING id
        """, uc["title"], uc["description"], uc["category"], uc["stage"], uc["phase"],
            uc["status"], uc["effort_tshirt"], uc["priority_score"], uc["risk_tags"],
            uc["compliance_tags"], json.dumps(uc["hypothesized_value_json"])
            if uc["hypothesized_value_json"] else None, actor)
        if row is None:
            raise HTTPException(503, "Database unavailable")
        local_id = int(row["id"])
        action = "created"

    await db.execute("""
        INSERT INTO external_object_map
          (account_id, source_app, source_object_type, source_object_id,
           local_object_type, local_object_id, source_hash, imported_by, imported_at)
        VALUES ($1,$2,'use_case',$3,'use_case',$4,$5,$6,now())
        ON CONFLICT (account_id, source_app, source_object_type, source_object_id, local_object_type)
        DO UPDATE SET local_object_id=EXCLUDED.local_object_id,
                      source_hash=EXCLUDED.source_hash,
                      imported_by=EXCLUDED.imported_by,
                      imported_at=now()
    """, account_id, source_app, source_id, str(local_id), _hash_payload(raw), actor)
    return local_id, action


@router.post("/maturity-roadmap/preview")
async def preview_maturity_roadmap(body: RoadmapPackageIn):
    package = body.package or {}
    use_cases = package.get("useCases") or package.get("use_cases") or []
    source_app = package.get("sourceApp") or "data-ai-maturity-assessment"
    mapped = 0
    creates = 0
    dataset_labels: set[str] = set()
    current_ids = _selected_current_use_case_ids(package)
    current_incoming = 0
    for raw in use_cases:
        source_id = str(raw.get("id") or raw.get("externalId") or raw.get("name") or raw.get("title"))
        if source_id in current_ids:
            current_incoming += 1
        for label in _as_list(raw.get("dataRequirements")) + _as_list(raw.get("systemPrerequisites")):
            clean = _clean_label(label)
            if clean:
                dataset_labels.add(clean.lower())
        row = await db.fetchrow("""
            SELECT 1 FROM external_object_map
            WHERE source_app=$1 AND source_object_type='use_case' AND source_object_id=$2
              AND local_object_type='use_case'
        """, source_app, source_id)
        if row:
            mapped += 1
        else:
            creates += 1
    return {
        "dryRun": True,
        "sourceApp": source_app,
        "useCases": {"incoming": len(use_cases), "mapped": mapped, "toCreateOrTitleMatch": creates},
        "datasets": {"incomingUnique": len(dataset_labels), "currentUseCases": current_incoming},
        "roadmapItems": len(package.get("roadmapItems") or package.get("roadmap") or []),
    }


@router.post("/maturity-roadmap/apply")
async def apply_maturity_roadmap(body: RoadmapPackageIn, request: Request):
    package = body.package or {}
    use_cases = package.get("useCases") or package.get("use_cases") or []
    if not use_cases:
        raise HTTPException(400, "Package has no useCases")
    actor = current_user(request)
    source_app = package.get("sourceApp") or "data-ai-maturity-assessment"
    id_map: dict[str, int] = {}
    summary = {"created": 0, "updated": 0, "mapped": 0, "datasets": 0, "requirementEdges": 0}
    summary["unresolvedDatasetLabels"] = 0
    for raw in use_cases:
        local_id, action = await _lookup_or_create_use_case(raw, source_app, actor)
        await portfolio.set_membership(
            local_id, True, actor=actor, source="maturity_roadmap_import")
        req_summary = await _import_use_case_requirements(local_id, raw, package, actor)
        summary["datasets"] += req_summary["assets"]
        summary["requirementEdges"] += req_summary["edges"]
        summary["unresolvedDatasetLabels"] += req_summary["unresolved"]
        id_map[str(raw.get("id") or raw.get("externalId") or raw.get("name") or raw.get("title"))] = local_id
        summary[action] = summary.get(action, 0) + 1
        if action in {"updated", "mapped"}:
            uc = _normalize_maturity_use_case(raw)
            await db.execute("""
                UPDATE use_cases SET
                  description=COALESCE($2, description),
                  category=COALESCE($3, category),
                  hypothesized_value_json=COALESCE($4::jsonb, hypothesized_value_json),
                  updated_at=now()
                WHERE id=$1
            """, local_id, uc["description"], uc["category"],
                json.dumps(uc["hypothesized_value_json"]) if uc["hypothesized_value_json"] else None)

    await write_audit("sync_package", None, "apply_maturity_roadmap", actor, {
        "sourceApp": source_app,
        "sourceAssessmentId": package.get("sourceAssessmentId"),
        "summary": summary,
    })
    return {"ok": True, "sourceApp": source_app, "summary": summary, "idMap": id_map}
