"""Data Assets CRUD (source systems decomposed to module level)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from .. import accounts, portfolio
from ..db import db

router = APIRouter(prefix="/data-assets", tags=["data_assets"])

_STATUSES = {"not_started", "landed", "curated", "governed"}


class DataAssetIn(BaseModel):
    source_category: str  # generic category drives (required)
    vendor: str | None = None  # optional product metadata
    module: str
    description: str | None = None
    sub_vertical: str | None = None
    ingestion_status: str = "not_started"
    uc_catalog: str | None = None
    uc_schema: str | None = None
    owning_lob_id: int | None = None
    benefiting_lob_ids: list[int] = []
    # PART B.2: new detail fields from migration 016
    provides: str | None = None
    refresh_cadence: str | None = None
    steward: str | None = None
    source_of_record: str | None = None


async def _attach_benefiting(asset: dict) -> dict:
    rows = await db.fetch(
        "SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1 ORDER BY lob_id",
        asset["id"],
    )
    asset["benefiting_lob_ids"] = [r["lob_id"] for r in rows]
    return asset


@router.get("")
async def list_data_assets():
    # The catalog row is shared; the STATUS is per-account. Overlaid here so the list
    # shows this customer's position rather than the catalog default — otherwise a new
    # account sees another tenant's landed sources in the very first screen.
    rows = await db.fetch("""
        SELECT da.*,
               COALESCE(acs.ingestion_status, da.ingestion_status) AS ingestion_status,
               COALESCE(acs.is_user_edited, false) AS status_user_edited
        FROM data_assets da
        LEFT JOIN account_asset_status acs
               ON acs.data_asset_id = da.id AND acs.account_id = $1
        ORDER BY da.id
    """, await accounts.current())
    assets = rows_to_list(rows)
    for a in assets:
        await _attach_benefiting(a)
    return assets


@router.get("/{asset_id}")
async def get_data_asset(asset_id: int):
    row = await db.fetchrow("""
        SELECT da.*,
               COALESCE(acs.ingestion_status, da.ingestion_status) AS ingestion_status,
               COALESCE(acs.is_user_edited, false) AS status_user_edited
        FROM data_assets da
        LEFT JOIN account_asset_status acs
               ON acs.data_asset_id = da.id AND acs.account_id = $2
        WHERE da.id = $1
    """, asset_id, await accounts.current())
    if row is None:
        raise HTTPException(404, "Data asset not found")
    asset = await _attach_benefiting(row_to_dict(row))

    # PART B.2(i): enrich with required_by = reverse join to use cases + readiness per UC
    # Apply use-case visibility to prevent cross-tenant leaks: only show catalog use cases
    # (shared) and use cases in the current account's portfolio (never another tenant's
    # custom use cases).
    visibility_condition, visibility_params = await portfolio.use_case_visibility("uc", param_index=2)
    required_by_rows = await db.fetch(f"""
        SELECT uc.id AS use_case_id,
               uc.title,
               ura.criticality,
               ura.rationale
        FROM uc_requires_asset ura
        JOIN use_cases uc ON uc.id = ura.use_case_id
        WHERE ura.data_asset_id = $1
          AND {visibility_condition}
        ORDER BY uc.title
    """, asset_id, *visibility_params)

    # Attach per-use-case readiness cheaply via the existing readiness_map
    from ..readiness import readiness_map
    rmap = await readiness_map()

    required_by = []
    for r in required_by_rows:
        uc_id = r["use_case_id"]
        entry = {
            "use_case_id": uc_id,
            "title": r["title"],
            "criticality": r["criticality"],
            "rationale": r["rationale"],
        }
        # Overlay readiness if available
        if uc_id in rmap:
            entry["readiness"] = rmap[uc_id].get("readiness")
        required_by.append(entry)

    asset["required_by"] = required_by
    return asset


@router.post("")
async def create_data_asset(body: DataAssetIn, request: Request):
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO data_assets
           (source_category, vendor, source_system, module, description, sub_vertical,
            ingestion_status, uc_catalog, uc_schema, owning_lob_id, origin, created_by,
            provides, refresh_cadence, steward, source_of_record)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'custom',$11,$12,$13,$14,$15) RETURNING *""",
        body.source_category, body.vendor, body.source_category, body.module, body.description,
        body.sub_vertical or "cross", body.ingestion_status, body.uc_catalog, body.uc_schema,
        body.owning_lob_id, actor,
        body.provides, body.refresh_cadence, body.steward, body.source_of_record,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    asset = row_to_dict(row)
    for lob_id in body.benefiting_lob_ids:
        await db.execute(
            "INSERT INTO data_asset_lobs (data_asset_id, lob_id) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING",
            asset["id"], lob_id,
        )
    await write_audit("data_asset", asset["id"], "create", actor, body.model_dump())
    return await _attach_benefiting(asset)


class StatusIn(BaseModel):
    ingestion_status: str


@router.patch("/{asset_id}/status")
async def set_status(asset_id: int, body: StatusIn, request: Request):
    """One-click ingestion-status setter for the curated catalog checklist.

    Writes to account_asset_status, NOT to data_assets. Whether a source is landed is
    a statement about ONE customer; data_assets is the shared catalog. Writing there
    would mean one utility marking their OMS governed did so for every tenant — the
    write-side twin of the read leak migration 010 fixed.

    `is_user_edited` is set so a later system-table sweep does not silently revert a
    human's judgement, which is the same rule the rest of the app follows.
    """
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)

    asset = await db.fetchrow("SELECT id FROM data_assets WHERE id = $1", asset_id)
    if asset is None:
        raise HTTPException(404, "Data asset not found")

    account_id = await accounts.current()
    if account_id is None:
        # Pre-migration install: the shared column is still the only place there is.
        await db.execute(
            "UPDATE data_assets SET ingestion_status=$1, auto_captured=false, "
            "updated_at=now() WHERE id=$2", body.ingestion_status, asset_id)
    else:
        await db.execute("""
            INSERT INTO account_asset_status
                (account_id, data_asset_id, ingestion_status, is_user_edited,
                 updated_by, updated_at)
            VALUES ($1,$2,$3,true,$4,now())
            ON CONFLICT (account_id, data_asset_id) DO UPDATE SET
                ingestion_status = EXCLUDED.ingestion_status,
                is_user_edited = true,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
        """, account_id, asset_id, body.ingestion_status, actor)

    await write_audit("data_asset", asset_id, "set_status", actor,
                      {"ingestion_status": body.ingestion_status,
                       "account_id": account_id})

    # Landing or unlanding a source changes readiness and therefore buildable value.
    # This is the event the trend chart exists to record.
    from .. import snapshots as snap
    await snap.capture_quietly(
        snap.REASON_SOURCE, actor=actor,
        detail=f"source {asset_id} set to {body.ingestion_status}")

    fresh = await db.fetchrow("SELECT * FROM data_assets WHERE id = $1", asset_id)
    result = await _attach_benefiting(row_to_dict(fresh))
    # Report the ACCOUNT's status, not the catalog default, or the UI would show the
    # value it just overwrote.
    result["ingestion_status"] = body.ingestion_status
    return result


@router.put("/{asset_id}")
async def update_data_asset(asset_id: int, body: DataAssetIn, request: Request):
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            """UPDATE data_assets SET
               source_category=$1, vendor=$2, source_system=$1, module=$3,
               description=$4, sub_vertical=COALESCE($5, sub_vertical, 'cross'),
               uc_catalog=$6, uc_schema=$7,
               provides=$9, refresh_cadence=$10, steward=$11, source_of_record=$12,
               updated_at=now()
               WHERE id=$8 RETURNING *""",
            body.source_category, body.vendor, body.module, body.description,
            body.sub_vertical, body.uc_catalog, body.uc_schema, asset_id,
            body.provides, body.refresh_cadence, body.steward, body.source_of_record)
        if row is not None:
            await db.execute("""
                INSERT INTO account_asset_status
                    (account_id, data_asset_id, ingestion_status, owning_lob_id,
                     is_user_edited, updated_by, updated_at)
                VALUES ($1,$2,$3,$4,true,$5,now())
                ON CONFLICT (account_id, data_asset_id) DO UPDATE SET
                    ingestion_status = EXCLUDED.ingestion_status,
                    owning_lob_id = EXCLUDED.owning_lob_id,
                    is_user_edited = true,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
            """, account_id, asset_id, body.ingestion_status, body.owning_lob_id,
                actor)
    else:
        row = await db.fetchrow(
            """UPDATE data_assets SET
               source_category=$1, vendor=$2, source_system=$1, module=$3, description=$4,
               sub_vertical=COALESCE($5, sub_vertical, 'cross'), ingestion_status=$6,
               uc_catalog=$7, uc_schema=$8, owning_lob_id=$9,
               provides=$11, refresh_cadence=$12, steward=$13, source_of_record=$14,
               updated_at=now()
               WHERE id=$10 RETURNING *""",
            body.source_category, body.vendor, body.module, body.description, body.sub_vertical,
            body.ingestion_status, body.uc_catalog, body.uc_schema, body.owning_lob_id, asset_id,
            body.provides, body.refresh_cadence, body.steward, body.source_of_record,
        )
    if row is None:
        raise HTTPException(404, "Data asset not found")
    await db.execute("DELETE FROM data_asset_lobs WHERE data_asset_id = $1", asset_id)
    for lob_id in body.benefiting_lob_ids:
        await db.execute(
            "INSERT INTO data_asset_lobs (data_asset_id, lob_id) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING",
            asset_id, lob_id,
        )
    await write_audit("data_asset", asset_id, "update", actor, body.model_dump())
    result = await _attach_benefiting(row_to_dict(row))
    if account_id is not None:
        result["ingestion_status"] = body.ingestion_status
        result["owning_lob_id"] = body.owning_lob_id
    return result


@router.delete("/{asset_id}")
async def delete_data_asset(asset_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM data_assets WHERE id = $1", asset_id)
    await write_audit("data_asset", asset_id, "delete", actor)
    return {"deleted": res is not None}
