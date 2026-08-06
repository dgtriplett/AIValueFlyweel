#!/usr/bin/env python3
"""Grid Atlas — multi-workspace Unity Catalog metadata extractor.

Sweeps `system.information_schema` across one or more Databricks workspaces and
writes consolidated CSVs that the Grid Atlas app ingests to build its data-asset
inventory.

WHY THIS RUNS OUTSIDE THE APP
-----------------------------
The app's service principal only has credentials for the workspace it is
deployed in. Most utilities run several (prod / dev / per-OpCo / per-region), and
the interesting question — "what data do we have as an enterprise" — spans all of
them. This script runs on an analyst's machine under THEIR credentials, so it can
reach every workspace they can, and produces files that get uploaded once.

PRIVACY
-------
Metadata only: catalog / schema / table / column names, data types, comments,
owners, and row counts where already collected by the platform. No table
contents are read. Nothing is transmitted anywhere — output lands in ./output/
for you to inspect before uploading.

USAGE
-----
    pip install -r requirements.txt
    # list your workspace URLs, one per line
    $EDITOR workspaces.txt
    python3 extract_schemas.py

    # or point at a specific file / skip the column sweep on huge estates
    python3 extract_schemas.py --input prod-only.txt --no-columns

Auth: for each workspace the script reuses a matching `~/.databrickscfg` profile
if one exists, otherwise it opens a browser for OAuth. A workspace that fails
(no access, no warehouse, expired token) is reported and skipped — the run
continues and still produces output for the rest.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_PYTHON = (3, 9)

HERE = Path(__file__).parent
OUTPUT_DIR = HERE / "output"

# Catalogs that are platform internals rather than customer data. Excluded from
# every sweep so the inventory reflects the estate, not Databricks' own objects.
EXCLUDED_CATALOGS = ("system", "__databricks_internal", "samples")
EXCLUDED_SCHEMAS = ("information_schema",)


def _fail(message: str) -> "None":
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def check_prerequisites() -> None:
    if sys.version_info < MIN_PYTHON:
        _fail(f"Python {'.'.join(map(str, MIN_PYTHON))}+ required; found {sys.version.split()[0]}")
    try:
        import databricks.sdk  # noqa: F401
    except ImportError:
        _fail("databricks-sdk not installed. Run: pip install -r requirements.txt")


def workspace_slug(url: str) -> str:
    """Stable, filesystem-safe identifier for a workspace URL.

    Azure workspaces carry their id in the host (adb-<id>.<n>.azuredatabricks.net);
    for AWS/GCP the first host label is already unique within an account.
    """
    match = re.search(r"adb-(\d+)", url)
    if match:
        return f"adb-{match.group(1)}"
    host = re.sub(r"^https?://", "", url).rstrip("/")
    return re.sub(r"[^A-Za-z0-9._-]", "_", host.split(".")[0])


def find_profile_by_host(workspace_url: str) -> str | None:
    """Return a ~/.databrickscfg profile whose host matches, if any."""
    config_path = Path.home() / ".databrickscfg"
    if not config_path.exists():
        return None

    def normalize(value: str) -> str:
        value = value.strip().rstrip("/").lower()
        return value if value.startswith("http") else f"https://{value}"

    target = normalize(workspace_url)
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except configparser.Error:
        return None
    for section in parser.sections():
        host = parser[section].get("host")
        if host and normalize(host) == target:
            return section
    return None


def get_workspace_client(workspace_url: str):
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.config import Config

    workspace_url = workspace_url.rstrip("/")
    profile = find_profile_by_host(workspace_url)
    if profile:
        print(f"  auth: profile {profile!r}")
        return WorkspaceClient(profile=profile)
    print("  auth: no matching profile — opening browser for OAuth")
    return WorkspaceClient(config=Config(host=workspace_url, auth_type="external-browser"))


def pick_warehouse(client, preferred_id: str | None = None):
    """Choose a SQL warehouse, preferring one that is already RUNNING.

    Starting a cold warehouse can take minutes and bill for the privilege, so a
    running one is strongly preferred. `--warehouse-id` overrides the choice.
    """
    warehouses = list(client.warehouses.list())
    if not warehouses:
        raise RuntimeError("no SQL warehouses visible to this identity")
    if preferred_id:
        for warehouse in warehouses:
            if warehouse.id == preferred_id:
                return warehouse
        raise RuntimeError(f"warehouse id {preferred_id} not found in this workspace")
    for warehouse in warehouses:
        state = getattr(warehouse.state, "value", warehouse.state)
        if str(state).upper() == "RUNNING":
            return warehouse
    return warehouses[0]


def execute_sql(client, warehouse_id: str, sql: str) -> list[dict]:
    """Run a statement and return rows as dicts, following result pagination.

    The Statement Execution API caps an inline result chunk, so a large estate's
    table list arrives across several chunks. Ignoring `next_chunk_index` is the
    classic way to silently truncate an inventory, so we follow the chain.
    """
    from databricks.sdk.service.sql import Disposition, Format, StatementState

    response = client.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql,
        wait_timeout="50s",
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
    )

    statement_id = response.statement_id
    # wait_timeout caps at 50s; poll for anything slower.
    while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
        response = client.statement_execution.get_statement(statement_id)

    if response.status.state != StatementState.SUCCEEDED:
        error = getattr(response.status, "error", None)
        raise RuntimeError(getattr(error, "message", None) or f"state={response.status.state}")

    if not response.manifest or not response.result:
        return []

    columns = [c.name for c in response.manifest.schema.columns]
    rows: list[dict] = []
    chunk = response.result
    while chunk is not None:
        for values in chunk.data_array or []:
            rows.append(dict(zip(columns, values)))
        next_index = getattr(chunk, "next_chunk_index", None)
        if next_index is None:
            break
        chunk = client.statement_execution.get_statement_result_chunk_n(
            statement_id, next_index)
    return rows


def _not_in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} NOT IN ({quoted})"


SCHEMAS_SQL = f"""
SELECT catalog_name, schema_name, schema_owner, comment,
       created, created_by, last_altered, last_altered_by
FROM system.information_schema.schemata
WHERE {_not_in('catalog_name', EXCLUDED_CATALOGS)}
  AND {_not_in('schema_name', EXCLUDED_SCHEMAS)}
"""

TABLES_SQL = f"""
SELECT table_catalog, table_schema, table_name, table_type, table_owner,
       data_source_format, storage_sub_directory, comment,
       created, created_by, last_altered, last_altered_by
FROM system.information_schema.tables
WHERE {_not_in('table_catalog', EXCLUDED_CATALOGS)}
  AND {_not_in('table_schema', EXCLUDED_SCHEMAS)}
"""

# Column names are the single strongest signal for classifying what a table
# actually holds (a table with `settlement_point` + `lmp` is market data no
# matter what it is called), so they materially improve AI enrichment quality.
COLUMNS_SQL = f"""
SELECT table_catalog, table_schema, table_name, column_name, ordinal_position,
       data_type, is_nullable, comment
FROM system.information_schema.columns
WHERE {_not_in('table_catalog', EXCLUDED_CATALOGS)}
  AND {_not_in('table_schema', EXCLUDED_SCHEMAS)}
"""


def save_csv(rows: list[dict], path: Path, lead_columns: tuple[str, ...] = ()) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    ordered = [c for c in lead_columns if c in keys] + [c for c in keys if c not in lead_columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ordered, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def extract_workspace(url: str, *, want_columns: bool, warehouse_id: str | None) -> dict:
    """Extract one workspace. Returns {schemas, tables, columns} (possibly empty)."""
    slug = workspace_slug(url)
    print(f"\n=== {url}  [{slug}]")
    empty: dict[str, list] = {"schemas": [], "tables": [], "columns": []}
    try:
        client = get_workspace_client(url)
        warehouse = pick_warehouse(client, warehouse_id)
        state = getattr(warehouse.state, "value", warehouse.state)
        print(f"  warehouse: {warehouse.name} ({state})")

        results: dict[str, list] = {}
        sweeps = [("schemas", SCHEMAS_SQL), ("tables", TABLES_SQL)]
        if want_columns:
            sweeps.append(("columns", COLUMNS_SQL))
        for label, sql in sweeps:
            rows = execute_sql(client, warehouse.id, sql)
            for row in rows:
                row["workspace_url"] = url
                row["workspace_id"] = slug
            results[label] = rows
            print(f"  {label}: {len(rows)}")
            save_csv(rows, OUTPUT_DIR / f"{slug}_{label}.csv",
                     lead_columns=("workspace_url", "workspace_id"))
        results.setdefault("columns", [])
        return results
    except Exception as exc:  # noqa: BLE001 - one bad workspace must not end the run
        print(f"  SKIPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return empty


def read_workspace_list(path: Path) -> list[str]:
    if not path.exists():
        _fail(f"input file not found: {path}\n"
              "Create it with one workspace URL per line (see workspaces.txt).")
    urls: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line if line.startswith("http") else f"https://{line}")
    if not urls:
        _fail(f"no workspace URLs found in {path}")
    # De-dupe while preserving order so a repeated URL isn't swept twice.
    return list(dict.fromkeys(urls))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Unity Catalog metadata from one or more Databricks workspaces.")
    parser.add_argument("input", nargs="?", default=None,
                        help="file of workspace URLs (default: ./workspaces.txt)")
    parser.add_argument("--input", dest="input_flag", default=None,
                        help="same as the positional argument")
    parser.add_argument("--no-columns", action="store_true",
                        help="skip the column sweep (much faster on large estates, "
                             "at the cost of AI enrichment quality)")
    parser.add_argument("--warehouse-id", default=None,
                        help="force a specific SQL warehouse instead of auto-picking "
                             "a running one (must exist in every listed workspace)")
    parser.add_argument("--output-dir", default=None,
                        help="where to write CSVs (default: ./output)")
    args = parser.parse_args()

    check_prerequisites()

    global OUTPUT_DIR
    if args.output_dir:
        OUTPUT_DIR = Path(args.output_dir).expanduser().resolve()
    input_path = Path(args.input_flag or args.input or (HERE / "workspaces.txt")).expanduser()

    urls = read_workspace_list(input_path)
    print("=" * 64)
    print("Grid Atlas — Databricks metadata extractor")
    print("=" * 64)
    print(f"workspaces: {len(urls)}   columns: {'no' if args.no_columns else 'yes'}")
    print(f"output:     {OUTPUT_DIR}")

    totals: dict[str, list] = {"schemas": [], "tables": [], "columns": []}
    succeeded = 0
    for url in urls:
        result = extract_workspace(
            url, want_columns=not args.no_columns, warehouse_id=args.warehouse_id)
        if result["schemas"] or result["tables"]:
            succeeded += 1
        for key in totals:
            totals[key].extend(result[key])

    lead = ("workspace_url", "workspace_id")
    for key, rows in totals.items():
        if rows:
            save_csv(rows, OUTPUT_DIR / f"all_{key}.csv", lead_columns=lead)

    manifest = OUTPUT_DIR / "extract_manifest.csv"
    save_csv([{
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspaces_requested": len(urls),
        "workspaces_succeeded": succeeded,
        "schemas": len(totals["schemas"]),
        "tables": len(totals["tables"]),
        "columns": len(totals["columns"]),
        "columns_included": (not args.no_columns),
    }], manifest)

    print("\n" + "=" * 64)
    print(f"workspaces: {succeeded}/{len(urls)} succeeded")
    print(f"schemas:    {len(totals['schemas'])}")
    print(f"tables:     {len(totals['tables'])}")
    print(f"columns:    {len(totals['columns'])}")
    print(f"\nUpload these to Grid Atlas → Setup → Data Sources:")
    for key in ("schemas", "tables", "columns"):
        if totals[key]:
            print(f"  {OUTPUT_DIR / f'all_{key}.csv'}")
    if succeeded < len(urls):
        # Non-zero exit so a scheduled run surfaces partial failure to CI.
        sys.exit(2)


if __name__ == "__main__":
    main()
