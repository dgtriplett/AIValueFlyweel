# AI Value Flywheel — Metadata Extractor

A standalone utility that pulls Unity Catalog **metadata** from one or more
Databricks workspaces into CSVs you upload to AI Value Flywheel.

## Why this exists

The AI Value Flywheel app authenticates as a service principal in the single workspace
it is deployed to. Most utilities run several — prod/dev, per-operating-company,
per-region — and the question worth answering ("what data do we have as an
enterprise?") spans all of them.

This script runs on your machine under **your** credentials, so it reaches every
workspace you can reach, and produces files that get uploaded once.

## What it reads — and what it does not

Reads, from `system.information_schema`:

- **schemas** — catalog, schema, owner, comment, created/altered timestamps
- **tables** — table name, type, format, owner, comment, timestamps
- **columns** — column name, data type, nullability, comment *(skippable)*

It does **not** read table contents. No row of your data is queried, and nothing
is transmitted anywhere — output lands in `./output/` for you to review before
uploading.

`system`, `__databricks_internal`, `samples`, and `information_schema` are
excluded, so the inventory reflects your estate rather than platform internals.

## Prerequisites

- Python 3.9+
- A SQL warehouse you can use in each workspace
- `SELECT` on `system.information_schema` (any user with catalog access has this)

> **Windows:** the python.org installer ships `python.exe`, not `python3.exe`.
> Every `python3 ...` command below works as `python ...` (or `py -3 ...`).

## Usage

```bash
pip install -r requirements.txt

# list your workspace URLs, one per line
$EDITOR workspaces.txt

python3 extract_schemas.py
```

Output in `./output/`:

| File | Contents |
|---|---|
| `all_schemas.csv` | Every schema across every workspace |
| `all_tables.csv` | Every table across every workspace |
| `all_columns.csv` | Every column (omitted with `--no-columns`) |
| `<workspace>_*.csv` | Per-workspace files, for spot-checking |
| `extract_manifest.csv` | Run summary — counts, timestamp, success ratio |

Upload `all_schemas.csv`, `all_tables.csv`, and (if present) `all_columns.csv` in
the app under **Setup → Data Sources**.

### Options

```bash
python3 extract_schemas.py --input prod-only.txt   # a different workspace list
python3 extract_schemas.py --no-columns            # skip the column sweep
python3 extract_schemas.py --warehouse-id abc123   # force a specific warehouse
python3 extract_schemas.py --output-dir /tmp/out   # write elsewhere
```

**On `--no-columns`:** the column sweep is the largest query — a 50k-table estate
has millions of columns. It is also the single best signal for AI enrichment: a
table with `settlement_point` and `lmp` columns is market data regardless of what
it is named. Keep columns on unless the sweep is too slow, and prefer narrowing
the workspace list instead.

## Authentication

Per workspace, in order:

1. **A matching `~/.databrickscfg` profile** — matched on host, reused silently.
2. **Browser OAuth** — opens a login for that workspace.

To pre-create profiles:

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com -p <name>
```

## Warehouse selection

A **running** warehouse is preferred over a stopped one, since a cold start costs
minutes and money. If none is running, the first visible warehouse is used (and
starting it may take a few minutes). Override with `--warehouse-id`.

## Behaviour on failure

A workspace that fails — no access, no warehouse, expired token — is reported to
stderr and **skipped**; the run continues and still writes output for the rest.
The exit code is `2` if any workspace was skipped, `0` if all succeeded, so a
scheduled run surfaces partial failure.

Results are paginated correctly: large estates return table lists across several
chunks, and all chunks are followed. If a count looks suspiciously round
(exactly 25,000, say), that would be truncation — please file an issue.

## Re-running

Safe and idempotent. Each run overwrites the CSVs. Ingestion in the app is a
MERGE keyed on `(workspace, catalog, schema, table)`, so re-uploading updates
existing rows rather than duplicating them, and tables dropped since the last run
are marked absent rather than silently lingering.
