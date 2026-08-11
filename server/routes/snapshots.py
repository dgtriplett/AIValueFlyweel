"""Portfolio trajectory.

    GET  /api/snapshots            the series, plus the change since the first point
    POST /api/snapshots            capture one now
    GET  /api/snapshots/current    today's figures WITHOUT storing them
    DELETE /api/snapshots/{id}     remove a bad point

WHY /current EXISTS SEPARATELY
------------------------------
It answers "what would a snapshot say" without writing one. That matters because the
capture endpoint is rate-limited and de-duplicated per minute — a UI that needed to
POST in order to display today's number would either be blocked by its own guard or
would pollute the series with a row per page view.

WHY DELETE EXISTS
-----------------
A snapshot taken mid-migration, or while an assumption was half-calibrated, is a
misleading point on a chart someone will show an executive. It cannot be corrected —
the state it recorded is gone — so removing it is the only honest option.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import accounts as acct
from .. import snapshots as snap
from ..common import current_user, rows_to_list, write_audit
from ..db import db
from ..limits import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


class CaptureIn(BaseModel):
    reason: str = Field(default=snap.REASON_MANUAL, max_length=60)
    detail: str | None = Field(default=None, max_length=500)


@router.get("")
async def list_snapshots(limit: int = Query(default=200, ge=2, le=2000)):
    """The series for the current account, oldest first, plus deltas.

    Oldest first because it is charted left to right; returning newest-first would push
    the reversal into every client.
    """
    account_id = await acct.current()
    try:
        rows = rows_to_list(await db.fetch("""
            SELECT id, captured_at, reason, detail, captured_by,
                   total_value_mm, buildable_value_mm, realized_value_mm,
                   shovel_ready, nearly_ready, awaiting_prereqs, blocked,
                   sources_total, sources_ready, domains_total, domains_satisfied,
                   use_cases_total, use_cases_live
            FROM value_snapshots
            WHERE account_id = $1 OR ($1 IS NULL AND account_id IS NULL)
            ORDER BY captured_at
            LIMIT $2
        """, account_id, limit))
    except Exception as exc:  # noqa: BLE001 - table absent before migration 011
        logger.info("snapshots unavailable (%s)", type(exc).__name__)
        return {"snapshots": [], "count": 0,
                "note": "Run scripts/migrate.py to enable the trend."}

    if not rows:
        return {"snapshots": [], "count": 0,
                "note": "No snapshots yet. Capture one to start the trend — it is "
                        "also captured automatically whenever a change moves the "
                        "portfolio's value."}

    first, last = rows[0], rows[-1]
    return {
        "snapshots": rows,
        "count": len(rows),
        "first_captured_at": first["captured_at"],
        "latest_captured_at": last["captured_at"],
        # The headline: what has changed over the whole recorded period. One point
        # yields all-zero deltas, which is correct rather than absent.
        "change_since_first": snap.delta(last, first),
        "latest": last,
    }


@router.get("/current")
async def current_metrics():
    """Today's figures, computed but NOT stored."""
    try:
        return {"metrics": await snap.compute_metrics(), "stored": False}
    except Exception as exc:  # noqa: BLE001
        logger.warning("metric computation failed (%s: %s)",
                       type(exc).__name__, exc)
        raise HTTPException(
            502, "Could not compute the portfolio figures. The database may be "
                 "mid-migration.") from exc


@router.post("", dependencies=[Depends(limiter("write"))])
async def capture_snapshot(body: CaptureIn, request: Request):
    """Capture a point now."""
    actor = current_user(request)
    try:
        result = await snap.capture(body.reason, detail=body.detail, actor=actor)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            502, f"Could not capture a snapshot: {type(exc).__name__}. Nothing was "
                 "stored.") from exc
    await write_audit("value_snapshot", result.get("id"), "capture", actor,
                      {"reason": body.reason})
    return result


@router.delete("/{snapshot_id}", dependencies=[Depends(limiter("write"))])
async def delete_snapshot(snapshot_id: int, request: Request):
    """Remove a misleading point.

    Scoped to the current account: a snapshot id from another tenant must not be
    deletable by guessing a number.
    """
    actor = current_user(request)
    account_id = await acct.current()
    row = await db.fetchrow("""
        DELETE FROM value_snapshots
        WHERE id = $1 AND (account_id = $2 OR ($2 IS NULL AND account_id IS NULL))
        RETURNING captured_at, reason
    """, snapshot_id, account_id)
    if row is None:
        raise HTTPException(404, "Snapshot not found for this account.")
    await write_audit("value_snapshot", snapshot_id, "delete", actor, dict(row))
    return {"deleted": True, "id": snapshot_id}
