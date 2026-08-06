# Value Flywheel — P&U Use Case, Value & Roadmap Portfolio

A Databricks App for **Power & Utilities** organizations to manage their data + AI
**use-case portfolio**, quantify **value** (hypothesized and realized), map the
**dependency flywheel** ("deploy A → B/C/D become fast-follows"), and build a
**dependency-respecting roadmap** — with AI agents and live Databricks integration.

Ships with a curated **reference library** (198 P&U use cases, a 52-source data
catalog, value-model templates, and benchmark ranges) so a utility can start on
day one and populate their own reality on top.

> **Powered by Databricks.** Field Engineering reference application. Provided as-is
> for evaluation and internal planning — see [`LICENSE.md`](LICENSE.md).

---

## Table of contents
- [What's inside](#whats-inside)
- [Reference architecture](#reference-architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Seed data — two modes](#seed-data--two-modes)
- [Configuration](#configuration)
- [The flywheel — how it works](#the-flywheel--how-it-works)
- [Documentation index](#documentation-index)
- [A note on the frontend source](#a-note-on-the-frontend-source)
- [License](#license)

---

## What's inside

| Area | What it does |
|---|---|
| **Get Started** | Excel export/import (bulk populate offline) + consent-gated auto-populate from your Databricks system tables. |
| **Portfolio** | 198 reference use cases; table + kanban (by status or phase); readiness (shovel-ready / nearly / blocked); rich detail drawer with inline edit; AI "next-best use case" recommender. |
| **Data Assets** | Curated source catalog decomposed to module level; generic `source_category` drives, `vendor` optional; one-click ingestion status; provenance (catalog / auto / custom); AI source decomposition; "Sync from Databricks". |
| **Value Flywheel** | Blast Radius (polar map by LOB, 3-color relationship network) + Dependency Graph (selector-first neighborhood editor, drag-to-connect). |
| **Dashboards** | Value by status/domain, readiness heatmap, asset utilization, realized-by-period — driven by the parameterized value engine. |
| **Roadmap** | AI-sequenced now/next/later waves (dependency-respecting), drag-to-edit, board-ready exec one-pager (print/PDF). |
| **Joint Funding** | Finds data sources benefiting ≥2 LOBs with combined cross-LOB value; co-funding requests. |
| **Value & Assumptions** | 34 global assumptions; edit any and the whole portfolio re-quantifies live. |
| **Genie assistant** | Floating NL Q&A over the portfolio (needs a Genie space — see [`INSTALL.md`](INSTALL.md)). |

---

## Reference architecture

A single **Databricks App** (FastAPI backend serving a pre-built React SPA) backed by
**Lakebase** (Autoscaling Postgres) for instance state, with optional **read-only**
integration to Unity Catalog system tables and the **Foundation Model API** for the
AI agents.

```
                                  ┌───────────────────────────┐
                                  │   User (browser)           │
                                  │   Databricks App URL        │
                                  └─────────────┬──────────────┘
                                                │  HTTPS (Databricks App auth)
                                                ▼
┌──────────────────────── Databricks App: "value-flywheel" ────────────────────────┐
│                                                                                    │
│   FastAPI  (app.py)                                                                │
│   ┌──────────────────────────────────────────────────────────────────────────┐  │
│   │  /api/*  REST routers (server/routes/)                                     │  │
│   │    lobs · data_assets · use_cases · dependencies · values · roadmap ·      │  │
│   │    comments · funding_requests · impact · value_assumptions · agents ·     │  │
│   │    analytics · live · onboarding · joint_funding · source_recommendations ·│  │
│   │    genie · demo (gated)                                                    │  │
│   ├──────────────────────────────────────────────────────────────────────────┤  │
│   │  Domain logic:  value_engine.py · readiness.py · phase.py · lineage.py ·   │  │
│   │                 live.py · llm.py                                            │  │
│   ├──────────────────────────────────────────────────────────────────────────┤  │
│   │  Static SPA  →  frontend/dist  (React 18 build; served for all non-/api)   │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
│   Auth: dual-mode — service principal in-app · CLI profile when run locally        │
└──────────┬─────────────────────┬──────────────────────────┬───────────────────────┘
           │                     │                           │
   asyncpg │ (OAuth token        │ Statement Execution API   │ OpenAI-compatible
     pool  │  as PG password)    │ (bound SQL warehouse)     │ chat completions
           ▼                     ▼                           ▼
   ┌────────────────┐   ┌──────────────────────────┐   ┌──────────────────────┐
   │  Lakebase      │   │  Unity Catalog            │   │  Foundation Model API │
   │  (Postgres)    │   │  • system.access.*        │   │  (serving endpoint)   │
   │                │   │    table_lineage          │   │                       │
   │  App state:    │   │  • system.lakeflow.*      │   │  5 AI agents:         │
   │  use_cases,    │   │  • system.serving.*       │   │  • dependency detect  │
   │  data_assets,  │   │  • system.billing.usage   │   │  • next-best UC       │
   │  edges, values,│   │                           │   │  • roadmap generator  │
   │  roadmap, …    │   │  Genie mirror tables      │   │  • value estimator    │
   │                │   │  (portfolio → UC schema)  │   │  • source decompose   │
   └────────────────┘   └──────────────────────────┘   └──────────────────────┘
   your workspace,       READ-ONLY · consent-gated ·      heuristic fallback if
   your data             degrades gracefully              endpoint unavailable

   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   Reference library (Databricks IP) — see DELTA_SHARING.md
   Provider catalog ──Delta Share (grant / revoke / versioned)──► seeded once into
   db_ip_reference                                                your Lakebase
   (ref_use_cases, ref_data_sources, ref_value_models, ref_benchmarks)
```

**Key properties**
- **Your data stays in your workspace.** Instance state lives in *your* Lakebase; the
  app does not exfiltrate it.
- **Least privilege.** The app service principal holds Postgres DML (no table
  ownership → no DDL), `CAN_USE` on one SQL warehouse, and only an explicit,
  documented grant for the optional Genie mirror. Schema DDL is owned by the
  install/seed step, run as the database owner.
- **Consent-gated, read-only live reads.** "Sync from Databricks" / "Auto-populate"
  only query system tables with per-use consent and never write to them. Every
  mutation to app state writes an `audit_log` row.
- **Graceful degradation.** No Lakebase → demo mode; no system tables → Excel path;
  no serving endpoint → heuristic agent fallback; no Genie space → placeholder.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for a layer-by-layer breakdown.

---

## Tech stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18 · TypeScript · Vite · TailwindCSS (Databricks Blueprint dark theme) · TanStack Query · React Flow (`@xyflow/react`) · Recharts · dagre. Heavy views are code-split / lazy-loaded. |
| **Backend** | FastAPI · `asyncpg` (pooled, OAuth-token password, ~45-min refresh) · Uvicorn. All SQL is parameterized. |
| **State** | Lakebase — Autoscaling Postgres in your workspace. Schema: [`server/migrations/001_init.sql`](server/migrations/001_init.sql). |
| **AI** | Foundation Model API via the OpenAI-compatible client (default `databricks-claude-sonnet-4-5`). |
| **Live** | Unity Catalog system tables read via the Statement Execution API over a bound SQL warehouse. |
| **Packaging** | Databricks Asset Bundle ([`databricks.yml`](databricks.yml)) — one-command deploy. |
| **Runtime** | Python 3.11+ (dev pinned to 3.12), Node 18+ to build the frontend. |

---

## Repository layout

```
.
├── app.py                      # FastAPI entry point: mounts /api routers + serves SPA
├── app.yaml                    # Databricks App runtime config (command + env + resources)
├── databricks.yml              # Asset Bundle — one-command deploy (dev/prod targets)
├── pyproject.toml              # Python project metadata + deps
├── requirements.txt            # Runtime Python deps (Databricks App install)
├── server/                     # FastAPI backend
│   ├── config.py               # Dual-mode auth (in-app SP vs. local CLI profile)
│   ├── db.py                   # asyncpg pool + OAuth-token refresh loop
│   ├── value_engine.py         # Parameterized value model (hypothesized + realized)
│   ├── readiness.py            # Shovel-ready / nearly / blocked scoring
│   ├── phase.py                # Use-case phase helpers
│   ├── lineage.py, live.py     # Read-only UC system-table integration
│   ├── llm.py                  # Foundation Model API client + heuristic fallback
│   ├── migrations/001_init.sql # Full Lakebase schema (owned by install step)
│   └── routes/                 # REST routers (one module per domain area)
├── scripts/                    # Owner-side seed + data-build utilities
│   ├── seed_clean.py           # Clean day-1 seed (reference library, nothing ingested)
│   ├── seed_demo.py            # Populated walkthrough seed
│   ├── seed_lib.py             # Shared seed helpers + dataset
│   ├── seed_data.json          # Reference portfolio dataset
│   ├── pu_catalog.py           # P&U reference catalog definition
│   └── build_seed_from_parent.py, derive_requires.py, apply_manual_override_cols.py
├── frontend/                   # React SPA
│   ├── dist/                   # Pre-built bundle (COMMITTED — served by the app)
│   ├── package.json            # Frontend deps + build scripts
│   ├── vite.config.ts, tailwind.config.js, tsconfig.json, index.html
│   └── (src/ — see note below)
├── ARCHITECTURE.md             # Layer-by-layer architecture
├── INSTALL.md                  # Step-by-step deploy into your workspace
├── DEMO_MODE.md                # The isolated, removable Demo Mode feature
├── DELTA_SHARING.md            # Reference-library IP / distribution model
└── LICENSE.md                  # License & terms
```

---

## Quick start

Full instructions are in [`INSTALL.md`](INSTALL.md). The short version (~15 min into
**your own** serverless Databricks workspace):

```bash
# 1. Provision Lakebase (app state) — see INSTALL.md step 1
# 2. Build the frontend (only if you have frontend/src; the built dist/ is committed)
cd frontend && npm ci && npm run build && cd ..

# 3. Configure app.yaml / databricks.yml variables (Lakebase host, warehouse id, …)

# 4. Deploy with the Asset Bundle
databricks bundle deploy -t prod --var="warehouse_id=<your-warehouse-id>"
databricks bundle run value_flywheel -t prod

# 5. Create schema + seed (as the Lakebase owner)
python scripts/seed_clean.py --profile <profile> --project value-flywheel-db --db app
```

---

## Seed data — two modes

Both seeds are **idempotent** and re-runnable; switch anytime.

- **Clean day-1** (shipped default): full portfolio + full data-asset catalog pre-loaded and pristine — everything visible, nothing yet ingested / in-flight.
  ```bash
  python scripts/seed_clean.py --profile <your-cli-profile> --project <lakebase-project> --db app
  ```
- **Full demo** (populated in-flight / realized state, for walkthroughs):
  ```bash
  python scripts/seed_demo.py  --profile <your-cli-profile> --project <lakebase-project> --db app
  ```

The live app can also flip between these states on demand via **Demo Mode** — see
[`DEMO_MODE.md`](DEMO_MODE.md) (isolated, gated behind `DEMO_MODE`, default OFF).

---

## Configuration

All configuration is workspace-specific and set via `app.yaml` env (or bundle
variables in `databricks.yml`). No secrets live in the repo.

| Var | Purpose |
|---|---|
| `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` | Lakebase connection (from the bound `database` resource + app SP client id) |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse for system-table + live reads (from the bound `sql-warehouse` resource) |
| `SERVING_ENDPOINT` | Foundation Model chat endpoint (default `databricks-claude-sonnet-4-5`) |
| `GENIE_SPACE_ID` | Genie space over the portfolio mirror (optional) |
| `GENIE_MIRROR_CATALOG` / `GENIE_MIRROR_SCHEMA` | Unity Catalog target for the Genie mirror |
| `DEMO_MODE` | Enables the header Demo Mode toggle + `/api/demo/*` endpoints (default OFF) |

> **Note:** The `app.yaml` committed here contains the values for the current
> Field Engineering demo instance (Lakebase host, service-principal id, warehouse id,
> catalog, and `DEMO_MODE: "on"`). None are secrets, but for your own deployment
> replace them with your workspace's values as described in [`INSTALL.md`](INSTALL.md).

---

## The flywheel — how it works

1. **Register / curate** data sources (catalog) → mark ingestion status (manual, Excel, or auto from lineage).
2. **Readiness recomputes** → use cases become shovel-ready.
3. **Deliver a use case** (status → live) → its required data is captured as landed → dependents' readiness jumps → "unlocks" are surfaced.
4. **Blast radius + recommender** narrate the fast-follows; the roadmap sequences waves.
5. **Value engine** quantifies hypothesized → realized as assumptions and status evolve.

---

## Documentation index

| Doc | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Step-by-step deploy into your workspace (Lakebase, build, configure, bundle deploy, seed, Genie, live). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layer-by-layer architecture, data flow, and security posture. |
| [`DEMO_MODE.md`](DEMO_MODE.md) | The isolated, removable Demo Mode feature and how it is gated. |
| [`DELTA_SHARING.md`](DELTA_SHARING.md) | Reference-library IP model and Delta Sharing distribution. |
| [`LICENSE.md`](LICENSE.md) | License & terms. |

---

## A note on the frontend source

This repository ships the **pre-built** React SPA in `frontend/dist/`, which is what
the Databricks App actually serves. The **TypeScript/React source** (`frontend/src/`)
was **not** part of the exported Databricks workspace folder and is therefore not
included here. The committed `dist/` bundle is the source of record for the UI.

To restore full frontend buildability, add the `frontend/src/` tree back (the
`package.json`, `vite.config.ts`, `tailwind.config.js`, and `tsconfig*.json` build
config are all present); `index.html` expects an entry point at `/src/main.tsx`.

---

## License

See [`LICENSE.md`](LICENSE.md). Application code is provided as-is for evaluation and
internal planning. The bundled **reference library** is Databricks IP (directional
guidance — validate against your own data before any financial decision). Your
instance data is yours.
