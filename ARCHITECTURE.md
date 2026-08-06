# Architecture — Value Flywheel

## Overview
A single Databricks App (FastAPI backend serving a React SPA) backed by Lakebase
Postgres, with optional live reads of Unity Catalog system tables and Foundation
Model API for AI agents.

```
┌────────────────────── Databricks App (value-flywheel) ──────────────────────┐
│  FastAPI (app.py)                                                            │
│   ├─ /api/*  REST (use_cases, data_assets, dependencies, values, roadmap,    │
│   │          comments, funding, impact, value_assumptions, agents,          │
│   │          analytics, live, onboarding, genie)                             │
│   └─ SPA static (frontend/dist)                                              │
│  Auth: dual-mode — service principal in-app, CLI profile locally            │
└──────────┬───────────────────┬───────────────────┬──────────────────────────┘
           │ asyncpg (OAuth)    │ Statement Exec API │ OpenAI-compatible
           ▼                    ▼ (warehouse)         ▼ (serving endpoints)
   ┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │ Lakebase PG   │   │ system.* tables  │   │ Foundation Model │
   │ (app state)   │   │ + Genie mirror   │   │ (AI agents)      │
   └───────────────┘   └──────────────────┘   └──────────────────┘
```

## Layers
- **Frontend** — React 18 + TS + Vite + Tailwind (Blueprint dark tokens), TanStack
  Query, React Flow (graph/blast radius), Recharts (dashboards). Heavy views
  (`FlywheelTab`, `DashboardsView`, `RoadmapView`, `AssumptionsView`,
  `JointFundingView`) are lazy-loaded to keep the initial bundle small.
- **Backend** — FastAPI + `asyncpg` pool with OAuth-token password + ~45-min
  refresh; dual-mode auth (`server/config.py`); all SQL is **parameterized**
  ($-placeholders), no string interpolation of user input.
- **State (Lakebase)** — schema in `server/migrations/001_init.sql`: lobs,
  data_assets (+ provenance origin/auto flags), use_cases (phase + status + both
  value models), edges (uc_requires_asset, uc_enables_uc), value_records,
  value_assumptions, roadmap_items, linked_databricks_assets, comments,
  funding_requests, benchmark_library, audit_log.
- **Value engine** (`server/value_engine.py`) — safe structured evaluator:
  component value = multiplier × Π(assumption keys); low/high bands; realized via
  parameterized actuals OR manual override. Editing any global assumption
  re-quantifies the whole portfolio live.
- **Readiness** (`server/readiness.py`) — % of required assets that are curated/
  governed → shovel-ready / nearly / blocked.
- **AI agents** (`server/routes/agents.py`) — Foundation Model API with heuristic
  fallback: dependency-detect, next-best-UC, roadmap generator (topological waves),
  value estimator, source decomposition. Human-in-the-loop where they write.
- **Live integration** (`server/lineage.py`, `server/live.py`) — Statement
  Execution API over the bound warehouse; reads `system.access.table_lineage`,
  `system.lakeflow.*`, `system.serving.*`, `system.billing.usage`; data-relative
  windows; degrades gracefully if system tables are unavailable.
- **Genie** (`server/routes/genie.py`) — proxies the Genie Conversations API over
  a UC mirror of the portfolio; graceful placeholder until `GENIE_SPACE_ID` is set.

## Data flow: the flywheel
1. Register/curate data sources (catalog) → mark ingestion status (manual, Excel,
   or auto from lineage).
2. Readiness recomputes → use cases become shovel-ready.
3. Deliver a use case (status→live) → its required data auto-captured as landed →
   dependents' readiness jumps → "unlocks" surfaced.
4. Blast radius + recommender narrate the fast-follows; roadmap sequences waves.
5. Value engine quantifies hypothesized → realized as assumptions and status evolve.

## Security / production posture
- Least privilege: app SP gets a scoped Lakebase role + CAN_USE on one warehouse;
  UC mirror needs an explicit customer grant (documented, not auto-elevated).
- No secrets in the repo; `.gitignore` excludes node_modules/.venv/.git.
- Read-only + consent-gated live integration; every mutation writes an audit_log row.
- Graceful degradation everywhere (demo mode if Lakebase absent; friendly errors if
  system tables / Genie / LLM unavailable).
