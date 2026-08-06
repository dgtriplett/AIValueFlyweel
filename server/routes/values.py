"""Value records CRUD (hypothesized vs realized)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/value-records", tags=["value_records"])


class ValueIn(BaseModel):
    use_case_id: int
    kind: str
    metric_type: str | None = None
    amount: float | None = None
    unit: str | None = None
    fiscal_period: str | None = None
    confidence: str | None = None


@router.get("")
async def list_values(use_case_id: int | None = None):
    if use_case_id is not None:
        rows = await db.fetch("SELECT * FROM value_records WHERE use_case_id=$1 ORDER BY id", use_case_id)
    else:
        rows = await db.fetch("SELECT * FROM value_records ORDER BY id")
    return rows_to_list(rows)


@router.get("/{rec_id}")
async def get_value(rec_id: int):
    row = await db.fetchrow("SELECT * FROM value_records WHERE id=$1", rec_id)
    if row is None:
        raise HTTPException(404, "Value record not found")
    return row_to_dict(row)


@router.post("")
async def create_value(body: ValueIn, request: Request):
    if body.kind not in ("hypothesized", "realized"):
        raise HTTPException(422, "kind must be 'hypothesized' or 'realized'")
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO value_records
           (use_case_id, kind, metric_type, amount, unit, fiscal_period, confidence, created_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
        body.use_case_id, body.kind, body.metric_type, body.amount, body.unit,
        body.fiscal_period, body.confidence, actor,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("value_record", row["id"], "create", actor, body.model_dump())
    return row_to_dict(row)


@router.put("/{rec_id}")
async def update_value(rec_id: int, body: ValueIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        """UPDATE value_records SET
           use_case_id=$1, kind=$2, metric_type=$3, amount=$4, unit=$5,
           fiscal_period=$6, confidence=$7 WHERE id=$8 RETURNING *""",
        body.use_case_id, body.kind, body.metric_type, body.amount, body.unit,
        body.fiscal_period, body.confidence, rec_id,
    )
    if row is None:
        raise HTTPException(404, "Value record not found")
    await write_audit("value_record", rec_id, "update", actor, body.model_dump())
    return row_to_dict(row)


@router.delete("/{rec_id}")
async def delete_value(rec_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM value_records WHERE id=$1", rec_id)
    await write_audit("value_record", rec_id, "delete", actor)
    return {"deleted": res is not None}
