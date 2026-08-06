"""Data Assets CRUD (source systems decomposed to module level)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
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


async def _attach_benefiting(asset: dict) -> dict:
    rows = await db.fetch(
        "SELECT lob_id FROM data_asset_lobs WHERE data_asset_id = $1 ORDER BY lob_id",
        asset["id"],
    )
    asset["benefiting_lob_ids"] = [r["lob_id"] for r in rows]
    return asset


@router.get("")
async def list_data_assets():
    rows = await db.fetch("SELECT * FROM data_assets ORDER BY id")
    assets = rows_to_list(rows)
    for a in assets:
        await _attach_benefiting(a)
    return assets


@router.get("/{asset_id}")
async def get_data_asset(asset_id: int):
    row = await db.fetchrow("SELECT * FROM data_assets WHERE id = $1", asset_id)
    if row is None:
        raise HTTPException(404, "Data asset not found")
    return await _attach_benefiting(row_to_dict(row))


@router.post("")
async def create_data_asset(body: DataAssetIn, request: Request):
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO data_assets
           (source_category, vendor, source_system, module, description, sub_vertical,
            ingestion_status, uc_catalog, uc_schema, owning_lob_id, origin, created_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'custom',$11) RETURNING *""",
        body.source_category, body.vendor, body.source_category, body.module, body.description,
        body.sub_vertical or "cross", body.ingestion_status, body.uc_catalog, body.uc_schema,
        body.owning_lob_id, actor,
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
    """One-click ingestion-status setter for the curated catalog checklist."""
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)
    row = await db.fetchrow(
        "UPDATE data_assets SET ingestion_status=$1, auto_captured=false, updated_at=now() "
        "WHERE id=$2 RETURNING *",
        body.ingestion_status, asset_id,
    )
    if row is None:
        raise HTTPException(404, "Data asset not found")
    await write_audit("data_asset", asset_id, "set_status", actor,
                      {"ingestion_status": body.ingestion_status})
    return await _attach_benefiting(row_to_dict(row))


@router.put("/{asset_id}")
async def update_data_asset(asset_id: int, body: DataAssetIn, request: Request):
    if body.ingestion_status not in _STATUSES:
        raise HTTPException(422, f"ingestion_status must be one of {_STATUSES}")
    actor = current_user(request)
    row = await db.fetchrow(
        """UPDATE data_assets SET
           source_category=$1, vendor=$2, source_system=$1, module=$3, description=$4,
           sub_vertical=COALESCE($5, sub_vertical, 'cross'), ingestion_status=$6,
           uc_catalog=$7, uc_schema=$8, owning_lob_id=$9, updated_at=now()
           WHERE id=$10 RETURNING *""",
        body.source_category, body.vendor, body.module, body.description, body.sub_vertical,
        body.ingestion_status, body.uc_catalog, body.uc_schema, body.owning_lob_id, asset_id,
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
    return await _attach_benefiting(row_to_dict(row))


@router.delete("/{asset_id}")
async def delete_data_asset(asset_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM data_assets WHERE id = $1", asset_id)
    await write_audit("data_asset", asset_id, "delete", actor)
    return {"deleted": res is not None}
