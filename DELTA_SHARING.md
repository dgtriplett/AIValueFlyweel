# Reference-library distribution via Delta Sharing (IP model)

The **reference library** (curated 198-UC P&U catalog, module-level data-source
catalog, value-model templates, `benchmark_library`) is Databricks IP. Rather than
copying it into each customer instance, the intended distribution is a **grantable,
revocable, tracked Delta Share** — a live product Databricks maintains and versions.

## Model
```
Databricks (provider)                         Customer (recipient)
┌──────────────────────────┐                  ┌──────────────────────────┐
│ catalog: db_ip_reference  │  Delta Share →   │ shared catalog (read-only)│
│  ├─ ref_use_cases         │  (grant/revoke)  │  ├─ ref_use_cases         │
│  ├─ ref_data_sources      │                  │  ├─ ref_data_sources      │
│  ├─ ref_value_models      │                  │  ├─ ref_value_models      │
│  └─ ref_benchmarks        │                  │  └─ ref_benchmarks        │
└──────────────────────────┘                  └──────────────────────────┘
        │  provider controls versioning + access; usage tracked
        ▼
 Customer instance imports the reference set ONCE into their Lakebase on first run
 (origin='catalog'); their own additions are origin='custom'; live detection marks
 origin='auto'. Provenance is already tracked in `data_assets.origin` +
 `use_cases`/edges — so customer edits never overwrite the licensed reference set,
 and a re-share/refresh can update reference rows while leaving customer rows intact.
```

## Why sharing (not copy)
- **Grantable / revocable**: access is a share grant, not a file; can be revoked.
- **Tracked**: Delta Sharing records recipient access (audit / entitlement).
- **Versioned**: Databricks updates the catalog centrally; recipients pull updates.
- **No redistribution**: recipients read the share; they don't get a copyable dump.

## v1 status (this build)
- v1 **ships the reference library seeded locally** (in the app's Lakebase) so the
  app runs standalone with no external dependency — see `scripts/seed_clean.py`.
- Provenance columns (`origin` = catalog | auto | custom) already separate licensed
  reference content from customer content, so the sharing model drops in cleanly.
- **Stub / next step**: create the provider share `db_ip_reference`, publish the four
  `ref_*` tables, and switch `seed_clean.py`'s reference load to read from the shared
  catalog (a one-line source swap) once the share exists. Grant management and usage
  tracking are handled by Delta Sharing itself.

## Provider-side sketch (not run here)
```sql
CREATE SHARE db_pu_reference_library;
ALTER SHARE db_pu_reference_library ADD TABLE db_ip_reference.ref_use_cases;
ALTER SHARE db_pu_reference_library ADD TABLE db_ip_reference.ref_data_sources;
ALTER SHARE db_pu_reference_library ADD TABLE db_ip_reference.ref_value_models;
ALTER SHARE db_pu_reference_library ADD TABLE db_ip_reference.ref_benchmarks;
CREATE RECIPIENT "<customer>";
GRANT SELECT ON SHARE db_pu_reference_library TO RECIPIENT "<customer>";
-- revoke: REVOKE SELECT ON SHARE ... ; usage visible in system.access + sharing audit
```
