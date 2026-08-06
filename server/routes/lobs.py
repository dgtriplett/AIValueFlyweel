"""Lines of Business CRUD."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/lobs", tags=["lobs"])


class LobIn(BaseModel):
    name: str
    description: str | None = None


@router.get("")
async def list_lobs():
    rows = await db.fetch("SELECT * FROM lobs ORDER BY id")
    return rows_to_list(rows)


@router.get("/{lob_id}")
async def get_lob(lob_id: int):
    row = await db.fetchrow("SELECT * FROM lobs WHERE id = $1", lob_id)
    if row is None:
        raise HTTPException(404, "LOB not found")
    return row_to_dict(row)


@router.post("")
async def create_lob(body: LobIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        "INSERT INTO lobs (name, description) VALUES ($1, $2) RETURNING *",
        body.name,
        body.description,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("lob", row["id"], "create", actor, body.model_dump())
    return row_to_dict(row)


@router.put("/{lob_id}")
async def update_lob(lob_id: int, body: LobIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        "UPDATE lobs SET name = $1, description = $2 WHERE id = $3 RETURNING *",
        body.name,
        body.description,
        lob_id,
    )
    if row is None:
        raise HTTPException(404, "LOB not found")
    await write_audit("lob", lob_id, "update", actor, body.model_dump())
    return row_to_dict(row)


@router.delete("/{lob_id}")
async def delete_lob(lob_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM lobs WHERE id = $1", lob_id)
    await write_audit("lob", lob_id, "delete", actor)
    return {"deleted": res is not None}
