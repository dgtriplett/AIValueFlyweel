# Install — Grid Atlas

Deploy into **your own** Databricks workspace.

- [The short version](#the-short-version)
- [Prerequisites](#prerequisites)
- [Step 1 — Provision Lakebase](#step-1--provision-lakebase)
- [Step 2 — Deploy](#step-2--deploy)
- [Step 3 — Verify with the Setup console](#step-3--verify-with-the-setup-console)
- [Step 4 — Unity Catalog grants](#step-4--unity-catalog-grants)
- [Step 5 — Discover your data (optional)](#step-5--discover-your-data-optional)
- [Step 6 — Genie space (optional)](#step-6--genie-space-optional)
- [Manual deploy](#manual-deploy)
- [Local development](#local-development)
- [Troubleshooting](#troubleshooting)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)

---

## The short version

```bash
git clone <this-repo> && cd grid-atlas
python3 scripts/deploy.py --dry-run     # review what it will do
python3 scripts/deploy.py               # do it
```

Then open the app → **`/console`** → *Setup & health*, and fix anything red.

---

## Prerequisites

### In your workspace

| Resource | Why | Notes |
|---|---|---|
| **Unity Catalog** | Discovery + the Genie mirror | Required for discovery; the curated catalog works without it |
| **Serverless enabled** | Lakebase + Foundation Models | Required |
| **A SQL warehouse** | System tables, UC discovery, `ai_query()` | Serverless is fine; size with enrichment volume |
| **A model-serving endpoint** | AI agents | Any chat-completion endpoint. Default `databricks-claude-sonnet-4-5`. Pay-per-token works |
| **Permission to create Apps** | The bundle deploys an App | Workspace admin, or *Can Manage* on Apps |
| **Permission to GRANT** | The app SP needs UC access | Metastore admin or catalog owner — or hand the generated SQL to one |

### On your machine

| Tool | Version | For |
|---|---|---|
| [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) | ≥ 0.239 | Bundle deploy, grants, app start |
| Python | ≥ 3.11 | The deploy and seed scripts |
| Node.js | ≥ 18 | Only to rebuild the SPA (the built bundle is committed) |

Authenticate first:

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com -p <profile>
databricks current-user me -p <profile>      # verify
```

> **Windows:** the python.org installer ships `python.exe`, not `python3.exe`.
> Every `python3 ...` below works as `python ...` (or `py -3 ...`).

---

## Step 1 — Provision Lakebase

The app stores portfolio state in a Lakebase (Autoscaling Postgres) project.

```bash
databricks postgres create-project grid-atlas-db -p <profile>
```

Or create it in the UI: **Compute → Lakebase → Create project**, named
`grid-atlas-db`.

`scripts/deploy.py` auto-detects the endpoint host once the project exists. To
find it yourself:

```bash
databricks postgres list-endpoints projects/grid-atlas-db/branches/production \
  -p <profile> -o json
```

---

## Step 2 — Deploy

```bash
python3 scripts/deploy.py
```

It prompts for: CLI profile, bundle target, warehouse ID, discovery catalog and
schema, serving endpoint, Genie mirror catalog/schema, Lakebase project and
database. Defaults pre-fill from your last run.

Then it: writes `app.yaml` + `databricks.yml` → builds the frontend (if its source
is present) → `bundle deploy` → **resolves the app's service principal and sets
`PGUSER`, then re-deploys** → runs the UC GRANTs → seeds Lakebase → starts the app.

> **Why the second deploy?** The app authenticates to Postgres *as its own service
> principal*, so `PGUSER` must be that principal's client id — which only exists
> after the first deploy creates it. This is the step that is easiest to get wrong
> by hand.

Non-interactive:

```bash
python3 scripts/deploy.py --yes \
  --profile <profile> --target prod \
  --warehouse-id <warehouse-id> \
  --atlas-catalog <catalog> --atlas-schema grid_atlas_discovery \
  --serving-endpoint databricks-claude-sonnet-4-5 \
  --lakebase-project grid-atlas-db
```

Useful flags: `--dry-run`, `--skip-build`, `--skip-grants`, `--skip-seed`,
`--skip-start`, `--seed-demo`.

---

## Step 3 — Verify with the Setup console

Open the app and go to **`/console`**. The *Setup & health* view probes every
dependency and shows a pill per check.

| Check | Required | If it fails |
|---|---|---|
| Configuration | ✅ | `app.yaml` is missing `PGHOST`/`PGUSER`/warehouse. Re-run deploy. |
| Identity | — | Shows which principal the app runs as. |
| Lakebase | ✅ | Connection or DML problem. |
| Schema | ✅ | Migrations haven't run — run the seed (step 1 of the seed section). |
| Reference data | ✅ | The portfolio is empty — run the seed. |
| SQL warehouse | | The SP lacks `CAN_USE`, or the warehouse is stopped. |
| Foundation Model | | The SP lacks `CAN_QUERY`. Agents fall back to heuristics. |
| Discovery catalog | | `ATLAS_CATALOG` unset, or the SP can't create a schema in it. |
| System tables | | Optional — enables auto-detecting landed sources from lineage. |
| Genie space | | Optional — see step 6. |

**Required** failures mean the app is not usable. **Optional** ones mean a feature
is off; the portfolio still works. Each failure shows the fix, and permission
problems show the exact GRANT statements.

---

## Step 4 — Unity Catalog grants

`scripts/deploy.py` runs these when you have the privileges. If you don't, get the
full block from **`/console` → Setup → Show all GRANTs** (or
`GET /api/setup/grants`) and hand it to a metastore admin:

```sql
-- Discovery layer
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `<atlas_catalog>` TO `<app-sp>`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE
  ON SCHEMA `<atlas_catalog>`.`grid_atlas_discovery` TO `<app-sp>`;

-- Genie mirror (optional)
GRANT USE CATALOG ON CATALOG `<mirror_catalog>` TO `<app-sp>`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE
  ON SCHEMA `<mirror_catalog>`.`grid_atlas` TO `<app-sp>`;

-- System tables (optional)
GRANT SELECT ON SCHEMA system.access TO `<app-sp>`;
```

Plus, in the UI (these can't be expressed as SQL):

- **SQL Warehouses → `<warehouse>` → Permissions →** `CAN USE` for the app SP
- **Serving → `<endpoint>` → Permissions →** `CAN QUERY` for the app SP

Click **Re-check** in the console afterward.

---

## Step 5 — Discover your data (optional)

Skip this to use only the curated 146-module catalog.

**5a. Extract metadata.** Runs on *your* machine under *your* credentials, so it
reaches workspaces the app cannot. Metadata only — no table contents.

```bash
cd schema-extractor
pip install -r requirements.txt
$EDITOR workspaces.txt          # one workspace URL per line
python3 extract_schemas.py
```

Output lands in `schema-extractor/output/`. See
[`schema-extractor/README.md`](schema-extractor/README.md) for options such as
`--no-columns` on very large estates.

**5b. Run the pipeline** in `/console` → *Discovery*, in order:

1. **Create discovery tables** — one click, idempotent.
2. **Upload** `all_schemas.csv`, `all_tables.csv`, and optionally
   `all_columns.csv` (columns markedly improve AI accuracy).
3. **AI enrichment** — start with a capped run (`Max tables = 500`) to sanity-check
   quality and cost before enriching everything.
4. **Normalize source systems** — deterministic matching first; only the long tail
   costs an LLM call. *Deterministic only (free)* skips the model entirely.
5. **Attribute to the catalog** — links discovered tables to modules. Leave
   *Advance ingestion status* off for a first pass; turning it on moves readiness
   and therefore the roadmap.

**5c. Review** in *Source mapping*. Anything low-confidence or unmapped is listed;
correcting it pins that mapping permanently.

> **Cost note.** Enrichment spend scales with table count. A one-time pass over
> ~50k tables is on the order of a few hours of pay-per-token usage. Check
> `system.billing.usage` for actuals, and start capped.

---

## Step 6 — Genie space (optional)

Natural-language Q&A over the portfolio.

1. In the app: **Sync → Genie mirror** (or `POST /api/live/sync-genie`) to
   materialize the portfolio into `GENIE_MIRROR_CATALOG.GENIE_MIRROR_SCHEMA`.
2. In Databricks: **Genie → New space**, add those tables.
3. Copy the space id into `GENIE_SPACE_ID` in `app.yaml` and redeploy.

---

## Manual deploy

If you'd rather not use `scripts/deploy.py`, two files must agree:
`app.yaml` (app runtime env) and `databricks.yml` (bundle variables).

```bash
# 1. Edit app.yaml: PGHOST, PGUSER, ATLAS_CATALOG, warehouse id, ...
# 2. Deploy
databricks bundle deploy -t prod -p <profile> \
  --var="warehouse_id=<id>" --var="atlas_catalog=<catalog>"
# 3. Find the app's service principal, put it in PGUSER, and redeploy
databricks apps get grid-atlas -p <profile> -o json
# 4. Run the GRANTs from step 4
# 5. Seed, as the Lakebase owner
python3 scripts/seed_clean.py --profile <profile> --project grid-atlas-db --db app
# 6. Start
databricks bundle run grid_atlas -t prod -p <profile>
```

`ATLAS_CATALOG` in `app.yaml` and `var.atlas_catalog` in `databricks.yml` must
match; likewise the warehouse id. The deploy script keeps them in sync for you.

---

## Local development

No workspace, database, or network needed:

```bash
python3 tests/serve_local.py --port 8000
# http://127.0.0.1:8000/console   — operator console
# http://127.0.0.1:8000/docs      — OpenAPI
```

This serves the real app with stubbed drivers and an in-memory fixture, which also
makes the "nothing configured yet" state easy to inspect deliberately.

Run the tests:

```bash
python3 -m unittest discover -s tests -v      # 281 tests, stdlib only
```

To work on the portfolio SPA you need `frontend/src/`, which is not in this repo —
see the README's note on the frontend source.

---

## Troubleshooting

**Every `/api/*` call returns 403.** The app SP lacks warehouse or catalog access.
`/console` → Setup names the failing probe and gives the GRANT.

**`ai_query()` fails.** The SP needs `CAN_QUERY` on the endpoint. Agents degrade to
heuristics meanwhile and label their output as such.

**Setup says "schema managed by install step".** Expected and benign. The app SP
holds DML but not ownership, so it can't run DDL. The seed (run as the DB owner)
owns the schema.

**The portfolio is empty.** The seed hasn't run. See step 1 of the seed section in
the README.

**Enrichment finds no candidates.** Everything is already enriched, or no tables
were uploaded. Check *Discovery → Inventory* counts.

**Normalization maps everything to "Other".** The canonical vocabulary comes from
`data_assets.source_category`, so the reference catalog must be seeded first.

**A use case reads as ready but its data isn't landed.** Check
`requirement_model` in the readiness response. On the domain path, *any* serving
source satisfies a need — if you need one specific module, hand-edit the use case's
module requirements, which sets `requires_locked` and restores strict matching.

**Deploy can't auto-detect the Lakebase host.** The project doesn't exist yet — do
step 1, or pass `--lakebase-host`.

---

## Upgrading

```bash
git pull
python3 scripts/deploy.py --yes --skip-seed --profile <profile> --target prod \
  --warehouse-id <id> --atlas-catalog <catalog>
```

Migrations are idempotent and applied on startup, so a schema addition lands
automatically. Re-running the seed is **destructive** (it truncates and reloads the
portfolio) — use `--skip-seed` on an instance with real data.

---

## Uninstalling

```bash
databricks bundle destroy -t prod -p <profile>       # app + bundle resources
databricks postgres delete-project grid-atlas-db -p <profile>   # portfolio state
```

Then drop the discovery schema if you created one:

```sql
DROP SCHEMA IF EXISTS `<atlas_catalog>`.`grid_atlas_discovery` CASCADE;
DROP SCHEMA IF EXISTS `<mirror_catalog>`.`grid_atlas` CASCADE;
```
