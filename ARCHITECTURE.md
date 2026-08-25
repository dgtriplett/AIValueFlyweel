# Architecture — AI Value Flywheel

## Overview

A single Databricks App: FastAPI serving a pre-built React SPA, backed by
**Lakebase** (portfolio state) and **Unity Catalog** (discovery state), with the
**Foundation Model API** behind the agents.

```
┌──────────────────── Databricks App (grid-atlas) ────────────────────┐
│  FastAPI (app.py)                                                    │
│   ├─ /api/*   REST                                                   │
│   └─ /        static SPA (frontend/dist)                             │
│  Auth: dual-mode — service principal in-app, CLI profile locally     │
└──────┬─────────────────┬──────────────────────┬─────────────────────┘
       │ asyncpg (OAuth)  │ Statement Exec API   │ chat / ai_query
       ▼                  ▼ (bound warehouse)    ▼ (serving endpoint)
 ┌─────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ Lakebase PG │   │ Unity Catalog    │   │ Foundation Model │
 │ portfolio   │   │ discovery +      │   │ agents           │
 │ state       │   │ system tables    │   │                  │
 └─────────────┘   └──────────────────┘   └──────────────────┘
```

## Why state is split across two stores

**Lakebase (Postgres)** holds the portfolio: use cases, data assets, domains,
edges, values, roadmap, taxonomy, aliases, tokens. It is transactional,
relational, and small — and the value engine's live recompute needs real joins
and sub-millisecond reads.

**Unity Catalog (Delta)** holds discovered inventory: schemas, tables, enrichment
staging. Three reasons it does not live in Lakebase:

1. **`ai_query()` only runs in SQL.** Enriching tens of thousands of tables means
   batch inference inside the warehouse, not row-by-row calls from Python.
2. **Scale.** A large utility estate is 50k+ tables; that is a Delta-shaped
   workload, not a Postgres-shaped one.
3. **Governance.** Customers can query and govern their own metadata inventory
   with their own tools instead of it being locked inside an app database.

Only the *conclusions* — asset ingestion status, discovered table counts — are
written back to Lakebase, which stays the portfolio's system of record.

## Layers

- **Frontend** — React 18 + TS + Vite + Tailwind (Blueprint dark tokens),
  TanStack Query, React Flow (graph/blast radius), Recharts. Heavy views are
  lazy-loaded. See the README's note on the frontend source.
- **Backend** — FastAPI + `asyncpg` pool with an OAuth-token password and ~45-min
  refresh; dual-mode auth (`server/config.py`). Lakebase SQL is parameterized
  (`$n` placeholders, no interpolation of user input).
- **Portfolio state** — `server/migrations/001_init.sql` and `002_domains.sql`:
  lobs, data_assets, use_cases, data_domains, asset_serves_domain,
  uc_requires_domain, uc_requires_asset, uc_enables_uc, value_records,
  value_assumptions, roadmap_items, comments, funding_requests, audit_log.
- **Discovery state** — `003_discovery.sql`: discovered_schemas,
  discovered_tables, data_asset_aliases, asset_taxonomy, ingestion_runs.
- **Agent state** — `004_agents.sql`: confirm_tokens, uc_generation_previews,
  chat_conversations, chat_messages. `005_research.sql`: research_runs,
  company_profile, assumption_proposals. `006_branding.sql`: branding.
- **Knowledge base** — `007_knowledge.sql`: kb_folders (materialized path),
  kb_articles (markdown + a GENERATED tsvector for full-text search),
  kb_article_versions (append-only history), kb_links (polymorphic attachment to
  portfolio entities), kb_attachments (binary documents, in a UC Volume when one is
  configured and Lakebase otherwise).
- **Constraints** — `008_use_case_title_unique.sql`: a unique index on
  `lower(title)`. The generation route guarded duplicates with a check-then-insert
  that nothing in the database backed up, so two concurrent commits could both pass
  the check and create the same use case twice — double-counting its value in every
  rollup. The migration merges any pre-existing duplicates (re-pointing their child
  rows and writing an audit trail) before adding the index, so it cannot abort on
  real customer data.
- **Accounts (multi-tenancy)** — `009_accounts.sql`: accounts,
  account_asset_status, and the `asset_status_by_account` view. The
  customer-specific tables (company_profile, branding, value_assumptions, research,
  kb_*) gained an `account_id`; the reference library (use_cases, data_assets,
  data_domains, the edges) stays shared, because it is the product's IP and copying
  it per account would mean applying every catalog fix N times.
  `data_assets.ingestion_status` was the hard case — per-customer state on a shared
  table — so it moved to `account_asset_status` with the column kept as the
  default-account fallback, resolved through the view so one COALESCE serves every
  query instead of eight copies. A NULL `account_id` means "visible to all", which
  is how a seeded folder or a global glossary term serves every tenant without
  duplication.
- **Tenancy fix** — `010_fix_account_status_leak.sql`. 009's view read
  `COALESCE(s.ingestion_status, da.ingestion_status, ...)`, intending the shared column
  as a fallback for the DEFAULT account so the upgrade would be invisible. But the
  COALESCE applied to EVERY account, and a newly created account has no rows — so it
  inherited the first customer's landed sources. Found by creating a second account on
  the live instance: it reported the same 20 governed sources with zero differing.
  Every downstream number would have been computed from another tenant's data and
  looked plausible. 010 backfills explicit rows for every account and drops the status
  fallback; the DESCRIPTIVE overrides (display name, cost, owning LOB) still inherit
  the catalog, because those describe a shared module rather than stating a customer's
  position.
- **Snapshots** — `011_snapshots.sql`: value_snapshots, one denormalized row per
  account per event. Deliberately stores computed FIGURES rather than foreign keys: a
  snapshot must stay readable after a use case is renamed or an assumption recalibrated,
  and a view over current state would retroactively rewrite every historical point,
  making the trend a straight line by construction. Captured on the events that move
  the number (research applied, a source landed, a live sync that changed something)
  plus a manual button, so every point corresponds to a real event and `reason` says
  which. De-duplicated to one row per account/minute/reason via a GENERATED UTC minute
  column — a functional index on `date_trunc` is rejected because that is only STABLE
  on a timestamptz, not IMMUTABLE.
- **Tenancy hardening** — `012_harden_account_scoping.sql`: customer-owned tables
  that must never be global (`value_records`, `roadmap_items`, `funding_requests`,
  `comments`, `research_runs`, `assumption_research`, `chat_conversations`) adopt
  any accidental NULL `account_id` rows into the default account and then mark the
  column NOT NULL. It also repairs accounts created without their own baseline
  value assumptions.
- **External sync** — `013_external_sync.sql`: `external_object_map` records
  source-app ids for imported assessment/roadmap objects. This makes recurring
  imports from the Data & AI Maturity Assessment app idempotent: a use case created
  from an exported roadmap is updated on the next run instead of duplicated and
  double-counted.
- **Account portfolio membership** — `014_account_portfolio.sql`:
  `account_portfolio_use_cases` separates the shared use-case catalog from each
  account's selected portfolio. The old `use_cases.in_portfolio` flag was global,
  so a new account inherited the default/demo portfolio. 014 migrates the current
  global selection into the default account only, preserves non-default accounts'
  real work via roadmap/value/external-sync rows, and lets newly-created accounts
  start with a clean portfolio ready for roadmap import.
- **Joint funding delivery cost** — `015_joint_funding_delivery_cost.sql`: adds
  `funding_requests.delivery_cost` for user-entered labor/people cost, replacing
  the auto-assumed estimate with an editable field. The auto-calculated
  `delivery_cost_mid` now serves only as a prefilled suggestion.
- **Asset detail enrichment** — `016_asset_detail.sql`: adds four descriptive
  fields to `data_assets` (provides, refresh_cadence, steward, source_of_record) and
  `rationale` to `uc_requires_asset`, making the module edge symmetric with the
  domain edge (`uc_requires_domain.rationale` existed but `uc_requires_asset.rationale`
  did not). Supports the clickable data-asset detail drawer (PART B) and fixes the
  per-account status overlay bug in `get_use_case_detail` (PART A).
- **Use-case progression tracking** — `017_uc_progression.sql`: adds per-account
  target go-live dates (`account_use_case_progress`) and an append-only event log
  (`use_case_status_events`) for status changes, date changes (incl. slippage with
  reason), and free notes. Enables centralized tracking of "when we may start to
  realize value" and "what's slipping and why" — the user-approved feature request.
  Fully account-scoped (no cross-tenant leak). Routes: `GET/PUT /use-cases/{id}/progression/target-date`,
  `POST /use-cases/{id}/progression/note`. Frontend: Progression section in UseCaseDrawer.
- **Asset detail content population** — `018_asset_detail_content.sql`: populates
  the four descriptive columns added in migration 016 (provides, steward, source_of_record,
  refresh_cadence) for all 146 catalog data assets, deriving confident values from existing
  metadata (source_system, module, owning_lob, source_category). Idempotent and
  non-destructive — only updates NULL/empty values so user edits are preserved. Uses
  natural-key matching (source_system, module) to work across environments. Complements
  the inline-edit UI added to DataAssetDrawer (pencil icon → editable fields → Save/Cancel).
- **Asset detail content FIX** — `019_asset_detail_content_fix.sql`: corrects migration
  018, which used the WRONG natural key (source_system, module) instead of the CORRECT
  canonical key (source_category, module) documented in `scripts/seed_lib.py`. As a result,
  only 41 of 146 assets were populated; 105 remained NULL. This migration fills those 105
  assets using the correct key, matching the tone and confidence-derivation approach of 018.
  Also populates uc_requires_asset.rationale for 734 edges with concise, category-derived
  rationale (e.g., "Provides X data from Y category required for this use case"). Idempotent
  and non-destructive — only updates NULL/empty values. Creates helper function
  `update_asset_detail_by_category` keyed on (source_category, module).
- **Hypothesized value override** — `020_hypothesized_override.sql`: adds three
  `hypothesized_override_*` columns (enabled BOOLEAN NOT NULL DEFAULT false, amount
  NUMERIC, note TEXT) to `use_cases`, mirroring the existing `realized_override_*`
  columns from 001_init.sql. This lets the use-case detail (drawer AND full page)
  edit HYPOTHESIZED value two ways — CALCULATE (per-component multipliers, live
  total) and OVERRIDE (a straight dollar value + a required "why I am overriding"
  note) — exactly as realized value already can. Idempotent and additive
  (ADD COLUMN IF NOT EXISTS); columns only, no SP grant needed on deploy.
- **Migration ledger** — `schema_migrations`, created by `server/migrator.py`
  rather than by a numbered migration, since it must exist before the ledger can
  be consulted.
- **Value engine** (`value_engine.py`) — safe structured evaluator: component
  value = multiplier × Π(assumption keys); low/high bands; realized via
  parameterized actuals or manual override. Editing one global assumption
  re-quantifies the whole portfolio live.
- **Readiness** (`readiness.py`) — the dual path; see below.
- **Normalization** (`normalization.py`) — 5-stage canonicalization cascade.
- **Enrichment** (`enrichment.py`) — staged `ai_query()` SQL builders.
- **Generation** (`generation.py`) — prompts + candidate validation.
- **Research** (`research.py`) — company research plus assumption calibration. The
  `ASSUMPTION_GUIDE` gives every one of the 34 assumptions a unit, a meaning, and a
  derivation, because several read as something other than what they are —
  `saidiMinuteValueMM` looks like a duration but is the $M value of avoiding one
  minute, and a model left to infer that is wrong by orders of magnitude.
- **Chat tools** (`chat_tools.py`) — 12 typed tools. Read tools execute; write
  tools return a `_propose` descriptor and never write.
- **Rules** (`rules.py`) — classification rules, first-match-wins per dimension.
- **Knowledge** (`knowledge.py`) — slugs (reserved-word aware, so an article cannot
  shadow a route), materialized folder paths, attachment validation against the
  actual bytes rather than the declared type, and the search-SQL builder.
- **Proposals** (`proposals.py`) — the eight-section prompt built from real instance
  state, plus output validation. A section that is missing, thin, or stubbed with
  TODO is rejected rather than stored: a half-written document in the knowledge base
  is indistinguishable from a finished one once the generation context is gone. There
  is no heuristic fallback, because a template-filled proposal would read as authored
  and say nothing.
- **Taxonomy** (`taxonomy.py`) — 3 dimensions, effective-dated.
- **Confirm** (`confirm.py`) — single-use tokens gating agent writes.
- **Live integration** (`lineage.py`, `live.py`) — Statement Execution API over
  the bound warehouse; reads `system.access.table_lineage`, `system.lakeflow.*`,
  `system.serving.*`, `system.billing.usage`; degrades gracefully.
- **Migrator** (`migrator.py`) — checksum ledger, apply-once, drift detection. The
  app calls only the read-only `startup_check`; applying belongs to
  `scripts/migrate.py`, run as the database owner.
- **Logging** (`logging_setup.py`) — JSON in Databricks Apps, text locally; a
  request id in a `ContextVar` so concurrent requests never share one; redaction
  in the formatter.
- **Limits** (`limits.py`) — per-actor token buckets and the per-request query
  budget, charged at the `db.fetch`/`db.execute` choke point so it counts real
  queries rather than estimating them.

## The three decisions worth understanding

### 1. Readiness runs a dual path

Two requirement models coexist:

- **Module path** — `use_case --requires--> data_asset`. Precise: satisfied only
  when that exact module is curated/governed.
- **Domain path** — `use_case --requires--> data_domain <--serves-- data_asset`.
  Robust: satisfied when **any** mapped asset is curated/governed.

The domain path exists because the module path produces false gaps. A use case
needing work-order history and bound to *SAP Plant Maintenance* reads as blocked
at a utility running *Maximo* — which has the data. Domains decouple the need from
the product.

But domains trade precision for robustness: `process_historian_timeseries` is
served by turbine, boiler, and feedwater modules alike, so a use case that
genuinely needs *turbine* tags would be satisfied too easily. Hence per-use-case
precedence:

```
requires_locked = true  →  MODULE path   (a human curated it; precision wins)
has required domains    →  DOMAIN path   (the vendor-substitution fix)
otherwise               →  MODULE path   (pre-domain behaviour, unchanged)
```

`requirement_model` is returned with every readiness result so the UI can explain
*why* something is blocked. A fresh install with an unpopulated domain layer
behaves exactly like the pre-domain app.

### 2. AI enrichment is staged, for cost

The obvious implementation is one `MERGE` whose source CTE calls `ai_query()` and
parses several fields out of the JSON response. That invokes the model **once per
output field** — roughly 4x the necessary cost — because each reference to a
`from_json` result lets the optimizer push the projection, and the `ai_query`
beneath it, down separately.

So:

- **Stage 1** evaluates `ai_query()` exactly once per candidate row and persists
  the **raw** response to a Delta staging table.
- **Stage 2** MERGEs from that table, parsing stored text. Zero LLM calls.

Beyond cost this buys visible progress (`SELECT count(*)` on staging climbs),
resumability (a failed MERGE doesn't re-pay for inference), and a free re-parse if
the JSON shape needs adjusting. `tests/test_enrichment.py` asserts the invariant
directly: exactly one `ai_query(` in stage 1, zero in stage 2.

### 3. Agent writes go through a confirm gate

Agent-proposed changes never write directly:

1. A propose endpoint validates inputs, reads current state, and issues a
   single-use token with the payload stored **server-side**.
2. The UI renders a before/after card.
3. `POST /api/confirm/{token}` atomically claims the token and dispatches to the
   registered executor.

The payload is stored server-side rather than re-sent by the client because
otherwise the only thing binding "what the user saw" to "what gets written" is
client state — and anything that can mutate the page could substitute different
values behind an approval already given. This way the client's authority is one
bit.

The claim is a single `UPDATE ... WHERE consumed_at IS NULL ... RETURNING`, so a
replayed confirm (double click, retry, back button) is a no-op rather than a
duplicate write. Tokens live in Lakebase, not memory, because Apps can run
multiple workers and restart between propose and confirm.

## Data flow: the flywheel

1. **Discover** — sweep workspace metadata, enrich it, canonicalize source
   systems, attribute tables to catalog modules.
2. **Register/curate** sources → mark ingestion status (manual, Excel, or
   discovered).
3. **Readiness recomputes** → use cases become shovel-ready as their required
   domains are satisfied.
4. **Deliver a use case** (status → live) → dependents' readiness jumps →
   "unlocks" surface.
5. **Blast radius + recommender** narrate the fast-follows; the roadmap sequences
   waves topologically.
6. **Value engine** quantifies hypothesized → realized as assumptions and status
   evolve.
7. **Generation** proposes new use cases against real availability — `ready` for
   what is buildable now, `gap` to justify new ingestion.

## Security posture

- **Least privilege.** The app SP holds a scoped Lakebase role (DML, not
  ownership → no DDL), `CAN_USE` on one warehouse, `CAN_QUERY` on one endpoint,
  and explicit grants on only the two UC schemas it writes. Schema DDL belongs to
  the install/seed step, run as the DB owner.
- **Metadata only.** Discovery reads names, comments, owners, and data types. No
  table contents are ever queried; the extractor states this in its own README
  because it runs under a user's own credentials.
- **SQL injection posture.** Lakebase queries use real `$n` bind parameters. The
  Statement Execution API has no bind parameters, so values there route through
  one escaping choke point (`enrichment.sql_str`) and identifiers are
  backtick-quoted with a validator that rejects embedded backticks. Adversarial
  inputs are covered in `tests/test_enrichment.py`.
- **Auditability.** Every mutation writes an `audit_log` row; every pipeline stage
  writes an `ingestion_runs` row; taxonomy changes are effective-dated rather than
  overwritten; alias corrections record who made them.
- **Human precedence.** `is_user_edited` / `mapped_by='manual'` /
  `requires_locked` / `domains_locked` are never overwritten by a re-run. A human
  decision outranks the model's, permanently.
- **No secrets in the repo.** `app.yaml` ships with empty values; `.gitignore`
  excludes the deploy cache, node_modules, and venvs. Both are enforced by tests
  (`tests/test_deploy.py`, `scripts/check_no_secrets.py`) rather than by
  convention — the `app.yaml` guard has caught a real regression.
- **No secrets in the logs.** Redaction runs in the log formatter, so a token
  inside an exception string nobody thought to sanitize is still caught. Uploaded
  logos are served under a restrictive CSP with `nosniff`, since an SVG can carry
  script.
- **Schema DDL is not reachable from the app.** Migrations apply at most once, run
  as the database owner. A restart cannot replay one, which is the failure mode
  where a non-idempotent backfill destroys data unattended.
- **Bounded cost per caller.** Per-actor rate limits on the endpoints that spend
  money or warehouse time, and a per-request query budget so one chat turn cannot
  monopolize the connection pool. Guard rails against accidental load, explicitly
  not a security control — see [`OPERATIONS.md`](OPERATIONS.md).
- **Graceful degradation.** No Lakebase → demo mode. No `ATLAS_CATALOG` →
  discovery disabled. No warehouse → dependent probes report *skipped*, not
  failed. No serving endpoint → heuristic agents. No Genie space → placeholder.

## Failure modes and what happens

| Failure | Behaviour |
|---|---|
| Lakebase unreachable | Demo mode; setup reports it as the root cause. |
| Warehouse stopped/denied | Discovery, taxonomy, and Genie report *skipped*; portfolio unaffected. |
| Serving endpoint denied | Agents fall back to heuristics and say so in the response. |
| `ai_query` partial failure | `failOnError => false`; the run reports `partial` with an error count. |
| A workspace unreachable during extract | Skipped and reported; other workspaces still produce output; exit code 2. |
| Model returns bad JSON | Strict schema, then a fence-tolerant parse, then heuristic fallback. |
| Model hallucinates a domain | Dropped, with a warning. Never created. |
| Duplicate confirm | Rejected as "already applied". |
| Re-uploading an inventory | MERGE on the natural key: updates, never duplicates. |
| Unapplied migration | Logged with the exact command to run; the app still serves every table that exists. |
| Applied migration file edited | Reported as drift and refused — see [`OPERATIONS.md`](OPERATIONS.md#drift). |
| A migration fails midway | That file is rolled back and not recorded; later files do not run, since they assume it landed. Re-runnable once fixed. |
| Too many expensive requests | 429 with `Retry-After` and a message saying it is a guard rail, not a quota. |
| A chat turn queries unboundedly | 429 at the query budget, with the count. The user's message is already saved, so the conversation stays coherent. |
| An unknown rate-limit name | Fails **open**. An outage caused by the thing preventing outages is a worse trade. |
