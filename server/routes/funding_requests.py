"""Funding requests CRUD (joint-funding conversations)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts
from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/funding-requests", tags=["funding_requests"])


class FundingIn(BaseModel):
    data_asset_id: int
    requesting_lob_id: int | None = None
    co_funding_lobs: list[int] = []
    combined_value: float | None = None
    status: str = "proposed"


@router.get("")
async def list_funding():
    account_id = await accounts.current()
    if account_id is not None:
        rows = await db.fetch(
            "SELECT * FROM funding_requests WHERE account_id=$1 ORDER BY id",
            account_id)
    else:
        rows = await db.fetch("SELECT * FROM funding_requests ORDER BY id")
    return rows_to_list(rows)


@router.get("/{req_id}")
async def get_funding(req_id: int):
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            "SELECT * FROM funding_requests WHERE id=$1 AND account_id=$2",
            req_id, account_id)
    else:
        row = await db.fetchrow("SELECT * FROM funding_requests WHERE id=$1", req_id)
    if row is None:
        raise HTTPException(404, "Funding request not found")
    return row_to_dict(row)


@router.post("")
async def create_funding(body: FundingIn, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            """INSERT INTO funding_requests
               (account_id, data_asset_id, requesting_lob_id, co_funding_lobs,
                combined_value, status)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            account_id, body.data_asset_id, body.requesting_lob_id,
            body.co_funding_lobs, body.combined_value, body.status)
    else:
        row = await db.fetchrow(
            """INSERT INTO funding_requests
               (data_asset_id, requesting_lob_id, co_funding_lobs, combined_value, status)
               VALUES ($1,$2,$3,$4,$5) RETURNING *""",
            body.data_asset_id, body.requesting_lob_id, body.co_funding_lobs,
            body.combined_value, body.status,
        )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("funding_request", row["id"], "create", actor, body.model_dump())
    return row_to_dict(row)


@router.put("/{req_id}")
async def update_funding(req_id: int, body: FundingIn, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            """UPDATE funding_requests SET
               data_asset_id=$1, requesting_lob_id=$2, co_funding_lobs=$3,
               combined_value=$4, status=$5
               WHERE id=$6 AND account_id=$7 RETURNING *""",
            body.data_asset_id, body.requesting_lob_id, body.co_funding_lobs,
            body.combined_value, body.status, req_id, account_id)
    else:
        row = await db.fetchrow(
            """UPDATE funding_requests SET
               data_asset_id=$1, requesting_lob_id=$2, co_funding_lobs=$3,
               combined_value=$4, status=$5 WHERE id=$6 RETURNING *""",
            body.data_asset_id, body.requesting_lob_id, body.co_funding_lobs,
            body.combined_value, body.status, req_id,
        )
    if row is None:
        raise HTTPException(404, "Funding request not found")
    await write_audit("funding_request", req_id, "update", actor, body.model_dump())
    return row_to_dict(row)


@router.delete("/{req_id}")
async def delete_funding(req_id: int, request: Request):
    actor = current_user(request)
    account_id = await accounts.current()
    if account_id is not None:
        res = await db.execute(
            "DELETE FROM funding_requests WHERE id=$1 AND account_id=$2",
            req_id, account_id)
    else:
        res = await db.execute("DELETE FROM funding_requests WHERE id=$1", req_id)
    await write_audit("funding_request", req_id, "delete", actor)
    return {"deleted": res is not None}
