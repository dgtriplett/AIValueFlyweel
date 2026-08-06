# Grid Atlas — P&U Data & AI Catalog, Value & Roadmap

A Databricks App for **Power & Utilities** organizations that connects two halves
of the same question:

- **What data do we actually have?** Sweep your Unity Catalog estate across every
  workspace, enrich it with AI, and normalize a thousand free-text source labels
  down to the ~50 systems you really run.
- **What is it worth, and what should we build next?** Manage a use-case
  portfolio, quantify value from your own assumptions, and sequence a
  dependency-respecting roadmap.

The bridge between them is a **semantic data-domain layer**: use cases declare the
data *needs* they have, sources declare the needs they *serve*, and readiness
follows. So a utility running Maximo instead of SAP PM stops showing a false gap.

Ships with a curated reference library — **240 P&U use cases, a 146-module source
catalog, 63 data domains, 34 value assumptions** — so day one is populated.

> **Powered by Databricks.** Field Engineering reference application. Provided
> as-is for evaluation and internal planning — see [`LICENSE.md`](LICENSE.md).

---

## Table of contents
- [What's inside](#whats-inside)
- [Reference architecture](#reference-architecture)
- [The pipeline](#the-pipeline)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Seed data](#seed-data)
- [Testing](#testing)
- [Documentation index](#documentation-index)
- [A note on the frontend source](#a-note-on-the-frontend-source)
- [License](#license)

---

## What's inside

### Portfolio & value
| Area | What it does |
|---|---|
| **Portfolio** | 240 reference use cases; table + kanban (by status or phase); readiness (shovel-ready / nearly / awaiting prerequisites / blocked); detail drawer with inline edit; AI "next-best use case" recommender. |
| **Value Flywheel** | Blast Radius (polar map by LOB) + Dependency Graph (drag-to-connect neighborhood editor). |
| **Value & Assumptions** | 34 global assumptions; edit one and the whole portfolio re-quantifies live. |
| **Dashboards** | Value by status/domain, readiness heatmap, asset utilization, realized-by-period. |
| **Roadmap** | AI-sequenced now/next/later waves, dependency-respecting, drag-to-edit, board-ready one-pager. |
| **Joint Funding** | Finds sources benefiting ≥2 LOBs, with combined cross-LOB value and co-funding requests. |
| **Get Started** | Excel export/import for bulk offline population. |
| **Genie assistant** | NL Q&A over the portfolio (needs a Genie space — see [`INSTALL.md`](INSTALL.md)). |

### Discovery & agents *(the console, at `/console`)*
| Area | What it does |
|---|---|
| **Setup & health** | Probes every dependency — Lakebase, warehouse, Unity Catalog, serving endpoint, system tables, Genie — and prints the exact GRANT statements for anything failing. |
| **Discovery** | Upload multi-workspace metadata, AI-enrich it, normalize source systems, attribute discovered tables to catalog modules. |
| **Source mapping** | Review and correct the source-system labels the normalizer wasn't confident about. A human correction is permanent. |
| **Data domains** | 63 semantic data needs with per-domain satisfaction, plus gaps ranked by the value they block. |
| **Taxonomy** | AI classification across integration pattern, criticality, and vendor type. Effective-dated, so history survives a reclassification. |
| **Generate use cases** | Author new use cases grounded in real data availability, with a `ready` / `gap` lens. Preview → approve → create; nothing is written without an explicit yes. |

---

## Reference architecture

One **Databricks App** (FastAPI serving a pre-built React SPA plus the console),
backed by **Lakebase** for portfolio state and **Unity Catalog** for the discovery
layer, with the **Foundation Model API** behind the agents.

```
                        ┌───────────────────────────────┐
                        │  User (browser)                │
                        │  /          portfolio SPA      │
                        │  /console   operator console   │
                        └───────────────┬────────────────┘
                                        │ HTTPS (Databricks App auth)
┌───────────────────── Databricks App: "grid-atlas" ──────────────────────────┐
│  FastAPI (app.py)                                                            │
│   /api/*  routers (server/routes/)                                           │
│     PORTFOLIO  lobs · use_cases · data_assets · dependencies · values ·      │
│                roadmap · joint_funding · value_assumptions · analytics ·     │
│                impact · comments · funding_requests · onboarding · genie     │
│     DISCOVERY  ingestion · domains · taxonomy · setup                        │
│     AGENTS     agents · generate · confirm                                   │
│   Domain logic: value_engine · readiness · phase · normalization ·           │
│                 enrichment · generation · taxonomy · confirm · lineage       │
│   Auth: dual-mode — service principal in-app, CLI profile locally            │
└────────┬──────────────────┬───────────────────────────┬─────────────────────┘
         │ asyncpg          │ Statement Execution API   │ chat completions /
         │ (OAuth token     │ (bound SQL warehouse)     │ ai_query()
         │  as PG password) │                           │
         ▼                  ▼                           ▼
 ┌────────────────┐  ┌──────────────────────────┐  ┌────────────────────┐
 │ Lakebase       │  │ Unity Catalog             │  │ Foundation Model   │
 │ (Postgres)     │  │  • system.information_    │  │ API                │
 │                │  │    schema (via extractor) │  │                    │
 │ PORTFOLIO      │  │  • system.access.lineage  │  │ AGENTS             │
 │  use_cases     │  │  • system.lakeflow/serving│  │  • generate UCs    │
 │  data_assets   │  │                           │  │  • detect deps     │
 │  data_domains  │  │ DISCOVERY (ATLAS_CATALOG) │  │  • estimate value  │
 │  edges, values │  │  discovered_schemas       │  │  • next-best UC    │
 │  roadmap       │  │  discovered_tables        │  │  • roadmap waves   │
 │  taxonomy      │  │  enrichment staging       │  │  • canonicalize    │
 │  aliases       │  │                           │  │  • classify        │
 │  confirm_tokens│  │ Genie mirror (portfolio)  │  │                    │
 └────────────────┘  └──────────────────────────┘  └────────────────────┘
   your workspace       your catalog, your govern.    heuristic fallback
```

**Key properties**

- **Your data stays in your workspace.** Portfolio state is in *your* Lakebase;
  discovered metadata is in *your* Unity Catalog. Nothing is exfiltrated.
- **Metadata only.** Discovery reads catalog/schema/table/column *names*,
  comments, and owners. No table contents are ever queried.
- **Least privilege.** The app SP holds Postgres DML (no ownership → no DDL),
  `CAN_USE` on one warehouse, `CAN_QUERY` on one endpoint, and explicit grants on
  the two schemas it writes. Schema DDL belongs to the install step.
- **Human-in-the-loop writes.** Agent-proposed changes go through a single-use
  confirm token with the payload stored server-side, so the client's authority is
  one bit: yes or no to what it was shown.
- **Graceful degradation, everywhere.** No Lakebase → demo mode. No
  `ATLAS_CATALOG` → discovery disabled, curated catalog still works. No serving
  endpoint → heuristic agents. No Genie space → placeholder. Nothing cascades.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the layer-by-layer breakdown.

---

## The pipeline

```
 schema-extractor/          CSV            AI              canonical
 (your credentials,   →  upload    →   enrichment   →   normalization
  every workspace)                    (staged ai_query)  (5-stage cascade)
                                                                 │
                                                                 ▼
    use_case ──requires──▶ data_domain ◀──serves── data_asset ◀─ attribution
        │                       │
        │                       ▼
        └──────────────▶  readiness  ──▶ value engine ──▶ roadmap waves
                                 ▲
                    generation agent (lens: ready | gap)
```

**Ingest → Canonicalize → Domains → Generate → Value → Sequence.** Each stage is a
separate idempotent endpoint, so a failure never forces a restart from zero, and
each reports what it *changed* rather than only that it finished.

Two details worth knowing, both of which cost real money or credibility to get
wrong:

- **AI enrichment is staged.** A single `MERGE` that calls `ai_query()` and parses
  several fields out of the response invokes the model *once per output field*
  (~4x cost), because the `from_json` projection gets pushed down. So stage 1
  persists raw responses to a staging table and stage 2 merges with no LLM calls —
  which also makes stage 2 replayable for free. See
  [`server/enrichment.py`](server/enrichment.py).
- **The generation lens is verified, not trusted.** A use case is only "ready" if
  every domain it requires is actually satisfied. The model's own claim is
  recomputed against real state and corrected, with a warning, because that claim
  is the entire basis of the recommendation.

---

## Tech stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18 · TypeScript · Vite · TailwindCSS (Databricks Blueprint dark) · TanStack Query · React Flow · Recharts · dagre. Plus a dependency-free operator console. |
| **Backend** | FastAPI · `asyncpg` (pooled, OAuth-token password, ~45-min refresh) · Uvicorn. Lakebase SQL is parameterized. |
| **Portfolio state** | Lakebase — Autoscaling Postgres in your workspace. Migrations in [`server/migrations/`](server/migrations/). |
| **Discovery state** | Unity Catalog Delta tables in `ATLAS_CATALOG.ATLAS_SCHEMA`. |
| **AI** | Foundation Model API — chat completions for the agents, `ai_query()` for batch enrichment. Strict JSON schemas; heuristic fallback. |
| **Packaging** | Databricks Asset Bundle + [`scripts/deploy.py`](scripts/deploy.py). |
| **Runtime** | Python 3.11+, Node 18+ (only to rebuild the SPA). |

---

## Repository layout

```
.
├── app.py                      # FastAPI entry: /api routers, SPA, console
├── app.yaml                    # App runtime config (ships with no environment baked in)
├── databricks.yml              # Asset Bundle — every value is a variable
├── requirements.txt
├── server/
│   ├── config.py               # Dual-mode auth + settings
│   ├── db.py                   # asyncpg pool + OAuth refresh
│   ├── value_engine.py         # Parameterized value model
│   ├── readiness.py            # Dual-path readiness (domain / module)
│   ├── normalization.py        # 5-stage source-system canonicalization
│   ├── enrichment.py           # Staged ai_query() SQL builders
│   ├── generation.py           # Use-case prompts + candidate validation
│   ├── taxonomy.py             # 3-dimension classification
│   ├── confirm.py              # Single-use propose/confirm tokens
│   ├── lineage.py, live.py     # System-table reads + Genie mirror
│   ├── migrations/             # 001_init · 002_domains · 003_discovery · 004_agents
│   └── routes/                 # One module per domain area
├── scripts/
│   ├── deploy.py               # One-shot deploy (interactive or scripted)
│   ├── seed_clean.py           # Clean day-1 seed
│   ├── seed_demo.py            # Populated walkthrough seed
│   ├── seed_lib.py             # Shared loader + domain derivation
│   ├── pu_catalog.py           # 146-module P&U source catalog
│   └── pu_domains.py           # 63-domain vocabulary + module mappings
├── schema-extractor/           # Standalone multi-workspace metadata sweep
├── frontend/
│   ├── dist/                   # Pre-built SPA (COMMITTED — served by the app)
│   └── console/                # Operator console (plain HTML/JS, no build)
├── tests/                      # 281 stdlib-only tests + a local dev server
└── ARCHITECTURE.md · INSTALL.md · DEMO_MODE.md · DELTA_SHARING.md · LICENSE.md
```

---

## Quick start

```bash
git clone <this-repo> && cd grid-atlas
python3 scripts/deploy.py
```

The script prompts for everything, then: writes `app.yaml` + `databricks.yml`,
builds the frontend if its source is present, deploys the bundle, resolves the
app's auto-created service principal, runs the Unity Catalog GRANTs, seeds
Lakebase, and starts the app.

Add `--dry-run` first to see every command it would run without running any.

Non-interactive:

```bash
python3 scripts/deploy.py --yes \
  --profile <cli-profile> --target prod \
  --warehouse-id <warehouse-id> \
  --atlas-catalog <catalog> \
  --lakebase-project grid-atlas-db
```

Then open the app and go to **`/console`** → *Setup & health*. Every dependency is
probed there, with copy-pastable GRANTs for anything missing.

Full manual steps are in [`INSTALL.md`](INSTALL.md).

---

## Configuration

Everything is environment-specific and set via `app.yaml` env (or bundle
variables). **No secrets, and nothing baked in** — the committed `app.yaml` ships
with empty values.

| Var | Purpose | Required |
|---|---|---|
| `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` | Lakebase. `PGUSER` **must be the app's service-principal client id** — the app authenticates as itself. | yes |
| `DATABRICKS_WAREHOUSE_ID` | Warehouse for system tables, UC discovery, `ai_query()`. From the bound resource. | yes |
| `SERVING_ENDPOINT` | Foundation Model endpoint. | agents |
| `ATLAS_CATALOG` / `ATLAS_SCHEMA` | Where the discovery layer writes. Blank disables discovery. | discovery |
| `GENIE_SPACE_ID` | Genie space over the portfolio mirror. | Genie |
| `GENIE_MIRROR_CATALOG` / `GENIE_MIRROR_SCHEMA` | UC target for the mirror. | Genie |
| `DEMO_MODE` | Header toggle + `/api/demo/*`. **Ship `off`** — it can reset the portfolio. | no |

---

## Seed data

Both seeds are idempotent and switchable at any time.

```bash
# Clean day-1 (shipped default): full catalog + domains, nothing ingested
python3 scripts/seed_clean.py --profile <profile> --project grid-atlas-db --db app

# Populated walkthrough: in-flight statuses, realized value, roadmap, funding
python3 scripts/seed_demo.py  --profile <profile> --project grid-atlas-db --db app
```

The seed also **derives all domain requirements from the 693 module edges** — so
the domain layer is useful immediately without anyone re-authoring 240 use cases.

---

## Testing

```bash
python3 -m unittest discover -s tests -v      # 281 tests, ~0.15s
```

Standard library only — no pytest, no containers, no database, no network. See
[`tests/README.md`](tests/README.md) for what each file covers and why the DB is
faked rather than provisioned.

To work on the UI without a workspace:

```bash
python3 tests/serve_local.py --port 8000       # then open /console
```

---

## Documentation index

| Doc | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Step-by-step deploy, manual and scripted, including the GRANTs. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layer-by-layer architecture, data flow, security posture. |
| [`schema-extractor/README.md`](schema-extractor/README.md) | The multi-workspace metadata sweep. |
| [`tests/README.md`](tests/README.md) | Test suite map and testing approach. |
| [`DEMO_MODE.md`](DEMO_MODE.md) | The isolated, removable Demo Mode feature. |
| [`DELTA_SHARING.md`](DELTA_SHARING.md) | Reference-library IP and distribution model. |
| [`LICENSE.md`](LICENSE.md) | License and terms. |

---

## A note on the frontend source

This repo ships the **pre-built** SPA in `frontend/dist/`, which is what the app
serves. The TypeScript source (`frontend/src/`) was not part of the exported
workspace folder and is therefore **not** included — the committed `dist/` bundle
is the source of record for the portfolio UI.

That is why the discovery and agent screens ship as a separate, dependency-free
page in `frontend/console/`: adding them to the SPA would mean reverse-engineering
a minified bundle, and a mistake there would break a UI that already works. The
build config (`package.json`, `vite.config.ts`, `tailwind.config.js`,
`tsconfig*.json`) is all present, so restoring `frontend/src/` is enough to make
the SPA buildable again.

---

## License

See [`LICENSE.md`](LICENSE.md). Application code is provided as-is for evaluation
and internal planning. The bundled **reference library** is Databricks IP —
directional guidance; validate against your own data before any financial
decision. Your instance data is yours.
