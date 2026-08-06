# Install — Value Flywheel

Deploy into **your own** Databricks workspace. ~15 minutes.

## Prerequisites
- A **serverless** Databricks workspace (required for Lakebase + Foundation Models).
- Databricks CLI **>= 0.239** authenticated to your workspace:
  `databricks auth login --host https://<your-workspace-host>`
- A **SQL warehouse** (any size) — note its ID.
- Permission to create **Apps**, a **Lakebase (Autoscaling Postgres)** project, and (for live integration) read **system tables**.
- Node 18+ and Python 3.11+ locally (to build the frontend and seed).

## 1. Provision Lakebase (app state)
```bash
databricks postgres create-project value-flywheel-db \
  --json '{"spec": {"display_name": "Value Flywheel DB"}}' -p <profile>
# wait until READY/ACTIVE:
databricks postgres list-endpoints projects/value-flywheel-db/branches/production -p <profile> -o json \
  | jq -r '.[0].status.hosts.host'
```
Create a Postgres role for the app's service principal (get the SP client id from
`databricks apps get value-flywheel`), then grant it CONNECT + CRUD on the `app`
database (see `scripts/grant_app_sp.sql` for the exact statements).

> **Schema ownership.** All schema DDL (tables, columns, constraints) is created
> **once, by you (the deploying principal, who owns the database)** in the seed
> step below — never by the running app. The app authenticates as its own service
> principal, which holds DML (SELECT/INSERT/UPDATE/DELETE) but **not** table
> ownership, so it cannot and does not run DDL. On boot the app makes a best-effort
> idempotent schema check; if it lacks DDL privilege it logs a single benign
> `schema managed by install step … continuing` line and serves normally. This is
> expected — no action needed.

## 2. Build the frontend
```bash
cd frontend && npm ci && npm run build && cd ..
```

## 3. Configure
Edit `app.yaml` (or set bundle variables in `databricks.yml`):
- `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER` → your Lakebase endpoint + app SP client id
- bind a `sql-warehouse` resource to **your** warehouse id. **The bound resource's
  name must be exactly `sql-warehouse`** — that is the key `app.yaml` reads via
  `valueFrom: "sql-warehouse"` to populate `DATABRICKS_WAREHOUSE_ID`. The bundle
  (`databricks.yml`) already binds it under that name; if you instead attach a
  warehouse through the UI, name the resource `sql-warehouse` (a mismatched name
  produces a benign `resource sql-warehouse not found` build log and an unset
  warehouse id — see note in step 4).
- `SERVING_ENDPOINT` → a chat model your workspace has (default Claude Sonnet)
- `GENIE_MIRROR_CATALOG`/`GENIE_MIRROR_SCHEMA` → a catalog the app SP can write

## 4. Deploy (one command)
```bash
databricks bundle deploy -t prod \
  --var="warehouse_id=<your-warehouse-id>"
databricks bundle run value_flywheel -t prod   # starts the app
```
Or without bundles:
```bash
databricks sync . /Workspace/Users/<you>/value-flywheel \
  --exclude node_modules --exclude .venv --exclude __pycache__ --exclude .git \
  --exclude frontend/src --exclude frontend/node_modules -p <profile>
databricks apps create value-flywheel -p <profile>
databricks apps deploy value-flywheel \
  --source-code-path /Workspace/Users/<you>/value-flywheel -p <profile>
```

> **Recommended order for a clean first boot:** provision Lakebase (step 1) →
> grant the app SP → run the owner-side schema+seed (step 5) → deploy the app.
> Seeding before (or right after) deploy means the app's first boot finds the
> schema already present. Deploying first is also fine — the app serves in demo
> mode / benign schema-check-skipped state until the seed runs.
>
> **Benign logs (non-fatal).** On a healthy install you may still see, once at
> startup: `resource sql-warehouse not found` — only if the bound warehouse
> resource isn't named `sql-warehouse` (step 3); and, if you deploy before
> seeding, `schema managed by install step … continuing`. Neither blocks the app.

## 5. Create the schema + seed (owner-side — clean day-1 is the shipped default)
Run this **as yourself** (the principal that owns the Lakebase database). It applies
the full, current schema (`server/migrations/001_init.sql`, including every column
such as `requires_locked` and `manual`) **and** loads the reference portfolio, all
in one idempotent transaction. Safe to re-run.
```bash
python scripts/seed_clean.py --profile <profile> --project value-flywheel-db --db app
```
For a populated walkthrough dataset instead: `python scripts/seed_demo.py ...`

> Because you own the database, this is the step that owns all DDL. After it runs,
> the app's first boot finds the schema already present and serves immediately.
> (If you ever need to add only the schema without reseeding, the same migration is
> idempotent — `scripts/apply_manual_override_cols.py` is a minimal owner-side
> example that adds columns via `ADD COLUMN IF NOT EXISTS`.)

## 6. (Optional) Genie assistant
The clippy works in graceful mode until a Genie space is wired. To enable real answers:
1. In the app: **Get Started → (mirror)** or call `POST /api/live/sync-genie` to
   materialize the portfolio into `GENIE_MIRROR_CATALOG.GENIE_MIRROR_SCHEMA`.
   *The app's service principal needs UC write access to that catalog:*
   ```sql
   GRANT USE CATALOG ON CATALOG <catalog> TO `<app-sp-client-id>`;
   GRANT USE SCHEMA, CREATE TABLE, MODIFY, SELECT ON SCHEMA <catalog>.<schema> TO `<app-sp-client-id>`;
   ```
2. Create a **Genie space** over those tables (Workspace → Genie → New).
3. Set `GENIE_SPACE_ID` in `app.yaml` and redeploy.

## 7. (Optional) Live integration
"Data Assets → Sync from Databricks" and "Get Started → Auto-populate" read your
system tables (read-only, consent-gated) to advance ingestion status and mark use
cases live. If system tables aren't enabled in your workspace, the app says so and
makes no changes — use the Excel path instead.

## Switching seeds later
Re-run `scripts/seed_clean.py` (pristine) or `scripts/seed_demo.py` (populated) anytime.
