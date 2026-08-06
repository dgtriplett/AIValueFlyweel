# Demo Mode (isolated, removable)

A **Demo Mode** control in the app header flips the LIVE app between two states
on demand, for presenting:

- **Load demo data** → populates the showcase portfolio (same dataset as
  `scripts/seed_demo.py`): ~35 use cases in the portfolio (incl. custom-authored
  ones), mixed statuses (scoping / in_progress / live / value_realized),
  shovel-ready **and** awaiting-prerequisites readiness, realized value, joint
  funding opportunities, a populated roadmap, and flywheel-flip candidates.
- **Reset to clean** → pristine day-1 (same as `scripts/seed_clean.py`): the full
  data-asset catalog and the full use-case portfolio are loaded, but everything is
  pristine (all not_started, nothing ingested or in-flight, no realized value).

## How it is gated (default OFF)

The entire feature is behind ONE env flag: **`DEMO_MODE`**.

- **Absent / `off` / `false` (the shipped bundle default in `databricks.yml`)**:
  every `/api/demo/*` endpoint returns **404** and the header toggle renders
  nothing. The feature is a complete no-op.
- **`on` (set in this deployed instance's `app.yaml`)**: the toggle renders and
  the endpoints are live.

It writes through the app's existing asyncpg pool using **DML only** (DELETE +
INSERT in one transaction — no DDL, no TRUNCATE, since the app service principal
holds DML but not table ownership). A failure rolls back, so the DB is never
left half-populated.

## Files that make up the feature

| File | Role |
|------|------|
| `server/routes/demo.py` | Backend: `GET /api/demo/status`, `POST /api/demo/load`, `POST /api/demo/reset`. Reuses `scripts/seed_lib.py` data + phase helpers. |
| `frontend/src/components/DemoModeToggle.tsx` | Header control; renders only when `/api/demo/status` reports enabled. |
| `app.yaml` (`DEMO_MODE: "on"`) | Turns the flag ON for this instance. |

Wiring is two one-line touches:
- `app.py` — the `# --- Demo Mode ... ---` include block.
- `frontend/src/App.tsx` — the `DemoModeToggle` import + the `<DemoModeToggle />`
  render in `Header`.

## Removal before Marketplace (~5 minutes)

1. Delete `server/routes/demo.py` and remove the `# --- Demo Mode ... ---`
   include block (3 lines) from `app.py`.
2. Delete `frontend/src/components/DemoModeToggle.tsx` and remove its `import`
   line + the `<DemoModeToggle />` line in `frontend/src/App.tsx`.
3. Remove the `DEMO_MODE` env entry from `app.yaml`.
4. Rebuild the frontend (`cd frontend && npm run build`) and redeploy.

That fully removes the feature. (Optional, cosmetic: `scripts/seed_lib.py` has a
guarded `try/import psycopg2` so the app runtime — which has no psycopg2 — can
import its pure helpers; it is harmless and can be reverted to a plain
`import psycopg2` if you prefer.)
