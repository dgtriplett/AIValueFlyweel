"""Catalog quality checks for use-case, value, and source-mapping accuracy."""
from __future__ import annotations

import json

from fastapi import APIRouter

from ..common import rows_to_list
from ..db import db
from ..value_engine import compute_value_range, load_assumptions

router = APIRouter(prefix="/quality", tags=["quality"])


def _formula(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


@router.get("/catalog")
async def catalog_quality():
    """Return data-quality findings that affect portfolio credibility.

    This is intentionally read-only and cheap. It does not judge whether a use case
    is a good business idea; it flags internal evidence gaps that make the catalog
    hard to defend: missing formulas, missing assumption keys, missing domain
    requirements, source modules that cannot satisfy any domain, and value claims
    large enough to deserve human review.
    """
    assumptions = await load_assumptions()
    assumption_keys = set(assumptions)
    revenue = float(assumptions.get("annualRevenueMM") or 0)

    use_cases = rows_to_list(await db.fetch(
        "SELECT id, title, hypothesized_value_json FROM use_cases ORDER BY id"))
    domains = await db.fetch(
        "SELECT use_case_id, count(*) AS n FROM uc_requires_domain GROUP BY use_case_id")
    domain_counts = {r["use_case_id"]: int(r["n"] or 0) for r in domains}
    asset_domains = await db.fetch(
        "SELECT data_asset_id, count(*) AS n FROM asset_serves_domain GROUP BY data_asset_id")
    asset_domain_counts = {r["data_asset_id"]: int(r["n"] or 0) for r in asset_domains}
    assets = rows_to_list(await db.fetch(
        "SELECT id, source_category, module FROM data_assets ORDER BY id"))

    missing_formulas = []
    missing_assumptions = []
    no_required_domains = []
    high_value_claims = []

    for uc in use_cases:
        formula = _formula(uc.get("hypothesized_value_json"))
        components = formula.get("components") or []
        if not components:
            missing_formulas.append({"id": uc["id"], "title": uc["title"]})
        used_keys = []
        for component in components:
            used_keys.extend(component.get("assumptionKeys") or [])
        missing = sorted(set(used_keys) - assumption_keys)
        if missing:
            missing_assumptions.append({
                "id": uc["id"], "title": uc["title"], "missing_keys": missing,
            })
        if domain_counts.get(uc["id"], 0) == 0:
            no_required_domains.append({"id": uc["id"], "title": uc["title"]})
        value = (compute_value_range(formula, assumptions) or {}).get("mid") or 0
        if revenue and value > revenue * 0.006:
            high_value_claims.append({
                "id": uc["id"],
                "title": uc["title"],
                "value_mm": round(value, 2),
                "share_of_revenue_pct": round(100 * value / revenue, 3),
            })

    unmapped_assets = [{
        "id": asset["id"],
        "label": f"{asset.get('source_category') or ''} · {asset.get('module') or ''}",
    } for asset in assets if asset_domain_counts.get(asset["id"], 0) == 0]

    findings = {
        "missing_value_formulas": missing_formulas,
        "missing_assumption_keys": missing_assumptions,
        "use_cases_without_domain_requirements": no_required_domains,
        "assets_without_domain_mappings": unmapped_assets,
        "high_value_claims": sorted(
            high_value_claims, key=lambda r: r["value_mm"], reverse=True),
    }
    return {
        "status": "ok" if not any(findings.values()) else "review_needed",
        "counts": {key: len(value) for key, value in findings.items()},
        "findings": findings,
    }
