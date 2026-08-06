"""Roadmap items CRUD."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/roadmap-items", tags=["roadmap_items"])


class RoadmapIn(BaseModel):
    use_case_id: int
    horizon: str | None = None
    wave: int | None = None
    target_date: str | None = None
    completion_date: str | None = None
    notes: str | None = None


@router.get("")
async def list_roadmap(use_case_id: int | None = None):
    if use_case_id is not None:
        rows = await db.fetch("SELECT * FROM roadmap_items WHERE use_case_id=$1 ORDER BY wave, id", use_case_id)
    else:
        rows = await db.fetch("SELECT * FROM roadmap_items ORDER BY wave, id")
    return rows_to_list(rows)


@router.get("/{item_id}")
async def get_roadmap(item_id: int):
    row = await db.fetchrow("SELECT * FROM roadmap_items WHERE id=$1", item_id)
    if row is None:
        raise HTTPException(404, "Roadmap item not found")
    return row_to_dict(row)


@router.post("")
async def create_roadmap(body: RoadmapIn, request: Request):
    if body.horizon and body.horizon not in ("now", "next", "later"):
        raise HTTPException(422, "horizon must be 'now', 'next', or 'later'")
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO roadmap_items
           (use_case_id, horizon, wave, target_date, completion_date, notes)
           VALUES ($1,$2,$3,$4::date,$5::date,$6) RETURNING *""",
        body.use_case_id, body.horizon, body.wave, body.target_date,
        body.completion_date, body.notes,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("roadmap_item", row["id"], "create", actor, body.model_dump())
    return row_to_dict(row)


@router.put("/{item_id}")
async def update_roadmap(item_id: int, body: RoadmapIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        """UPDATE roadmap_items SET
           use_case_id=$1, horizon=$2, wave=$3, target_date=$4::date,
           completion_date=$5::date, notes=$6 WHERE id=$7 RETURNING *""",
        body.use_case_id, body.horizon, body.wave, body.target_date,
        body.completion_date, body.notes, item_id,
    )
    if row is None:
        raise HTTPException(404, "Roadmap item not found")
    await write_audit("roadmap_item", item_id, "update", actor, body.model_dump())
    return row_to_dict(row)


@router.delete("/{item_id}")
async def delete_roadmap(item_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM roadmap_items WHERE id=$1", item_id)
    await write_audit("roadmap_item", item_id, "delete", actor)
    return {"deleted": res is not None}
