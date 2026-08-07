"""Discovery ingestion — inventory upload, AI enrichment, canonicalization,
and attribution of discovered tables to catalog data assets.

THE PIPELINE
------------
    schema-extractor/  ->  CSV upload  ->  AI enrichment  ->  canonicalization
                                                                    |
                                       data_assets  <-  attribution -+

Each stage is a separate, idempotent endpoint so the UI can run them one at a
time with visible progress, and so a failure never forces a restart from zero.
Every run writes an `ingestion_runs` row.

WHERE THE DATA LIVES
--------------------
Inventory rows live in **Unity Catalog** (`ATLAS_CATALOG.ATLAS_SCHEMA`), not
Lakebase, for two reasons: a large estate is tens of thousands of rows that we
want to enrich with `ai_query()` (which only runs in SQL), and keeping raw
metadata in UC means the customer can query and govern it with their own tools.
Only the *conclusions* — asset ingestion status, discovered table counts — are
written back to Lakebase, which stays the portfolio's system of record.

SQL SAFETY
----------
The Statement Execution API takes complete statements with no bind parameters, so
every interpolated value goes through `enrichment.sql_str()`. Identifiers come
from validated config. Lakebase writes use asyncpg with real `$n` placeholders.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import enrichment as enr
from .. import normalization as norm
from ..common import current_user, rows_to_list, write_audit
from ..config import ATLAS_CATALOG, ATLAS_SCHEMA, SERVING_ENDPOINT, atlas_fqn, discovery_configured
from ..db import db
from ..limits import limiter
from ..lineage import run_sql

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

# Upload guard. A 200k-table estate is ~40MB of CSV; beyond that the client should
# split by workspace rather than stream an unbounded body into memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
# Rows per INSERT. Statement Execution has a statement-size limit, and batching
# keeps each statement well inside it while staying far faster than row-at-a-time.
INSERT_BATCH = 500


# ---------------------------------------------------------------------------
# Extractor download
# ---------------------------------------------------------------------------
# The app's service principal only holds credentials for the workspace it runs
# in, and a utility's data is usually spread across several — prod/dev, per
# operating company, per region. So the sweep has to run somewhere that can
# authenticate as a HUMAN, against every workspace they can reach.
#
# Serving the tool from the app itself (rather than "go clone the repo") means the
# person who needs it is already looking at the page that tells them why, and the
# copy they get matches the version of the app that will ingest their output.
EXTRACTOR_DIR = Path(__file__).resolve().parents[2] / "schema-extractor"

# Never shipped inside the ZIP: caches, and any CSV left over from a previous run
# in the source tree (that would be someone else's metadata).
_EXTRACTOR_SKIP_DIRS = {"__pycache__", "output", ".venv", ".git"}
_EXTRACTOR_SKIP_SUFFIXES = {".csv", ".pyc", ".pyo"}


def _extractor_files() -> list[Path]:
    if not EXTRACTOR_DIR.is_dir():
        return []
    files = []
    for path in sorted(EXTRACTOR_DIR.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _EXTRACTOR_SKIP_DIRS for part in path.relative_to(EXTRACTOR_DIR).parts):
            continue
        if path.suffix.lower() in _EXTRACTOR_SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def _run_instructions() -> str:
    """A RUN-ME the downloader sees first, tailored to THIS deployment.

    The generic README ships too, but it can't know which app to upload back to or
    which catalog is configured. Generating this at download time means the
    instructions name the actual URL, so there is nothing to translate.
    """
    from ..config import get_workspace_host

    try:
        host = get_workspace_host()
    except Exception:  # noqa: BLE001 - the ZIP must build even if host lookup fails
        host = ""
    target = "Grid Atlas → Get started → step 2A"
    return f"""Grid Atlas — workspace metadata extractor
=========================================

WHY YOU ARE RUNNING THIS LOCALLY
--------------------------------
Grid Atlas authenticates as a service principal that only has credentials for the
one workspace it is deployed in. Your data is probably spread across more than
that. This script runs under YOUR credentials, so it reaches every workspace you
can reach, and produces files you upload back once.

It reads METADATA ONLY — catalog / schema / table / column names, comments,
owners, and data types. No table contents are ever queried, and nothing is
transmitted anywhere: output lands in ./output/ for you to inspect first.

STEPS
-----
1. pip install -r requirements.txt

2. Edit workspaces.txt — one workspace URL per line. Include every workspace whose
   data you want in the inventory, especially the ones this app cannot reach.

3. python3 extract_schemas.py

   Per workspace it reuses a matching ~/.databrickscfg profile if it finds one, and
   otherwise opens a browser to log in. A workspace that fails is reported and
   SKIPPED — the run continues and still produces output for the rest.

4. Upload these from ./output/ into {target}:
       all_schemas.csv     (required)
       all_tables.csv      (required)
       all_columns.csv     (optional, but markedly improves AI accuracy)

OPTIONS
-------
   --no-columns         skip the column sweep. It is the largest query on a big
                        estate, but also the best signal for classification — a
                        table with settlement_point and lmp columns is market data
                        whatever it is named. Prefer narrowing workspaces.txt.
   --warehouse-id ID    force a specific warehouse instead of auto-picking a
                        running one.
   --output-dir DIR     write the CSVs somewhere else.

Re-running is safe. Ingestion MERGEs on (workspace, catalog, schema, table), so
re-uploading updates rows instead of duplicating them.

THIS DEPLOYMENT
---------------
   App:        {host or "(host unavailable)"}
   Upload to:  {target}
   Discovery:  {(ATLAS_CATALOG + "." + ATLAS_SCHEMA) if discovery_configured()
                else "not configured — set ATLAS_CATALOG in app.yaml"}
"""


@router.get("/extractor/download")
async def download_extractor():
    """Serve the metadata extractor as a ready-to-run ZIP.

    Generated per request rather than stored as a build artifact, so the bundle
    always matches the deployed code and carries instructions naming this app.
    """
    files = _extractor_files()
    if not files:
        raise HTTPException(
            500,
            "The extractor source is missing from this deployment. Get it from the "
            "repository's schema-extractor/ directory instead.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"schema-extractor/{path.relative_to(EXTRACTOR_DIR)}")
        # Named to sort first in a file listing, so it is the obvious entry point.
        archive.writestr("schema-extractor/RUN-ME-FIRST.txt", _run_instructions())

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition":
                 'attachment; filename="grid-atlas-schema-extractor.zip"'})


@router.get("/extractor/info")
async def extractor_info():
    """What the download contains, for the UI to render before someone clicks."""
    files = _extractor_files()
    return {
        "available": bool(files),
        "files": [str(p.relative_to(EXTRACTOR_DIR)) for p in files],
        "total_bytes": sum(p.stat().st_size for p in files),
        "requires": ["Python 3.9+", "databricks-sdk", "a SQL warehouse per workspace"],
        "reads": "metadata only — catalog/schema/table/column names, comments, owners",
        "produces": ["all_schemas.csv", "all_tables.csv", "all_columns.csv"],
    }


def _require_discovery() -> None:
    if not discovery_configured():
        raise HTTPException(
            409,
            "The discovery layer is not configured. Set ATLAS_CATALOG (and "
            "ATLAS_SCHEMA) in app.yaml and grant the app's service principal "
            "USE_CATALOG + CREATE_SCHEMA. Setup → Environment shows the exact "
            "GRANT statements.")


async def _sql(statement: str, timeout_s: int = 40) -> dict:
    """Run a discovery statement, raising a useful HTTP error on failure."""
    result = await run_sql(statement, timeout_s=timeout_s)
    if not result["ok"]:
        error = result.get("error") or "unknown error"
        status = 403 if "PERMISSION_DENIED" in error or "does not have" in error else 502
        raise HTTPException(status, f"Discovery SQL failed: {error}")
    return result


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------
async def _start_run(kind: str, actor: str) -> int | None:
    row = await db.fetchrow(
        "INSERT INTO ingestion_runs (kind, status, actor) VALUES ($1,'running',$2) "
        "RETURNING id", kind, actor)
    return row["id"] if row else None


async def _finish_run(run_id: int | None, status: str, stats: dict,
                      error: str | None = None) -> None:
    if run_id is None:
        return
    await db.execute(
        "UPDATE ingestion_runs SET status=$1, stats_json=$2::jsonb, error=$3, "
        "finished_at=now() WHERE id=$4",
        status, json.dumps(stats), error, run_id)


@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=200)):
    return rows_to_list(await db.fetch(
        "SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT $1", limit))


# ---------------------------------------------------------------------------
# Stage 0 — bootstrap the discovery schema
# ---------------------------------------------------------------------------
@router.post("/bootstrap", dependencies=[Depends(limiter("sweep"))])
async def bootstrap(request: Request):
    """CREATE the discovery schema + tables in Unity Catalog. Idempotent."""
    _require_discovery()
    actor = current_user(request)
    await _sql(f"CREATE SCHEMA IF NOT EXISTS {enr.discovery_schema_fqn()}")

    await _sql(f"""
        CREATE TABLE IF NOT EXISTS {atlas_fqn('discovered_schemas')} (
            workspace_id STRING, workspace_url STRING,
            catalog_name STRING, schema_name STRING,
            owner STRING, comment STRING, table_count INT,
            business_name STRING, ai_definition STRING,
            is_user_edited BOOLEAN, is_present BOOLEAN,
            first_seen_at TIMESTAMP, last_seen_at TIMESTAMP
        ) USING DELTA
        COMMENT 'Grid Atlas: discovered Unity Catalog schemas across workspaces.'
    """)
    await _sql(f"""
        CREATE TABLE IF NOT EXISTS {atlas_fqn('discovered_tables')} (
            workspace_id STRING, catalog_name STRING, schema_name STRING,
            table_name STRING, table_type STRING, data_format STRING,
            owner STRING, comment STRING,
            column_summary STRING, column_count INT,
            business_name STRING, ai_definition STRING,
            source_system_raw STRING, source_system_canonical STRING,
            canonical_confidence STRING,
            data_asset_id INT, mapped_by STRING,
            is_user_edited BOOLEAN, is_present BOOLEAN,
            first_seen_at TIMESTAMP, last_seen_at TIMESTAMP
        ) USING DELTA
        COMMENT 'Grid Atlas: discovered Unity Catalog tables + AI enrichment.'
    """)
    await write_audit("ingestion", None, "bootstrap", actor,
                      {"catalog": ATLAS_CATALOG, "schema": ATLAS_SCHEMA})
    return {"ok": True, "catalog": ATLAS_CATALOG, "schema": ATLAS_SCHEMA,
            "tables": ["discovered_schemas", "discovered_tables"]}


# ---------------------------------------------------------------------------
# Stage 1 — inventory upload
# ---------------------------------------------------------------------------
def _read_csv(content: bytes) -> list[dict]:
    """Parse an uploaded CSV, tolerating a UTF-8 BOM from Excel round-trips."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not decode CSV: {exc}")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(422, "CSV contained no data rows")
    return rows


def _require_columns(rows: list[dict], required: set[str], label: str) -> None:
    present = {(k or "").strip() for k in rows[0]}
    missing = required - present
    if missing:
        raise HTTPException(
            422,
            f"{label} CSV is missing required column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(c for c in present if c))}. "
            "Use the CSVs produced by schema-extractor/extract_schemas.py.")


def _get(row: dict, *names: str) -> str | None:
    """First non-empty value among `names`. Accommodates the extractor's own
    column names as well as raw information_schema names, so a hand-assembled CSV
    also works."""
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


async def _merge_rows(table: str, key_columns: list[str], columns: list[str],
                      records: list[tuple]) -> int:
    """MERGE a batch of records into a discovery table.

    MERGE rather than INSERT so re-uploading updates in place instead of
    duplicating, and so `first_seen_at` survives while `last_seen_at` advances —
    that pair is what lets a later sweep mark vanished tables absent.
    """
    if not records:
        return 0
    fqn = atlas_fqn(table)
    written = 0
    for start in range(0, len(records), INSERT_BATCH):
        batch = records[start:start + INSERT_BATCH]
        values = ",".join(
            "(" + ",".join(enr.sql_str(v) for v in record) + ")" for record in batch)
        column_list = ", ".join(f"`{c}`" for c in columns)
        on_clause = " AND ".join(f"t.`{k}` = s.`{k}`" for k in key_columns)
        # Only refresh columns that carry new information. Enrichment columns and
        # is_user_edited are deliberately absent so an upload never clobbers them.
        updatable = [c for c in columns if c not in key_columns]
        set_clause = ", ".join(f"t.`{c}` = s.`{c}`" for c in updatable)
        await _sql(f"""
            MERGE INTO {fqn} AS t
            USING (SELECT * FROM VALUES {values} AS v({column_list})) AS s
            ON {on_clause}
            WHEN MATCHED THEN UPDATE SET {set_clause}, t.`last_seen_at` = current_timestamp(),
                                        t.`is_present` = true
            WHEN NOT MATCHED THEN INSERT ({column_list}, `is_user_edited`, `is_present`,
                                          `first_seen_at`, `last_seen_at`)
                 VALUES ({', '.join(f's.`{c}`' for c in columns)}, false, true,
                         current_timestamp(), current_timestamp())
        """, timeout_s=120)
        written += len(batch)
    return written


@router.post("/upload/schemas")
async def upload_schemas(request: Request, file: UploadFile = File(...)):
    """Ingest `all_schemas.csv` from the extractor."""
    _require_discovery()
    actor = current_user(request)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024}MB. "
                                 "Split the extract by workspace and upload separately.")
    rows = _read_csv(content)
    _require_columns(rows, {"catalog_name", "schema_name"}, "Schemas")

    run_id = await _start_run("inventory_upload", actor)
    try:
        records = []
        for row in rows:
            workspace = _get(row, "workspace_id", "workspace_url") or "unknown"
            catalog = _get(row, "catalog_name", "table_catalog")
            schema = _get(row, "schema_name", "table_schema")
            if not catalog or not schema:
                continue
            records.append((
                workspace, _get(row, "workspace_url"), catalog, schema,
                _get(row, "schema_owner", "owner"), _get(row, "comment"), 0))
        written = await _merge_rows(
            "discovered_schemas",
            ["workspace_id", "catalog_name", "schema_name"],
            ["workspace_id", "workspace_url", "catalog_name", "schema_name",
             "owner", "comment", "table_count"],
            records)
        stats = {"rows_in_file": len(rows), "rows_written": written}
        await _finish_run(run_id, "succeeded", stats)
        await write_audit("ingestion", run_id, "upload_schemas", actor, stats)
        return {"ok": True, "run_id": run_id, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", {}, str(exc.detail))
        raise
    except Exception as exc:  # noqa: BLE001
        await _finish_run(run_id, "failed", {}, str(exc))
        raise HTTPException(500, f"Schema upload failed: {exc}")


@router.post("/upload/tables")
async def upload_tables(request: Request, file: UploadFile = File(...)):
    """Ingest `all_tables.csv` from the extractor."""
    _require_discovery()
    actor = current_user(request)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024}MB. "
                                 "Split the extract by workspace and upload separately.")
    rows = _read_csv(content)
    _require_columns(rows, {"table_catalog", "table_schema", "table_name"}, "Tables")

    run_id = await _start_run("inventory_upload", actor)
    try:
        records = []
        for row in rows:
            workspace = _get(row, "workspace_id", "workspace_url") or "unknown"
            catalog = _get(row, "table_catalog", "catalog_name")
            schema = _get(row, "table_schema", "schema_name")
            table = _get(row, "table_name")
            if not (catalog and schema and table):
                continue
            records.append((
                workspace, catalog, schema, table,
                _get(row, "table_type"), _get(row, "data_source_format", "data_format"),
                _get(row, "table_owner", "owner"), _get(row, "comment")))
        written = await _merge_rows(
            "discovered_tables",
            ["workspace_id", "catalog_name", "schema_name", "table_name"],
            ["workspace_id", "catalog_name", "schema_name", "table_name",
             "table_type", "data_format", "owner", "comment"],
            records)
        # Keep the denormalized per-schema count honest after every upload.
        await _sql(f"""
            MERGE INTO {atlas_fqn('discovered_schemas')} AS t
            USING (
                SELECT workspace_id, catalog_name, schema_name, COUNT(*) AS n
                FROM {atlas_fqn('discovered_tables')}
                WHERE is_present = true
                GROUP BY workspace_id, catalog_name, schema_name
            ) AS s
            ON t.workspace_id = s.workspace_id AND t.catalog_name = s.catalog_name
               AND t.schema_name = s.schema_name
            WHEN MATCHED THEN UPDATE SET t.table_count = s.n
        """, timeout_s=90)
        stats = {"rows_in_file": len(rows), "rows_written": written}
        await _finish_run(run_id, "succeeded", stats)
        await write_audit("ingestion", run_id, "upload_tables", actor, stats)
        return {"ok": True, "run_id": run_id, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", {}, str(exc.detail))
        raise
    except Exception as exc:  # noqa: BLE001
        await _finish_run(run_id, "failed", {}, str(exc))
        raise HTTPException(500, f"Table upload failed: {exc}")


@router.post("/upload/columns")
async def upload_columns(request: Request, file: UploadFile = File(...)):
    """Ingest `all_columns.csv`, folded into `discovered_tables.column_summary`.

    Columns are collapsed to one string per table rather than stored per row:
    they are only ever fed to the model as context, and a columns table on a
    50k-table estate is millions of rows for no query benefit.
    """
    _require_discovery()
    actor = current_user(request)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024}MB.")
    rows = _read_csv(content)
    _require_columns(rows, {"table_catalog", "table_schema", "table_name", "column_name"},
                     "Columns")

    run_id = await _start_run("inventory_upload", actor)
    try:
        grouped: dict[tuple, list[str]] = {}
        for row in rows:
            workspace = _get(row, "workspace_id", "workspace_url") or "unknown"
            key = (workspace, _get(row, "table_catalog", "catalog_name"),
                   _get(row, "table_schema", "schema_name"), _get(row, "table_name"))
            if not all(key):
                continue
            name = _get(row, "column_name")
            if not name:
                continue
            data_type = _get(row, "data_type")
            grouped.setdefault(key, []).append(f"{name} {data_type}" if data_type else name)

        records = []
        for key, columns in grouped.items():
            # Cap the stored summary: the prompt truncates anyway, and a
            # 400-column fact table would bloat every row for no gain.
            summary = ", ".join(columns[:120])
            records.append((*key, summary, len(columns)))
        written = await _merge_rows(
            "discovered_tables",
            ["workspace_id", "catalog_name", "schema_name", "table_name"],
            ["workspace_id", "catalog_name", "schema_name", "table_name",
             "column_summary", "column_count"],
            records)
        stats = {"rows_in_file": len(rows), "tables_updated": written}
        await _finish_run(run_id, "succeeded", stats)
        return {"ok": True, "run_id": run_id, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", {}, str(exc.detail))
        raise
    except Exception as exc:  # noqa: BLE001
        await _finish_run(run_id, "failed", {}, str(exc))
        raise HTTPException(500, f"Column upload failed: {exc}")


# ---------------------------------------------------------------------------
# Stage 2 — AI enrichment
# ---------------------------------------------------------------------------
class EnrichIn(BaseModel):
    company_name: str | None = None
    max_rows: int | None = None
    only_schema: str | None = None
    # Replay stage 2 from existing staging without re-paying for inference.
    skip_staging: bool = False
    prune_staging: bool = False
    endpoint: str | None = None


async def _canonical_vocabulary() -> list[str]:
    rows = await db.fetch(
        "SELECT DISTINCT source_category FROM data_assets "
        "WHERE source_category IS NOT NULL AND source_category <> '' "
        "ORDER BY source_category")
    return [r["source_category"] for r in rows]


@router.post("/enrich/schemas", dependencies=[Depends(limiter("sweep"))])
async def enrich_schemas(body: EnrichIn, request: Request):
    """AI-enrich discovered schemas (single statement; few hundred rows)."""
    _require_discovery()
    actor = current_user(request)
    run_id = await _start_run("ai_enrichment", actor)
    try:
        await _sql(enr.build_schema_enrichment_sql(
            body.company_name or "the utility", body.endpoint, body.max_rows),
            timeout_s=600)
        result = await _sql(
            f"SELECT COUNT(*) AS enriched FROM {atlas_fqn('discovered_schemas')} "
            "WHERE ai_definition IS NOT NULL AND ai_definition <> ''")
        enriched = int(result["rows"][0][0]) if result["rows"] else 0
        stats = {"schemas_enriched_total": enriched}
        await _finish_run(run_id, "succeeded", stats)
        return {"ok": True, "run_id": run_id, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", {}, str(exc.detail))
        raise


@router.post("/enrich/tables", dependencies=[Depends(limiter("sweep"))])
async def enrich_tables(body: EnrichIn, request: Request):
    """AI-enrich discovered tables using the staged ai_query pattern.

    Stage 1 (the expensive half) can be skipped to replay the MERGE for free —
    see server/enrichment.py for why the split exists.
    """
    _require_discovery()
    actor = current_user(request)
    run_id = await _start_run("ai_enrichment", actor)
    stats: dict = {}
    try:
        if not body.skip_staging:
            canonicals = await _canonical_vocabulary()
            await _sql(enr.build_staging_sql(
                company_name=body.company_name or "the utility",
                canonicals=canonicals,
                endpoint=body.endpoint,
                max_rows=body.max_rows,
                only_schema=body.only_schema,
            ), timeout_s=600)
            counts = await _sql(enr.build_staging_stats_sql())
            if counts["rows"]:
                row = counts["rows"][0]
                stats.update({"staged_rows": int(row[0] or 0),
                              "staged_ok": int(row[1] or 0),
                              "staged_errors": int(row[2] or 0)})

        if body.prune_staging:
            await _sql(enr.build_prune_staging_sql(), timeout_s=120)

        await _sql(enr.build_merge_sql(), timeout_s=300)
        result = await _sql(
            f"SELECT COUNT(*) AS n FROM {atlas_fqn('discovered_tables')} "
            "WHERE ai_definition IS NOT NULL AND ai_definition <> ''")
        stats["tables_enriched_total"] = int(result["rows"][0][0]) if result["rows"] else 0

        # A partial run is a real outcome, not a failure: failOnError=>false means
        # individual rows can fail while the rest succeed.
        status = "partial" if stats.get("staged_errors") else "succeeded"
        await _finish_run(run_id, status, stats)
        return {"ok": True, "run_id": run_id, "status": status, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", stats, str(exc.detail))
        raise


# ---------------------------------------------------------------------------
# Stage 3 — canonicalization
# ---------------------------------------------------------------------------
class CanonicalizeIn(BaseModel):
    # Re-evaluate labels currently mapped to 'Other' (use after extending the
    # canonical vocabulary).
    remap_other: bool = False
    # Deterministic stages only — no LLM call, no cost.
    skip_llm: bool = False
    max_llm_labels: int = 400


@router.post("/canonicalize", dependencies=[Depends(limiter("generate"))])
async def canonicalize(body: CanonicalizeIn, request: Request):
    """Resolve raw source-system labels to the canonical vocabulary.

    Runs the cascade in server/normalization.py: exact -> alias memo ->
    normalized -> one batched LLM call -> 'Other'. Human mappings
    (mapped_by='manual' or is_user_edited) are never touched.
    """
    _require_discovery()
    actor = current_user(request)
    run_id = await _start_run("canonicalization", actor)
    stats = {"exact": 0, "normalized": 0, "llm": 0, "other": 0, "skipped_manual": 0}
    try:
        canonicals = await _canonical_vocabulary()
        if not canonicals:
            raise HTTPException(409, "No canonical source categories exist yet — seed the "
                                     "reference catalog first.")

        alias_rows = await db.fetch(
            "SELECT raw_normalized, canonical, mapped_by, is_user_edited "
            "FROM data_asset_aliases")
        alias_map = {r["raw_normalized"]: r["canonical"] for r in alias_rows if r["canonical"]}
        protected = {r["raw_normalized"] for r in alias_rows
                     if r["is_user_edited"] or r["mapped_by"] == "manual"}
        known = {r["raw_normalized"] for r in alias_rows}
        if body.remap_other:
            # Reconsider 'Other' rows, but never a human's explicit 'Other'.
            known -= {r["raw_normalized"] for r in alias_rows
                      if r["canonical"] == norm.OTHER and r["raw_normalized"] not in protected}

        # Distinct raw labels present in the inventory.
        result = await _sql(f"""
            SELECT DISTINCT source_system_raw
            FROM {atlas_fqn('discovered_tables')}
            WHERE source_system_raw IS NOT NULL AND source_system_raw <> ''
              AND is_present = true
        """, timeout_s=90)
        raw_labels = [r[0] for r in result["rows"] if r and r[0]]

        index = norm.build_canonical_index(canonicals)
        resolved: dict[str, tuple[str, str, str]] = {}   # raw -> (canonical, how, conf)
        unresolved: list[str] = []
        for raw in raw_labels:
            normalized = norm.normalize_raw(raw)
            if normalized in protected:
                stats["skipped_manual"] += 1
                continue
            if normalized in known:
                continue  # already memoized
            canonical, how, confidence = norm.resolve_deterministic(
                raw, canonicals, alias_map=alias_map, canonical_index=index)
            if canonical:
                resolved[raw] = (canonical, how, confidence)
                stats[how] = stats.get(how, 0) + 1
            else:
                unresolved.append(raw)

        # Stage 4: one batched call for the remainder.
        if unresolved and not body.skip_llm:
            from ..routes.agents import _llm_json
            batch = unresolved[:body.max_llm_labels]
            parsed, _used, _note = await _llm_json(
                norm.build_llm_prompt(batch, canonicals), max_tokens=4000)
            mappings = norm.parse_llm_mappings(parsed, batch, canonicals)
            for raw, (canonical, confidence) in mappings.items():
                resolved[raw] = (canonical, "llm", confidence)
                stats["llm"] += 1
            # Anything the model skipped, or we never sent, lands in 'Other' for
            # human review rather than being silently dropped.
            for raw in unresolved:
                if raw not in resolved:
                    resolved[raw] = (norm.OTHER, "fallback_other", "low")
                    stats["other"] += 1
        elif unresolved:
            for raw in unresolved:
                resolved[raw] = (norm.OTHER, "fallback_other", "low")
                stats["other"] += 1

        # Persist the memo table (Lakebase) — parameterized.
        for raw, (canonical, how, confidence) in resolved.items():
            await db.execute(
                """INSERT INTO data_asset_aliases
                   (raw, raw_normalized, canonical, mapped_by, confidence)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (raw) DO UPDATE
                   SET canonical=EXCLUDED.canonical, mapped_by=EXCLUDED.mapped_by,
                       confidence=EXCLUDED.confidence, updated_at=now()
                   WHERE data_asset_aliases.is_user_edited = false
                     AND data_asset_aliases.mapped_by <> 'manual'""",
                raw, norm.normalize_raw(raw), canonical, how, confidence)

        # Push the full mapping back onto the inventory in UC.
        all_aliases = await db.fetch(
            "SELECT raw, canonical, confidence FROM data_asset_aliases WHERE canonical IS NOT NULL")
        if all_aliases:
            pairs = ",".join(
                "(" + ",".join([enr.sql_str(a["raw"]), enr.sql_str(a["canonical"]),
                                enr.sql_str(a["confidence"] or "low")]) + ")"
                for a in all_aliases)
            await _sql(f"""
                MERGE INTO {atlas_fqn('discovered_tables')} AS t
                USING (SELECT * FROM VALUES {pairs} AS v(raw, canonical, confidence)) AS s
                ON t.source_system_raw = s.raw
                WHEN MATCHED AND COALESCE(t.is_user_edited, false) = false THEN UPDATE SET
                    t.source_system_canonical = s.canonical,
                    t.canonical_confidence = s.confidence
            """, timeout_s=180)

        stats["distinct_labels"] = len(raw_labels)
        stats["newly_resolved"] = len(resolved)
        await _finish_run(run_id, "succeeded", stats)
        await write_audit("ingestion", run_id, "canonicalize", actor, stats)
        return {"ok": True, "run_id": run_id, **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", stats, str(exc.detail))
        raise


# ---------------------------------------------------------------------------
# Stage 4 — attribution: discovered tables -> catalog data assets
# ---------------------------------------------------------------------------
class AttributeIn(BaseModel):
    # Minimum tables behind a module before its status is advanced. Guards against
    # one stray table flipping a whole source to landed.
    min_tables: int = 1
    # Advance ingestion_status for matched assets. Off by default: changing status
    # moves readiness and therefore the roadmap, so it is an explicit choice.
    advance_status: bool = False
    # Highest status discovery may assign. Discovery proves data EXISTS; it cannot
    # prove it is curated or governed, so it stops at 'landed' unless overridden.
    max_status: str = "landed"


_STATUS_ORDER = ("not_started", "landed", "curated", "governed")


@router.post("/attribute", dependencies=[Depends(limiter("generate"))])
async def attribute(body: AttributeIn, request: Request):
    """Attribute discovered tables to catalog data assets by canonical match.

    This is the join that turns raw inventory into portfolio signal: a module with
    real tables behind it is data the utility actually has.
    """
    _require_discovery()
    if body.max_status not in _STATUS_ORDER:
        raise HTTPException(422, f"max_status must be one of {list(_STATUS_ORDER)}")
    actor = current_user(request)
    run_id = await _start_run("asset_mapping", actor)
    try:
        result = await _sql(f"""
            SELECT source_system_canonical AS canonical,
                   COUNT(*) AS table_count,
                   COUNT(DISTINCT CONCAT(catalog_name, '.', schema_name)) AS schema_count,
                   MIN(canonical_confidence) AS worst_confidence
            FROM {atlas_fqn('discovered_tables')}
            WHERE is_present = true
              AND source_system_canonical IS NOT NULL
              AND source_system_canonical <> ''
              AND source_system_canonical <> {enr.sql_str(norm.OTHER)}
            GROUP BY source_system_canonical
        """, timeout_s=90)
        discovered = {
            row[0]: {"tables": int(row[1] or 0), "schemas": int(row[2] or 0),
                     "confidence": row[3] or "low"}
            for row in result["rows"] if row and row[0]
        }

        assets = await db.fetch(
            "SELECT id, source_category, module, ingestion_status FROM data_assets")
        matched = 0
        advanced = []
        # Every module under a canonical shares its discovered count: attribution
        # resolves to the SOURCE, and which module a given table belongs to is a
        # finer judgement the user makes in the catalog UI.
        for asset in assets:
            found = discovered.get(asset["source_category"])
            if not found or found["tables"] < body.min_tables:
                continue
            matched += 1
            await db.execute(
                "UPDATE data_assets SET discovered_table_count=$1, discovery_confidence=$2, "
                "last_discovered_at=now(), updated_at=now() WHERE id=$3",
                found["tables"], found["confidence"], asset["id"])
            if body.advance_status:
                current = asset["ingestion_status"]
                cap = _STATUS_ORDER.index(body.max_status)
                # Only ever move forward, and never past the cap — a source a
                # human already marked governed must not be demoted to landed.
                if _STATUS_ORDER.index(current) < cap:
                    await db.execute(
                        "UPDATE data_assets SET ingestion_status=$1, auto_captured=true, "
                        "auto_note=$2, updated_at=now() WHERE id=$3",
                        body.max_status,
                        f"Discovered {found['tables']} tables across "
                        f"{found['schemas']} schemas in Unity Catalog.",
                        asset["id"])
                    advanced.append({"id": asset["id"],
                                     "label": f"{asset['source_category']} · {asset['module']}",
                                     "from": current, "to": body.max_status})

        stats = {"canonicals_discovered": len(discovered), "assets_matched": matched,
                 "assets_advanced": len(advanced)}
        await _finish_run(run_id, "succeeded", stats)
        await write_audit("ingestion", run_id, "attribute", actor, stats)
        return {"ok": True, "run_id": run_id, "advanced": advanced,
                "unmatched_canonicals": sorted(
                    set(discovered) - {a["source_category"] for a in assets}),
                **stats}
    except HTTPException as exc:
        await _finish_run(run_id, "failed", {}, str(exc.detail))
        raise


# ---------------------------------------------------------------------------
# Read-side: inventory summary + alias review
# ---------------------------------------------------------------------------
@router.get("/summary")
async def summary():
    """Inventory rollup for the discovery dashboard. Degrades to a disabled
    state rather than erroring when discovery isn't configured yet."""
    if not discovery_configured():
        return {"configured": False, "catalog": None, "schema": None}
    result = await run_sql(f"""
        SELECT
            (SELECT COUNT(*) FROM {atlas_fqn('discovered_schemas')} WHERE is_present) AS schemas,
            (SELECT COUNT(*) FROM {atlas_fqn('discovered_tables')}  WHERE is_present) AS tables,
            (SELECT COUNT(*) FROM {atlas_fqn('discovered_tables')}
              WHERE is_present AND ai_definition IS NOT NULL AND ai_definition <> '') AS enriched,
            (SELECT COUNT(DISTINCT workspace_id) FROM {atlas_fqn('discovered_tables')}) AS workspaces,
            (SELECT COUNT(DISTINCT source_system_canonical) FROM {atlas_fqn('discovered_tables')}
              WHERE source_system_canonical IS NOT NULL AND source_system_canonical <> '') AS canonicals
    """)
    if not result["ok"]:
        return {"configured": True, "available": False, "error": result["error"],
                "catalog": ATLAS_CATALOG, "schema": ATLAS_SCHEMA}
    row = result["rows"][0] if result["rows"] else [0, 0, 0, 0, 0]
    return {
        "configured": True, "available": True,
        "catalog": ATLAS_CATALOG, "schema": ATLAS_SCHEMA,
        "schemas": int(row[0] or 0), "tables": int(row[1] or 0),
        "enriched_tables": int(row[2] or 0), "workspaces": int(row[3] or 0),
        "canonicals": int(row[4] or 0),
        "serving_endpoint": SERVING_ENDPOINT,
    }


@router.get("/aliases")
async def list_aliases(needs_review: bool = False, limit: int = Query(200, ge=1, le=2000)):
    """Alias mappings. `needs_review=true` surfaces exactly what a human should
    look at: unresolved labels and low-confidence guesses."""
    where = ""
    if needs_review:
        where = ("WHERE (canonical = $2 OR confidence = 'low' OR canonical IS NULL) "
                 "AND is_user_edited = false")
        rows = await db.fetch(
            f"SELECT * FROM data_asset_aliases {where} ORDER BY raw LIMIT $1",
            limit, norm.OTHER)
    else:
        rows = await db.fetch(
            "SELECT * FROM data_asset_aliases ORDER BY raw LIMIT $1", limit)
    return rows_to_list(rows)


class AliasPatchIn(BaseModel):
    canonical: str


@router.patch("/aliases/{alias_id}")
async def patch_alias(alias_id: int, body: AliasPatchIn, request: Request):
    """Correct a mapping by hand. Pins it permanently against future re-runs."""
    canonicals = await _canonical_vocabulary()
    if body.canonical not in canonicals and body.canonical != norm.OTHER:
        raise HTTPException(422, f"'{body.canonical}' is not a known source category")
    actor = current_user(request)
    row = await db.fetchrow(
        "UPDATE data_asset_aliases SET canonical=$1, mapped_by='manual', "
        "confidence='high', is_user_edited=true, updated_at=now() "
        "WHERE id=$2 RETURNING *", body.canonical, alias_id)
    if row is None:
        raise HTTPException(404, "Alias not found")
    await write_audit("data_asset_alias", alias_id, "manual_map", actor,
                      {"canonical": body.canonical})
    return dict(row)
