"""Setup status + preflight probes.

WHY PROBE AT ALL
----------------
Every one of this app's dependencies — Lakebase, a SQL warehouse, Unity Catalog,
a serving endpoint — fails in the same unhelpful way when a grant is missing: the
feature that needs it returns a 403 with a message about the specific SQL that was
denied, at whatever moment the user happened to click something. That turns a
five-minute permission fix into a support conversation.

So instead we actively check each dependency up front and, on failure, hand back
the exact GRANT statements a metastore admin needs to run. The probes are
read-only and cheap (SELECT 1, SHOW SCHEMAS, a one-token ai_query).

WHAT "READY" MEANS
------------------
Checks are split into REQUIRED and OPTIONAL. The app is usable with only Lakebase
and its seed data — the discovery layer, the Genie mirror, and the agents each
degrade to a clear disabled state rather than breaking the portfolio. `ready`
therefore reflects only the required set, so an install isn't reported broken for
lacking a feature the customer never asked for.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import config
from ..common import current_user
from ..db import db
from ..lineage import run_sql

# Settings are referenced through the `config` module rather than copied into
# module-level names on import. `from ..config import X` snapshots the value, which
# then disagrees with helpers like config.discovery_configured() that read the
# module's own globals — so the probe and the feature it is probing could reach
# different conclusions. Going through the module keeps one source of truth.

router = APIRouter(prefix="/setup", tags=["setup"])

# Probe order matches dependency order, so the first red pill is the root cause
# rather than a downstream symptom.
CHECK_ORDER = ("config", "identity", "lakebase", "schema", "seed",
               "warehouse", "serving", "discovery", "system_tables", "genie")

REQUIRED_CHECKS = ("config", "lakebase", "schema", "seed")


def _check(name: str, ok: bool, label: str, detail: str = "",
           required: bool = False, fix: str | None = None,
           grants: list[str] | None = None) -> dict:
    return {"name": name, "ok": ok, "label": label, "detail": detail,
            "required": required, "fix": fix, "grants": grants or []}


async def _service_principal() -> str:
    """The identity to name in GRANT statements.

    In-app this is the app's own managed service principal; locally it is the
    developer's CLI user. Falling back to a placeholder keeps the generated SQL
    copy-pasteable-with-one-edit rather than absent.
    """
    try:
        client = config.get_workspace_client()
        me = client.current_user.me()
        return me.user_name or me.display_name or "<app-service-principal>"
    except Exception:  # noqa: BLE001 - identity lookup must never break the page
        return "<app-service-principal>"


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------
def _probe_config() -> dict:
    """Environment variables that must be set for the app to work at all."""
    missing = []
    import os
    for var in ("PGHOST", "PGUSER"):
        if not os.environ.get(var):
            missing.append(var)
    if not config.DATABRICKS_WAREHOUSE_ID:
        missing.append("DATABRICKS_WAREHOUSE_ID")
    if missing:
        return _check(
            "config", False, "Configuration",
            f"Not set: {', '.join(missing)}.", required=True,
            fix="Set these in app.yaml (or bind the matching app resource) and "
                "redeploy. scripts/deploy.py fills them in for you.")
    return _check("config", True, "Configuration",
                  "Lakebase and warehouse settings are present.", required=True)


async def _probe_lakebase() -> dict:
    pool = await db.get_pool()
    if pool is None:
        return _check(
            "lakebase", False, "Lakebase", "No connection — running in demo mode.",
            required=True,
            fix="Check PGHOST/PGUSER/PGDATABASE in app.yaml and that the app's "
                "service principal has CAN_CONNECT on the Lakebase database "
                "(bind it as a `database` app resource).")
    try:
        row = await db.fetchrow("SELECT 1 AS ok")
        if row is None:
            raise RuntimeError("query returned nothing")
        return _check("lakebase", True, "Lakebase", "Connected.", required=True)
    except Exception as exc:  # noqa: BLE001
        return _check("lakebase", False, "Lakebase", f"Query failed: {exc}",
                      required=True,
                      fix="The app connected but cannot query. Confirm the service "
                          "principal has DML on the app schema.")


async def _probe_schema() -> dict:
    """Are the portfolio tables present? Distinguishes "no schema" from "no seed"."""
    try:
        row = await db.fetchrow("""
            SELECT count(*) AS n FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('use_cases','data_assets','lobs','data_domains',
                                 'confirm_tokens')
        """)
        found = int(row["n"]) if row else 0
        if found >= 5:
            return _check("schema", True, "Schema", "All core tables exist.",
                          required=True)
        return _check(
            "schema", False, "Schema",
            f"Only {found} of 5 core tables found.", required=True,
            fix="Run the seed as the Lakebase owner: "
                "python3 scripts/seed_clean.py --profile <profile> "
                "--project <lakebase-project> --db app")
    except Exception as exc:  # noqa: BLE001
        return _check("schema", False, "Schema", f"Check failed: {exc}", required=True)


async def _probe_seed() -> dict:
    """Reference content present? An empty portfolio is a working app with
    nothing to show, which looks broken to a first-time user."""
    try:
        row = await db.fetchrow("""
            SELECT (SELECT count(*) FROM use_cases)    AS use_cases,
                   (SELECT count(*) FROM data_assets)  AS data_assets,
                   (SELECT count(*) FROM data_domains) AS domains,
                   (SELECT count(*) FROM value_assumptions) AS assumptions
        """)
        if row is None:
            raise RuntimeError("no result")
        counts = dict(row)
        if counts["use_cases"] and counts["data_assets"] and counts["assumptions"]:
            detail = (f"{counts['use_cases']} use cases, "
                      f"{counts['data_assets']} data assets, "
                      f"{counts['domains']} data domains.")
            # Domains empty is a supported state (readiness falls back to the
            # module path), so flag it without failing the check.
            if not counts["domains"]:
                detail += " No data domains — readiness will use module requirements."
            return _check("seed", True, "Reference data", detail, required=True)
        return _check(
            "seed", False, "Reference data", "Portfolio is empty.", required=True,
            fix="Load the reference library: python3 scripts/seed_clean.py "
                "--profile <profile> --project <lakebase-project> --db app")
    except Exception as exc:  # noqa: BLE001
        return _check("seed", False, "Reference data", f"Check failed: {exc}",
                      required=True)


async def _probe_warehouse(sp: str) -> dict:
    if not config.DATABRICKS_WAREHOUSE_ID:
        return _check("warehouse", False, "SQL warehouse", "No warehouse configured.",
                      fix="Bind a `sql_warehouse` app resource named 'sql-warehouse'.")
    result = await run_sql("SELECT 1", timeout_s=30)
    if result["ok"]:
        return _check("warehouse", True, "SQL warehouse",
                      f"Reachable ({config.DATABRICKS_WAREHOUSE_ID}).")
    return _check(
        "warehouse", False, "SQL warehouse", result.get("error") or "unreachable",
        fix="Grant the app's service principal CAN_USE on the warehouse "
            "(SQL Warehouses -> Permissions), and confirm it is not stopped.",
        grants=[f"-- In the Databricks UI: SQL Warehouses -> "
                f"{config.DATABRICKS_WAREHOUSE_ID} -> Permissions -> add CAN USE for `{sp}`"])


async def _probe_serving(sp: str) -> dict:
    """One-token ai_query. Cheapest possible proof the endpoint is queryable."""
    result = await run_sql(
        f"SELECT ai_query('{config.SERVING_ENDPOINT}', 'Reply with OK', "
        "modelParameters => named_struct('max_tokens', 1)) AS probe", timeout_s=60)
    if result["ok"]:
        return _check("serving", True, "Foundation Model",
                      f"{config.SERVING_ENDPOINT} is queryable.")
    return _check(
        "serving", False, "Foundation Model",
        result.get("error") or "ai_query failed",
        fix=f"Grant the app's service principal CAN_QUERY on {config.SERVING_ENDPOINT}. "
            "AI agents fall back to heuristics without it — the portfolio still works.",
        grants=[f"-- In the Databricks UI: Serving -> {config.SERVING_ENDPOINT} -> "
                f"Permissions -> add CAN QUERY for `{sp}`"])


async def _probe_discovery(sp: str) -> dict:
    if not config.discovery_configured():
        return _check(
            "discovery", False, "Discovery catalog",
            "ATLAS_CATALOG is not set — workspace discovery is disabled.",
            fix="Set ATLAS_CATALOG (and optionally ATLAS_SCHEMA) in app.yaml to a "
                "catalog the app may write to, then redeploy. The curated "
                "146-module catalog works without this.")
    result = await run_sql(f"SHOW SCHEMAS IN `{config.ATLAS_CATALOG}`", timeout_s=40)
    if not result["ok"]:
        return _check(
            "discovery", False, "Discovery catalog",
            result.get("error") or "cannot list schemas",
            fix=f"Grant the app's service principal access to `{config.ATLAS_CATALOG}`.",
            grants=_discovery_grants(sp))
    # Listing isn't enough — ingestion needs to CREATE and MODIFY.
    probe = await run_sql(
        f"CREATE SCHEMA IF NOT EXISTS `{config.ATLAS_CATALOG}`.`{config.ATLAS_SCHEMA}`", timeout_s=40)
    if not probe["ok"]:
        return _check(
            "discovery", False, "Discovery catalog",
            f"Can read `{config.ATLAS_CATALOG}` but cannot create the discovery schema: "
            f"{probe.get('error')}",
            fix="The app needs CREATE_SCHEMA on the catalog (or an existing schema "
                "it can CREATE TABLE in).",
            grants=_discovery_grants(sp))
    return _check("discovery", True, "Discovery catalog",
                  f"`{config.ATLAS_CATALOG}`.`{config.ATLAS_SCHEMA}` is writable.")


def _discovery_grants(sp: str) -> list[str]:
    return [
        f"GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `{config.ATLAS_CATALOG}` TO `{sp}`;",
        f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE "
        f"ON SCHEMA `{config.ATLAS_CATALOG}`.`{config.ATLAS_SCHEMA}` TO `{sp}`;",
    ]


async def _probe_system_tables() -> dict:
    """Lineage/usage reads. Optional: they only power auto-reconcile."""
    result = await run_sql(
        "SELECT 1 FROM system.access.table_lineage LIMIT 1", timeout_s=30)
    if result["ok"]:
        return _check("system_tables", True, "System tables",
                      "system.access.table_lineage is readable.")
    return _check(
        "system_tables", False, "System tables",
        result.get("error") or "not readable",
        fix="Optional. Grant SELECT on the system.access schema to enable "
            "auto-detection of landed sources from lineage.",
        grants=["GRANT SELECT ON SCHEMA system.access TO `%s`;" % await _service_principal()])


async def _probe_genie(sp: str) -> dict:
    if not config.GENIE_SPACE_ID:
        return _check(
            "genie", False, "Genie space", "GENIE_SPACE_ID is not set.",
            fix="Optional. Create a Genie space over the portfolio mirror and set "
                "config.GENIE_SPACE_ID. See INSTALL.md.")
    mirror = f"`{config.GENIE_MIRROR_CATALOG}`.`{config.GENIE_MIRROR_SCHEMA}`"
    result = await run_sql(f"SHOW TABLES IN {mirror}", timeout_s=40)
    if result["ok"]:
        return _check("genie", True, "Genie space",
                      f"Space configured; mirror {mirror} is readable.")
    return _check(
        "genie", False, "Genie space",
        f"Space is set but the mirror is unreadable: {result.get('error')}",
        fix="Grant the app write access to the mirror schema, then run "
            "Sync → Genie mirror.",
        grants=[
            f"GRANT USE CATALOG ON CATALOG `{config.GENIE_MIRROR_CATALOG}` TO `{sp}`;",
            f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA {mirror} TO `{sp}`;",
        ])


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
@router.get("/status")
async def status(request: Request):
    """Every probe at once — this drives the setup banner.

    Runs sequentially rather than concurrently: the probes share one SQL warehouse,
    and firing five statements at a cold warehouse is slower than reusing the
    connection the first one warmed up.
    """
    sp = await _service_principal()
    checks = [
        _probe_config(),
        _check("identity", True, "Identity",
               f"Running as {sp}" + (" (app service principal)." if config.IS_DATABRICKS_APP
                                     else " (local CLI profile).")),
        await _probe_lakebase(),
    ]
    # Schema and seed only make sense once Lakebase answers.
    if checks[-1]["ok"]:
        checks.append(await _probe_schema())
        checks.append(await _probe_seed() if checks[-1]["ok"] else _check(
            "seed", False, "Reference data", "Skipped — schema is missing.",
            required=True))
    else:
        checks.append(_check("schema", False, "Schema",
                             "Skipped — no Lakebase connection.", required=True))
        checks.append(_check("seed", False, "Reference data",
                             "Skipped — no Lakebase connection.", required=True))

    warehouse = await _probe_warehouse(sp)
    checks.append(warehouse)
    # Everything below needs the warehouse, so skip rather than emit four
    # identical downstream failures.
    if warehouse["ok"]:
        checks.append(await _probe_serving(sp))
        checks.append(await _probe_discovery(sp))
        checks.append(await _probe_system_tables())
        checks.append(await _probe_genie(sp))
    else:
        for name, label in (("serving", "Foundation Model"),
                            ("discovery", "Discovery catalog"),
                            ("system_tables", "System tables"),
                            ("genie", "Genie space")):
            checks.append(_check(name, False, label,
                                 "Skipped — the SQL warehouse is unreachable."))

    by_name = {c["name"]: c for c in checks}
    ordered = [by_name[n] for n in CHECK_ORDER if n in by_name]
    required_failures = [c for c in ordered if c["required"] and not c["ok"]]
    optional_failures = [c for c in ordered if not c["required"] and not c["ok"]]

    # Collect grants once, de-duplicated, so the UI can offer a single copy button.
    all_grants: list[str] = []
    for check in ordered:
        for grant in check["grants"]:
            if grant not in all_grants:
                all_grants.append(grant)

    return {
        "ready": not required_failures,
        "checks": ordered,
        "summary": {
            "total": len(ordered),
            "passing": sum(1 for c in ordered if c["ok"]),
            "required_failing": len(required_failures),
            "optional_failing": len(optional_failures),
        },
        "service_principal": sp,
        "grants_sql": all_grants,
        "environment": "databricks" if config.IS_DATABRICKS_APP else "local",
        # What to do next, in dependency order — the first required failure is the
        # root cause, not a symptom of something further down.
        "next_action": (required_failures[0]["fix"] if required_failures
                        else (optional_failures[0]["fix"] if optional_failures else None)),
    }


@router.get("/grants")
async def grants():
    """Just the GRANT statements, for handing to a metastore admin."""
    sp = await _service_principal()
    lines = [
        f"-- Grid Atlas: privileges for the app service principal `{sp}`.",
        "-- Run as a metastore admin or catalog owner.",
        "",
    ]
    if config.discovery_configured():
        lines += ["-- Discovery layer (workspace inventory + AI enrichment):"]
        lines += _discovery_grants(sp)
        lines.append("")
    if config.GENIE_MIRROR_CATALOG:
        mirror = f"`{config.GENIE_MIRROR_CATALOG}`.`{config.GENIE_MIRROR_SCHEMA}`"
        lines += [
            "-- Genie mirror (optional — natural-language Q&A over the portfolio):",
            f"GRANT USE CATALOG ON CATALOG `{config.GENIE_MIRROR_CATALOG}` TO `{sp}`;",
            f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA {mirror} TO `{sp}`;",
            "",
        ]
    lines += [
        "-- System tables (optional — auto-detect landed sources from lineage):",
        f"GRANT SELECT ON SCHEMA system.access TO `{sp}`;",
        "",
        "-- Not SQL — set these in the Databricks UI:",
        f"--   SQL Warehouses -> {config.DATABRICKS_WAREHOUSE_ID or '<warehouse>'} "
        f"-> Permissions -> CAN USE for `{sp}`",
        f"--   Serving -> {config.SERVING_ENDPOINT} -> Permissions -> CAN QUERY for `{sp}`",
    ]
    return {"service_principal": sp, "sql": "\n".join(lines)}


@router.post("/recheck")
async def recheck(request: Request):
    """Re-run the probes. Exists as a POST so the UI's button is not a cached GET."""
    _actor = current_user(request)
    return await status(request)
