# License & Terms — Value Flywheel

**Powered by Databricks.** This is a Databricks Field Engineering reference application.

## Application code
The application code (frontend, backend, scripts, bundle) is provided by Databricks
for evaluation and internal planning use, **as is, without warranty of any kind**.

## Reference library (Databricks IP)
The bundled **reference library** — the curated 198 Power & Utilities use-case
catalog, the module-level data-source catalog, the value-model templates, and the
`benchmark_library` value ranges — is the **intellectual property of Databricks**,
provided under license for use within this application. It is **directional guidance**:
value estimates must be validated against your own data and assumptions before use in
any financial or investment decision. Do not redistribute the reference content
outside this application.

The intended distribution model for the reference library is a **grantable,
revocable, tracked Delta Share** (see `DELTA_SHARING.md`) rather than a copy — so
Databricks can maintain and version it and customers consume a live, licensed share.

## Your instance data
The data **you** enter or import (your use cases, statuses, values, edges, comments,
funding requests) is **yours**, stored in **your** workspace's Lakebase database. This
application does not exfiltrate your data.

## Live integration
The optional "Sync from Databricks" / "Auto-populate" features run **read-only**
queries against your Unity Catalog system tables and SQL warehouse, and only with
your explicit per-use consent. No writes are made to your system tables.

## Attribution
The "Powered by Databricks" attribution must remain visible in the application UI.
