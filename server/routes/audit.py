"""Audit log endpoints — read-only access to audit_log (admin-only, Phase D).

    GET /api/audit          list recent audit_log rows (paginated, filterable)
    GET /api/audit/export.csv   stream filtered rows as CSV

ADMIN-ONLY, FAIL-CLOSED
-----------------------
The audit log names who did what to which records: viewing it is a privileged
operation. Every endpoint calls require_admin FIRST, so with no GRID_ATLAS_ADMINS
allowlist configured these endpoints 403 for everyone.

FILTERING & SEARCH
-----------------
GET /api/audit supports query params:
  - limit (default 100, cap 500): row count
  - entity_type, action, actor: exact-match filters (optional)
  - search: substring search over diff_json (case-insensitive, optional)

Rows are newest-first (created_at DESC).
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import accounts as acct
from ..common import rows_to_list
from ..db import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def get_audit_log(
    request: Request,
    limit: int = 100,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    search: Optional[str] = None,
):
    """Return recent audit_log rows, newest-first, with optional filters.

    Admin-gated: the audit log contains a record of who changed what, and viewing
    it is a privileged operation.

    Query params:
      - limit: row count (default 100, cap 500)
      - entity_type, action, actor: exact-match filters (optional)
      - search: substring search over diff_json::text (case-insensitive, optional)
    """
    acct.require_admin(request, "Viewing the audit log")

    # Cap limit to prevent excessively large queries
    limit = min(limit, 500)
    if limit < 1:
        raise HTTPException(422, "limit must be >= 1")

    # Build WHERE clause dynamically based on provided filters
    conditions = []
    params = []
    param_idx = 1

    if entity_type:
        conditions.append(f"entity_type = ${param_idx}")
        params.append(entity_type)
        param_idx += 1

    if action:
        conditions.append(f"action = ${param_idx}")
        params.append(action)
        param_idx += 1

    if actor:
        conditions.append(f"actor = ${param_idx}")
        params.append(actor)
        param_idx += 1

    if search:
        conditions.append(f"diff_json::text ILIKE ${param_idx}")
        params.append(f"%{search}%")
        param_idx += 1

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"""
        SELECT id, entity_type, entity_id, action, actor, diff_json, created_at
        FROM audit_log
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_idx}
    """
    params.append(limit)

    rows = await db.fetch(query, *params)
    return {"audit_log": rows_to_list(rows)}


@router.get("/export.csv")
async def export_audit_csv(
    request: Request,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    search: Optional[str] = None,
):
    """Stream filtered audit_log rows as CSV.

    Admin-gated: the audit log contains a record of who changed what, and viewing
    it is a privileged operation.

    Accepts the same filter params as GET /api/audit (entity_type, action, actor,
    search) but streams ALL matching rows (no limit cap) as CSV. Columns:
    created_at, actor, entity_type, entity_id, action, diff_json.
    """
    acct.require_admin(request, "Exporting the audit log")

    # Build WHERE clause (same logic as get_audit_log)
    conditions = []
    params = []
    param_idx = 1

    if entity_type:
        conditions.append(f"entity_type = ${param_idx}")
        params.append(entity_type)
        param_idx += 1

    if action:
        conditions.append(f"action = ${param_idx}")
        params.append(action)
        param_idx += 1

    if actor:
        conditions.append(f"actor = ${param_idx}")
        params.append(actor)
        param_idx += 1

    if search:
        conditions.append(f"diff_json::text ILIKE ${param_idx}")
        params.append(f"%{search}%")
        param_idx += 1

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"""
        SELECT created_at, actor, entity_type, entity_id, action, diff_json
        FROM audit_log
        {where_clause}
        ORDER BY created_at DESC
    """

    rows = await db.fetch(query, *params)

    # Generate CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at", "actor", "entity_type", "entity_id", "action", "diff_json"])

    for row in rows:
        writer.writerow([
            row["created_at"].isoformat() if row["created_at"] else "",
            row["actor"] or "",
            row["entity_type"] or "",
            row["entity_id"] or "",
            row["action"] or "",
            row["diff_json"] or "",
        ])

    csv_content = output.getvalue()
    output.close()

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"}
    )
