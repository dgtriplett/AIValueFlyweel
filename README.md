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
- [Operations](OPERATIONS.md)
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
| **Get started** | Guided setup: probes every dependency — Lakebase, warehouse, Unity Catalog, serving endpoint, system tables, Genie — and prints the exact GRANT statements for anything failing. Also Excel export/import for bulk offline population, and a downloadable extractor for workspaces this one cannot reach. |
| **Ask** | Chat over the portfolio with 12 typed tools. Read tools answer; write tools only ever *propose*, landing in the same confirm gate as every other agent write. |
| **Company research** | Research a utility and **recalibrate all 34 value assumptions from the findings**, each with a confidence level and its derivation. Correctly zeroes what does not apply (fuel and capacity for a wires-only utility). Nothing is applied without confirmation. |
| **Coverage** | 63 semantic data needs with per-domain satisfaction, plus gaps ranked by the value they block. |
| **Flow** | Sankey from source → domain → use case → line of business, weighted by dollars, plus a glossary that projects domains as derived terms. |
| **Catalog** | Seven surfaces: data needs, source mapping (a human correction is permanent), taxonomy, glossary, artifact inventory, classification rules, and branding. |
| **Taxonomy** | AI classification across integration pattern, criticality, and vendor type. Effective-dated, so history survives a reclassification. |
| **Generate use cases** | Author new use cases grounded in real data availability, with a `ready` / `gap` lens. Preview → approve → create; nothing is written without an explicit yes. |
| **Branding** | Set the customer's name, subtitle, accent colour, and logo, so the app reads as theirs in a workshop. Falls back to the researched company name automatically. |
| **Knowledge base** | Markdown articles in folders, full-text search, version history with restore, and binary documents (PDF/Word/Excel/PowerPoint) attached to the use cases, sources and domains they explain. Deleting a folder never deletes its articles. |
| **Write a proposal** | An eight-section proposal for one use case, grounded in *this instance's* data — the researched company, the computed value and the assumptions behind it, the real data gaps. Filed in the knowledge base, attached to the use case, versioned, and confirm-gated. |
| **Admin** | Audit log, schema state, and the operational surface below. |

### Operations
| Concern | How it is handled |
|---|---|
| **Schema changes** | Versioned migrations with a checksum ledger, applied **at most once** by [`scripts/migrate.py`](scripts/migrate.py) as the database owner. The app holds DML but not DDL and never applies a migration; it reports drift instead of guessing. |
| **Logs** | Structured JSON with a request id on every line, echoed as `X-Request-Id`. `LOG_LEVEL` turns up detail without a redeploy. OAuth tokens are redacted in the formatter, not at call sites. |
| **Rate limits** | Per-actor token buckets on the endpoints that cost money or warehouse time, plus a per-request query budget bounding one chat turn's share of the connection pool. Guard rails against accidental load, not a quota — set `RATE_LIMITS=off` for a demo. |
| **Human approval** | Every agent-initiated write goes through a single-use, server-side, 10-minute propose/confirm token. The client's authority is one bit: yes or no to what it was shown. |
| **CI** | [`scripts/check.py`](scripts/check.py) runs every gate — tests, lint, secret scan, console syntax, and a clean import against the real dependencies. The workflow only calls it, so the gates are identical locally and in CI. |

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
│   Middleware: request id → every log line + X-Request-Id                     │
│   /api/*  routers (server/routes/)                                           │
│     PORTFOLIO  lobs · use_cases · data_assets · dependencies · values ·      │
│                roadmap · joint_funding · value_assumptions · analytics ·     │
│                impact · comments · funding_requests · onboarding · genie     │
│     DISCOVERY  ingestion · domains · taxonomy · setup · inventory · flow     │
│     AGENTS     agents · generate · chat · research · confirm                 │
│     INSTANCE   branding                                                      │
│   Domain logic: value_engine · readiness · phase · normalization ·           │
│                 enrichment · generation · research · chat_tools · rules ·    │
│                 taxonomy · confirm · lineage                                 │
│   Cross-cutting: logging_setup · limits (rate + query budget) · migrator     │
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
 │  confirm_tokens│  │ Genie mirror (portfolio)  │  │  • chat (12 tools) │
 │  research_runs │  │                           │  │  • company research│
 │  branding      │  │                           │  │                    │
 │  chat_*        │  │                           │  │                    │
 │schema_migrations│ │                           │  │                    │
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
- **Schema changes are explicit.** Migrations carry a checksum and apply at most
  once, run by the install step as the database owner. Because the app cannot
  execute DDL, a restart can never replay a migration — the failure mode where a
  non-idempotent backfill silently destroys data on a reboot nobody watched.
- **Supportable.** One request id ties together every log line, the response
  header, and the 429 if there was one, so a user report maps to a specific
  request without reproducing it. Secrets are redacted in the formatter, so a
  token in an unexamined exception string does not reach the log.

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
│   ├── db.py                   # asyncpg pool + OAuth refresh + budget charging
│   ├── logging_setup.py        # JSON logs, request ids, secret redaction
│   ├── limits.py               # Per-actor rate limits + query budgets
│   ├── migrator.py             # Migration ledger, apply-once, drift detection
│   ├── value_engine.py         # Parameterized value model
│   ├── readiness.py            # Dual-path readiness (domain / module)
│   ├── normalization.py        # 5-stage source-system canonicalization
│   ├── enrichment.py           # Staged ai_query() SQL builders
│   ├── generation.py           # Use-case prompts + candidate validation
│   ├── research.py             # Company research + assumption calibration
│   ├── chat_tools.py           # 12 typed tools for the chat assistant
│   ├── rules.py                # Classification rules (first-match-wins)
│   ├── taxonomy.py             # 3-dimension classification
│   ├── confirm.py              # Single-use propose/confirm tokens
│   ├── lineage.py, live.py     # System-table reads + Genie mirror
│   ├── migrations/             # 001_init … 006_branding
│   └── routes/                 # One module per domain area
├── scripts/
│   ├── deploy.py               # One-shot deploy (interactive or scripted)
│   ├── migrate.py              # Apply migrations as the database owner
│   ├── check.py                # Every CI gate, runnable locally
│   ├── check_no_secrets.py     # Credential / workspace-value scan
│   ├── seed_clean.py           # Clean day-1 seed
│   ├── seed_demo.py            # Populated walkthrough seed
│   ├── seed_lib.py             # Shared loader + domain derivation
│   ├── pu_catalog.py           # 146-module P&U source catalog
│   └── pu_domains.py           # 63-domain vocabulary + module mappings
├── schema-extractor/           # Standalone multi-workspace metadata sweep
├── frontend/
│   ├── dist/                   # Pre-built SPA (COMMITTED — served by the app)
│   └── console/                # Operator console (plain HTML/JS, no build)
├── tests/                      # stdlib-only test suite + a local dev server
├── .github/workflows/ci.yml    # Calls scripts/check.py — no logic of its own
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
| `LOG_LEVEL` | `INFO` normally; `DEBUG` raises detail on a running app with no redeploy. | no |
| `RATE_LIMITS` | `on` by default. Set `off` for a demo where clicking fast is deliberate. | no |
| `DATABRICKS_PROFILE` | **Local development only.** The CLI profile used when not running inside Databricks Apps, where the service principal is injected instead. | local |

`app.yaml` shipping empty is enforced by a test, not a convention — it has caught
a real regression where a Lakebase host, a service-principal id, and `DEMO_MODE=on`
were committed.

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
python3 scripts/check.py                      # every CI gate
python3 -m unittest discover -s tests         # just the tests, <1s
```

Standard library only — no pytest, no containers, no database, no network. See
[`tests/README.md`](tests/README.md) for what each file covers and why the DB is
faked rather than provisioned.

`scripts/check.py` is the single definition of "does this repo pass": tests, lint,
a secret scan, a syntax check of the operator console, and a clean import of
`app.py` against the **real** dependencies (the suite stubs `asyncpg`/`openai`, so
that last gate is what catches "works in tests, crashes on boot"). CI only calls
this script, so the gates are identical on a laptop and in CI, and a missing tool
is reported as *skipped* rather than counted as a pass.

To work on the UI without a workspace:

```bash
python3 tests/serve_local.py --port 8000       # then open /console
```

---

## Documentation index

| Doc | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Step-by-step deploy, manual and scripted, including the GRANTs. |
| [`OPERATIONS.md`](OPERATIONS.md) | Running it for someone else: migrations, logs, diagnosing a report, rate limits, upgrades, common failures. |
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

### The SPA's nav is patched, not built

`frontend/dist` is committed and its React source is not in this repo, so two edits
to the SPA live in re-runnable scripts rather than in source:

| Script | What it changes |
|---|---|
| [`scripts/patch_spa_nav.py`](scripts/patch_spa_nav.py) | Product name and subtitle in the header. |
| [`scripts/patch_spa_grouped_nav.py`](scripts/patch_spa_grouped_nav.py) | Collapses the nine flat tabs into `Portfolio · Analyze ▾ · Plan ▾`, and adds links to the knowledge base and the proposal agent. |

**If you ever rebuild the SPA, re-run both.** A fresh build reverts them, which
silently hides the knowledge base and the proposal agent from the app's front door
without anything failing. `scripts/check.py` gates on this (`--check` reports status
without modifying anything), and the patch refuses to write a bundle that does not
parse — an unparseable bundle is a blank page for every user.

## License

See [`LICENSE.md`](LICENSE.md). Application code is provided as-is for evaluation
and internal planning. The bundled **reference library** is Databricks IP —
directional guidance; validate against your own data before any financial
decision. Your instance data is yours.
