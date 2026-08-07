"""Shared helpers for routes: user attribution, audit logging, serialization."""
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import Request

from .db import db

logger = logging.getLogger(__name__)


def current_user(request: Request) -> str:
    """Resolve the acting user from Databricks Apps headers.

    Databricks Apps inject X-Forwarded-Email / X-Forwarded-User. The frontend
    may also set X-Grid-Atlas-User for local dev (the legacy X-GridValue-User
    header is still accepted so the pre-rebrand SPA build keeps working).
    Falls back to 'system'.
    """
    for header in ("x-forwarded-email", "x-forwarded-user",
                   "x-grid-atlas-user", "x-gridvalue-user"):
        val = request.headers.get(header)
        if val:
            return val
    return "system"


def jsonable(value: Any) -> Any:
    """Convert asyncpg / DB types into JSON-serializable Python values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, str):
        # asyncpg returns jsonb columns as strings; try to parse.
        return value
    return value


def row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, str) and k.endswith("_json"):
            try:
                out[k] = json.loads(v)
                continue
            except (ValueError, TypeError):
                pass
        out[k] = jsonable(v)
    return out


def rows_to_list(rows) -> list:
    return [row_to_dict(r) for r in rows]


async def write_audit(entity_type: str, entity_id, action: str, actor: str, diff: dict | None = None) -> None:
    """Best-effort audit-log write; never raises to the caller."""
    try:
        await db.execute(
            "INSERT INTO audit_log (entity_type, entity_id, action, actor, diff_json) "
            "VALUES ($1, $2, $3, $4, $5::jsonb)",
            entity_type,
            entity_id,
            action,
            actor,
            json.dumps(diff or {}),
        )
    except Exception as exc:  # noqa: BLE001
        # A lost audit row must not fail the write it describes, but it does need to
        # be visible: the audit log is the only record of who changed what.
        logger.warning("audit write failed for %s on %s#%s (%s: %s)",
                       action, entity_type, entity_id, type(exc).__name__, exc)
