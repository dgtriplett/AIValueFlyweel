"""Asset taxonomy endpoints.

See server/taxonomy.py for why only three dimensions were imported and why rows
are effective-dated rather than updated in place.

    GET   /api/taxonomy/dimensions        the closed vocabularies
    GET   /api/taxonomy                   current classifications + distribution
    GET   /api/taxonomy/assets/{id}       one asset, with history
    PUT   /api/taxonomy/assets/{id}       hand-classify (supersedes AI)
    POST  /api/taxonomy/classify          AI-classify unlabelled assets
    GET   /api/taxonomy/coverage          how much of the catalog is labelled
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import taxonomy as tx
from ..common import current_user, rows_to_list, write_audit
from ..config import SERVING_ENDPOINT
from ..db import db

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])

# Assets per LLM call. Large enough to be cheap, small enough that the response
# stays inside a sane token budget and one bad batch doesn't lose much work.
BATCH_SIZE = 40


class ClassifyIn(BaseModel):
    # Re-classify assets that already have values (default: only unlabelled ones).
    include_classified: bool = False
    max_assets: int = 200


class AssetTaxonomyIn(BaseModel):
    integration_pattern: str | None = None
    criticality: str | None = None
    vendor_type: str | None = None


@router.get("/dimensions")
async def dimensions():
    """The vocabularies, plus the directional effort weights the UI explains
    ingest difficulty with."""
    return {
        "dimensions": [
            {"name": name, "values": list(tx.ALLOWED_VALUES[name])}
            for name in tx.DIMENSIONS
        ],
        "integration_effort": tx.INTEGRATION_EFFORT,
    }


@router.get("")
async def list_taxonomy(dimension: str | None = None):
    """Current classifications (effective_to IS NULL) with a distribution rollup."""
    if dimension is not None and dimension not in tx.DIMENSIONS:
        raise HTTPException(422, f"dimension must be one of {list(tx.DIMENSIONS)}")
    filter_sql = "AND at.dimension = $1" if dimension else ""
    args = [dimension] if dimension else []
    rows = await db.fetch(f"""
        SELECT at.data_asset_id, at.dimension, at.value, at.source, at.confidence,
               at.ai_reasoning, at.effective_from, at.created_by,
               da.source_category, da.module, da.ingestion_status
        FROM asset_taxonomy at
        JOIN data_assets da ON da.id = at.data_asset_id
        WHERE at.effective_to IS NULL {filter_sql}
        ORDER BY da.source_category, da.module, at.dimension
    """, *args)
    items = rows_to_list(rows)

    distribution: dict[str, dict[str, int]] = {d: {} for d in tx.DIMENSIONS}
    for item in items:
        bucket = distribution.setdefault(item["dimension"], {})
        bucket[item["value"]] = bucket.get(item["value"], 0) + 1
    return {"classifications": items, "distribution": distribution,
            "total": len(items)}


@router.get("/coverage")
async def coverage():
    """How much of the catalog is classified, per dimension.

    Coverage is the number a user actually needs: a distribution over 12 of 146
    assets looks authoritative and means nothing, so the UI needs to know how much
    of the catalog is behind it.
    """
    total_row = await db.fetchrow("SELECT count(*) AS n FROM data_assets")
    total = int(total_row["n"]) if total_row else 0
    rows = await db.fetch("""
        SELECT dimension, count(DISTINCT data_asset_id) AS n
        FROM asset_taxonomy WHERE effective_to IS NULL
        GROUP BY dimension
    """)
    classified = {r["dimension"]: int(r["n"]) for r in rows}
    fully_row = await db.fetchrow("""
        SELECT count(*) AS n FROM (
            SELECT data_asset_id FROM asset_taxonomy
            WHERE effective_to IS NULL
            GROUP BY data_asset_id
            HAVING count(DISTINCT dimension) = $1
        ) t
    """, len(tx.DIMENSIONS))
    return {
        "total_assets": total,
        "fully_classified": int(fully_row["n"]) if fully_row else 0,
        "by_dimension": {
            d: {"classified": classified.get(d, 0),
                "pct": round(100 * classified.get(d, 0) / total, 1) if total else 0.0}
            for d in tx.DIMENSIONS
        },
    }


@router.get("/assets/{asset_id}")
async def get_asset_taxonomy(asset_id: int):
    """One asset's current values plus superseded history."""
    asset = await db.fetchrow(
        "SELECT id, source_category, module, vendor, description, ingestion_status "
        "FROM data_assets WHERE id = $1", asset_id)
    if asset is None:
        raise HTTPException(404, "Data asset not found")
    rows = await db.fetch("""
        SELECT dimension, value, source, confidence, ai_reasoning,
               effective_from, effective_to, created_by
        FROM asset_taxonomy WHERE data_asset_id = $1
        ORDER BY dimension, effective_from DESC
    """, asset_id)
    history = rows_to_list(rows)
    current = {r["dimension"]: r for r in history if r["effective_to"] is None}
    return {
        "asset": dict(asset),
        "current": current,
        "history": [r for r in history if r["effective_to"] is not None],
        "suggested_ingest_effort": tx.INTEGRATION_EFFORT.get(
            (current.get("integration_pattern") or {}).get("value", "")),
    }


async def _supersede_and_insert(asset_id: int, dimension: str, value: str,
                                *, source: str, actor: str,
                                reasoning: str | None = None,
                                confidence: float | None = None) -> None:
    """Close the current row for this (asset, dimension) and insert the new one.

    Two statements rather than an UPDATE because the point is to keep history. The
    partial unique index on (data_asset_id, dimension) WHERE effective_to IS NULL
    means the close MUST happen first — otherwise the insert violates it, which is
    the constraint doing its job.
    """
    await db.execute(
        "UPDATE asset_taxonomy SET effective_to = now() "
        "WHERE data_asset_id = $1 AND dimension = $2 AND effective_to IS NULL",
        asset_id, dimension)
    await db.execute(
        """INSERT INTO asset_taxonomy
           (data_asset_id, dimension, value, source, confidence, ai_reasoning,
            created_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        asset_id, dimension, value, source, confidence, reasoning, actor)


@router.put("/assets/{asset_id}")
async def set_asset_taxonomy(asset_id: int, body: AssetTaxonomyIn, request: Request):
    """Hand-classify an asset. source='manual' outranks AI on re-runs."""
    asset = await db.fetchrow("SELECT id FROM data_assets WHERE id = $1", asset_id)
    if asset is None:
        raise HTTPException(404, "Data asset not found")

    submitted = {d: getattr(body, d) for d in tx.DIMENSIONS if getattr(body, d)}
    if not submitted:
        raise HTTPException(422, f"Provide at least one of {list(tx.DIMENSIONS)}")
    # Validate everything before writing anything, so a typo in the third field
    # doesn't leave the first two applied.
    validated = {}
    for dimension, value in submitted.items():
        try:
            validated[dimension] = tx.validate(dimension, value)
        except tx.TaxonomyError as exc:
            raise HTTPException(422, str(exc))

    actor = current_user(request)
    for dimension, value in validated.items():
        await _supersede_and_insert(asset_id, dimension, value,
                                    source="manual", actor=actor)
    await write_audit("data_asset", asset_id, "set_taxonomy", actor, validated)
    return await get_asset_taxonomy(asset_id)


@router.post("/classify")
async def classify(body: ClassifyIn, request: Request):
    """AI-classify assets across all three dimensions.

    Manual classifications are never overwritten: a human decision outranks the
    model's, and silently reverting one would make the feature untrustworthy.
    """
    actor = current_user(request)
    if body.include_classified:
        # Still exclude manually-set assets — 'include_classified' means "redo the
        # AI's work", not "overrule the humans".
        rows = await db.fetch("""
            SELECT da.* FROM data_assets da
            WHERE NOT EXISTS (
                SELECT 1 FROM asset_taxonomy at
                WHERE at.data_asset_id = da.id AND at.effective_to IS NULL
                  AND at.source = 'manual')
            ORDER BY da.id LIMIT $1
        """, body.max_assets)
    else:
        rows = await db.fetch("""
            SELECT da.* FROM data_assets da
            WHERE NOT EXISTS (
                SELECT 1 FROM asset_taxonomy at
                WHERE at.data_asset_id = da.id AND at.effective_to IS NULL)
            ORDER BY da.id LIMIT $1
        """, body.max_assets)

    assets = [dict(r) for r in rows]
    if not assets:
        return {"ok": True, "classified": 0, "batches": 0, "warnings": [],
                "detail": "Every asset is already classified."}

    from .agents import _llm_json

    total_written = 0
    warnings: list[str] = []
    used_llm_any = False
    batches = 0

    for start in range(0, len(assets), BATCH_SIZE):
        batch = assets[start:start + BATCH_SIZE]
        batches += 1
        parsed, used_llm, note = await _llm_json(
            tx.build_prompt(batch), max_tokens=4000,
            response_schema=tx.RESPONSE_SCHEMA)
        used_llm_any = used_llm_any or used_llm
        if note:
            warnings.append(note)
        classifications, batch_warnings = tx.parse_classifications(
            parsed, {a["id"] for a in batch})
        warnings.extend(batch_warnings)
        for row in classifications:
            for dimension, value in row["values"].items():
                await _supersede_and_insert(
                    row["asset_id"], dimension, value, source="ai", actor=actor,
                    reasoning=row["reasoning"])
                total_written += 1

    await write_audit("taxonomy", None, "classify", actor,
                      {"assets": len(assets), "values_written": total_written})
    return {
        "ok": True,
        "model": SERVING_ENDPOINT if used_llm_any else "none",
        "used_llm": used_llm_any,
        "assets_considered": len(assets),
        "values_written": total_written,
        "batches": batches,
        # De-duplicated: the same warning repeats once per batch otherwise.
        "warnings": list(dict.fromkeys(warnings)),
    }
