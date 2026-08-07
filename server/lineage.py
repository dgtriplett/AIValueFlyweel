"""Live Databricks integration — read system tables via the bound SQL warehouse.

Uses the Statement Execution API (service principal in-app, CLI profile locally).
Everything degrades gracefully if system tables / the warehouse aren't reachable.
"""

import aiohttp

from .config import DATABRICKS_WAREHOUSE_ID, get_oauth_token, get_workspace_host

WAREHOUSE_ID = DATABRICKS_WAREHOUSE_ID


async def run_sql(statement: str, timeout_s: int = 40) -> dict:
    """Execute SQL via the Statement Execution API. Returns
    {ok, columns, rows, error}. Never raises."""
    if not WAREHOUSE_ID:
        return {"ok": False, "error": "No SQL warehouse configured (set DATABRICKS_WAREHOUSE_ID / bind a warehouse resource).", "rows": [], "columns": []}
    host = get_workspace_host()
    try:
        token = get_oauth_token()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"auth failed: {exc}", "rows": [], "columns": []}

    url = f"{host}/api/2.0/sql/statements"
    payload = {"warehouse_id": WAREHOUSE_ID, "statement": statement, "wait_timeout": "30s"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=timeout_s) as resp:
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"request failed: {exc}", "rows": [], "columns": []}

    status = (data.get("status") or {}).get("state")
    if status != "SUCCEEDED":
        msg = (data.get("status") or {}).get("error", {}).get("message", f"state={status}")
        return {"ok": False, "error": msg, "rows": [], "columns": []}
    result = data.get("result", {})
    cols = [c["name"] for c in (data.get("manifest", {}).get("schema", {}).get("columns", []))]
    return {"ok": True, "columns": cols, "rows": result.get("data_array", []) or [], "error": None}


async def system_tables_available() -> dict:
    """Probe which system tables this workspace/warehouse can read."""
    checks = {
        "table_lineage": "SELECT 1 FROM system.access.table_lineage LIMIT 1",
        "lakeflow_jobs": "SELECT 1 FROM system.lakeflow.jobs LIMIT 1",
        "billing_usage": "SELECT 1 FROM system.billing.usage LIMIT 1",
        "serving": "SELECT 1 FROM system.serving.endpoint_usage LIMIT 1",
    }
    out = {}
    for name, sql in checks.items():
        r = await run_sql(sql, timeout_s=20)
        out[name] = r["ok"]
    return out


async def landed_uc_tables(catalog: str | None = None) -> set[str]:
    """Fully-qualified table names that appear as lineage TARGETS (i.e. have
    data written to them) — used to detect which registered sources are 'landed'.
    Uses a data-relative window so demos never show an empty result."""
    where = f"AND target_table_catalog = '{catalog}'" if catalog else ""
    sql = (
        "SELECT DISTINCT lower(concat_ws('.', target_table_catalog, target_table_schema, target_table_name)) AS t "
        "FROM system.access.table_lineage "
        "WHERE event_time > (SELECT max(event_time) - INTERVAL 90 DAYS FROM system.access.table_lineage) "
        f"AND target_table_name IS NOT NULL {where} LIMIT 5000"
    )
    r = await run_sql(sql)
    return {row[0] for row in r["rows"]} if r["ok"] else set()
