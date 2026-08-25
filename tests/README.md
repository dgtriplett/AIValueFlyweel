# Tests

Run everything:

```bash
python3 -m unittest discover -s tests -v
```

Or run every gate CI runs — tests, lint, secret scan, and a clean import of
`app.py` against the real (unstubbed) dependencies:

```bash
python3 scripts/check.py
```

`scripts/check.py` is the single definition of "does this repo pass";
`.github/workflows/ci.yml` only calls it, so the gates are identical on a laptop
and in CI. It reports a missing tool as **skip**, never as a pass.

The suite deliberately uses only the **standard library** (`unittest`,
`unittest.mock`) plus what the app already depends on. No pytest, no test
containers, no live Lakebase — so it runs in CI, in a sandbox, and on a laptop
that has never talked to Databricks.

| File | Covers |
|---|---|
| `test_pu_domains.py` | The shipped domain vocabulary is internally consistent and every module mapping resolves against `pu_catalog.MODULES`. |
| `test_domain_derivation.py` | The seed derives `uc_requires_domain` from the module edges correctly — including strongest-necessity resolution — and covers the whole portfolio. |
| `test_readiness.py` | `classify()` truth table and the **dual-path** selection in `readiness_map()` (module vs. domain vs. `requires_locked`), driven by a fake DB. |
| `test_value_engine.py` | The parameterized value model: component arithmetic, low/high bands, realized value, and the shipped catalog (every assumption key it references is seeded, so nothing silently evaluates to $0). |
| `test_normalization.py` | Deterministic canonical matching (exact / alias / normalized) and that manual mappings are never overwritten. |
| `test_source_resolution.py` | The discovery→catalog join: 100% of a realistic corpus of customer schema names (`maximo`, `osisoft_pi`, `sap_isu`) resolves deterministically, and **zero** analytics-noise names (`dim_date`, `bronze`, `poc_project`) resolve. Both corpora are the test — a resolver that matches everything makes the coverage numbers fiction. |
| `test_sync_packages.py` | Cross-app roadmap import resolution: maturity-assessment free-text data requirements map to canonical Value Flywheel assets instead of creating generic imported dataset rows. |
| `test_generation.py` | Use-case candidate parsing: strict-schema validation, closed-vocab filtering, name-collision de-dupe, stable candidate ids. |
| `test_confirm.py` | Propose/confirm token lifecycle — single use, TTL expiry, payload integrity. |
| `test_taxonomy.py` | Taxonomy dimension vocabularies and effective-dating. |
| `test_migrator.py` | Migrations apply **at most once**, in order, and drift aborts before applying anything. Also that `startup_check` never executes DDL. |
| `test_logging.py` | JSON log shape, per-request correlation ids under concurrency, secret redaction, and an AST check banning `print()` in `server/`. |
| `test_ci.py` | The workflow references files that exist, tests the deployed Python version and the declared floor, and no gate has been silently dropped from `check.py`. |
| `test_limits.py` | Rate limits are per-actor and per-class, the limiter fails open, query budgets stay isolated across concurrent requests, and every expensive endpoint declares a limit. |
| `test_docs.py` | Docs make no claim the code contradicts: links resolve, documented flags exist, every setting the code reads is documented, and nothing still says migrations apply on startup. |
| `test_knowledge.py` | KB slugs (reserved-word and collision handling), folder paths (subtree matching, LIKE escaping, circular moves), **attachment validation against forged MIME types and path traversal**, the search SQL's parameter binding across all 32 filter combinations, and the two new migrations' DDL. |
| `test_whatif.py` | The what-if simulator and account scoping, which share `ready_assets()`: overrides win over stored state, the simulator reuses the real readiness logic instead of reimplementing it, it writes nothing, and concurrent requests never share an account. |
| `test_snapshots.py` | The trend: snapshots store computed FIGURES not foreign keys (a view would rewrite history when an assumption is recalibrated), they de-duplicate per minute, automatic triggers use `capture_quietly` so a charting failure never reports a successful change as failed, and the status WRITE path is account-scoped. |
| `test_account_scoping.py` | Regression guards for customer-owned tables: CRUD routes, derived surfaces, research/profile writes, and the hardening migration must carry `account_id` rather than producing globally visible rows. |
| `test_dependency_editing.py` | Hand-editing the dependency graph: cycles are refused BEFORE the insert (a two-use-case loop is the easy accident, and `compute_all_phases` survives it — so the graph goes quietly meaningless rather than loudly broken), the 422 names the chain without repeating a title, edits set `requires_locked` so a later automated pass cannot revert a human, and phase is recomputed on create AND delete. |
| `test_extractor_push.py` | The extractor's `--push` mode: it paces uploads to stay inside the server's `sweep` rate limit (three posted in a loop 429 on the third, every time), honours `Retry-After`, does not retry a 502 that names a permanent cause, keeps the manual CSV path intact for air-gapped estates, and reports failure with an exit code instead of raising after an hour-long sweep. |
| `test_value_plausibility.py` | What the portfolio claims IN AGGREGATE, which no per-formula test can see: every multiplier was individually defensible while 28 use cases together claimed 67% of the distribution capital plan and the total reached 28% of revenue. Caps the share of each denominator the whole catalog may claim, and the size of any single use case. |
| `test_genie_provision.py` | Genie space creation, where nothing fails loudly: the space is configured over the tables `mirror_to_uc()` actually builds (derived from its DDL, not a second copy of the list), the payload keeps the probed `serialized_space` shape, the instructions state the units and forbid joining two tables that both have an `id`, and an existing space is never overwritten. |
| `test_proposals.py` | The proposal agent: the prompt actually carries real instance values (company, computed value, domain gaps, prerequisite build state), and output that is missing, thin, or stubbed with TODO is rejected rather than stored. |
| `test_no_redundancy.py` | One definition per rule: no module restates `READY_STATUSES`, copies the cost table, or evaluates value models outside `value_engine`. |
| `test_app_wiring.py` | Every router is mounted, and no literal route is shadowed by a parameterized one declared before it. |
| `test_llm_negotiation.py` | Optional-parameter negotiation (`temperature` / `response_format` rejection) and that every model call goes through a negotiating helper. |
| `test_research.py` | Assumption parsing: invented keys rejected, unit misreads flagged, `"3.6M"` coerced, confidence downgraded. |
| `test_rules.py` | Classification rules — first-match-wins per dimension, regex support. |
| `test_enrichment.py` | The staged `ai_query()` enrichment pipeline. |
| `test_setup.py` | The `/api/setup/*` permission probes and the GRANT statements they emit. |
| `test_deploy.py` | `deploy.py`'s YAML rewriting, **and that `app.yaml` ships with no baked-in environment** (this has caught a real regression). |
| `test_extractor_download.py` | The downloadable cross-workspace schema extractor is served intact and runnable. |
| `test_path_traversal.py` | The SPA catch-all cannot be walked out of `frontend/dist`: **`GET /..%2f..%2fapp.py` returned this app's source pre-auth**. Covers the encoded spellings that survive router normalization (the plain `/../../` one never reproduced the bug), symlink escape, and that real assets and SPA deep links still serve. |
| `test_kb_glossary_scoping.py` | The knowledge base and glossary **do not span accounts**: reads spanned every row and writes omitted `account_id`, so any caller could search every utility's articles and body excerpts, edit or hard-delete one by (guessable) slug, walk `/api/kb/attachments/{id}` to download every uploaded document, and rewrite another tenant's vocabulary. Drives the real handlers and asserts every statement against a scoped table carries the predicate AND binds the resolved id — while `account_id IS NULL` shared reference rows stay visible. |
| `test_stub_fidelity.py` | The asyncpg stub exposes everything `server/db.py` resolves **at import time**, with the same inheritance as real asyncpg. `server/db.py` builds its `except` tuples at module scope, so one missing attribute is an `AttributeError` importing `server.db` that cascades into import errors suite-wide — it once collapsed the stdlib-only run from ~1090 collected tests to 709 with 56 `_FailedTest` errors blamed on unrelated modules. Invisible to anyone with asyncpg installed, since the stub is bypassed there. Derived from the source, so it fails on the NEXT attribute added rather than needing manual updates. |
| `test_serve_local_offline.py` | The local UI harness forcibly replaces importable real OpenAI, aiohttp, and asyncpg modules, clears ambient Databricks credentials, and returns deterministic model and Genie responses without network access. |
| `test_fail_closed_depth.py` | The fail-open paths that survived **one layer beneath** the first round of scoping fixes, found by cross-vendor review. A configured-but-unreachable Lakebase returned benign `[]`/`None`, so `default_account_id()` read it as "no accounts" and `scope_clause()` still produced `WHERE true` (and writes reported phantom success); `3F000` (bad `search_path`) was misclassified as pre-migration; the NULL-inclusive **read** predicate was reused for UPDATE/DELETE, letting one tenant mutate the shared library for everyone; the glossary duplicate check was scoped while its constraint is global; and link/label entity reads spanned accounts. |
| `test_account_authz.py` | Destructive account operations are **authorized and fail closed**: hard-delete, create, patch, `make_default` and archived-account enumeration were open to any caller. Gated on the proxy-injected `X-Forwarded-Email` via `GRID_ATLAS_ADMINS` (unset means nobody, not everyone), the client-settable `X-Grid-Atlas-User` grants nothing, refusals happen BEFORE the write, and `make_default` runs in one transaction so it cannot leave the table with no default. |
| `test_destructive_ops_authz.py` | Destructive operational endpoints are **admin-gated and fail closed**: Genie provisioning and demo load/reset reject non-admins and an empty `GRID_ATLAS_ADMINS`, while configured admins still reach the existing downstream behavior. |
| `test_account_fail_closed.py` | Account resolution **fails closed**: a DB error while reading `accounts` used to leave the request "unscoped", which made `scope_clause()` return `true` and served every tenant's rows. Scoped `/api/*` requests now 503, while the legitimate fresh-install / pre-migration path (no accounts yet) and the health, setup and SPA paths keep working. |
| `test_db_degraded.py` | A Lakebase **outage is not demo mode**: `PGHOST` unset means empty reads are correct, but `PGHOST` set with a failing pool means a live database is unreachable — so `/api/health` returns 503 instead of reporting "healthy" while every screen shows "no data yet" over a populated database. |
| `test_crash_guards.py` | Three fresh-install / bad-input **500s** from the production-readiness audit. The onboarding template built its dropdown range as `f"E2:E{max_row}"`, which on an empty database spells the reversed `E2:E1` — so the FIRST workbook a new customer downloads 500'd; asserts a parseable workbook, headers intact, and that the dropdown returns once rows exist (an over-correction that deleted it would also pass the empty case). `/api/impact/{node_id}` did `int(nid.split("-")[1])`, turning any malformed id into IndexError/ValueError instead of the **422** the typed `{id:int}` siblings already return. And nine `fetchrow(... RETURNING ...)` writes indexed a `None` result under a degraded pool, raising `TypeError` where the rest of the app raises the actionable 503 — pinned per-site, plus an AST sweep so a NEW unguarded write fails here rather than in the next audit. Cross-review found three gaps in the first round, each now pinned: `isdigit()` is **not** "int() accepts this" (`asset-²` passed the check and still 500'd; `uc-٣` would have been silently *reinterpreted* as use case 3 by a try/except, hence `isascii() and isdigit()`), and the sweep's hard-coded module list omitted `accounts.py` and `sync_packages.py` — which is exactly why their unguarded writes survived it. The sweep now **globs** `server/*.py` + `server/routes/*.py` and asserts its own corpus size, because a completeness check that needs a human to extend it silently narrows as the tree grows. |
| `test_joint_funding.py` | Joint funding delivery cost is a **user-entered field** rather than an auto-assumed estimate: the `FundIn` model accepts an optional `delivery_cost`, defaults to `None` when omitted, and allows explicit zero — so the auto-calculated `delivery_cost_mid` serves only as a prefilled suggestion the user can freely edit or clear. |
| `test_asset_detail.py` | Asset detail enrichment and per-account status overlay fixes: **PART A** verifies `get_use_case_detail` returns per-account ingestion_status (not the shared catalog column) via LEFT JOIN account_asset_status and COALESCE, and correctly partitions required vs helpful assets into separate lists. **PART B** verifies GET `/data-assets/{id}` enriches with `required_by` (reverse join to use cases + rationale + readiness per UC). |
| `test_reqby_tenant_isolation.py` | **Security:** Cross-tenant isolation for data-asset `required_by`. Verifies GET `/data-assets/{id}` does NOT leak custom use-case titles and rationale from other accounts. Custom use cases (origin='custom') are account-owned; a tenant must only see catalog use cases (shared) and their OWN custom use cases. Without the visibility predicate, Tenant A viewing a shared data asset would see Tenant B's private custom use-case titles + rationale — the same cross-tenant leak class as the onboarding-export bug. |
| `test_uc_progression.py` | Use-case progression tracking: (1) progression is account-scoped (account A's target dates/events do NOT appear for account B), (2) moving target date later records a slippage event with reason, (3) at_risk computes correctly (target in past + status not live/value_realized). |
| `test_hypothesized_override.py` | HYPOTHESIZED value override (mirror of realized_override_*): (1) PUT `/use-cases/{id}` persists `hypothesized_override_enabled`+`_amount`+`_note` — the values reach the UPDATE statement and round-trip in the returned row, (2) `get_use_case_detail` surfaces the three columns so the editor can initialize its Calculate/Override state. |
| `test_me_endpoint.py` | GET `/api/me` (Phase 1 persona linchpin): trusted identity (X-Forwarded-Email, lowercased, never spoofable headers), is_admin via GRID_ATLAS_ADMINS (empty allowlist → nobody, not everyone), is_exec_locked (hardcoded false in Phase 1; Phase 2+ reads from a table), unauthenticated requests return email:null + is_admin:false (no 500), and the endpoint is accessible without auth (reports who you are, including "nobody"). |
| `test_role_resolution.py` | Role resolution + persona inference (admin-portal Phase A): `resolve_role(email, request)` returns 'admin' for a GRID_ATLAS_ADMINS-allowlisted email even with no `app_users` row (bootstrap/lockout-proof), the stored `app_users.role` for a non-allowlisted email with a row, and 'pm' by default (unknown/unauthenticated). **Fail-closed:** any DB error or missing table falls back to non-admin 'pm', never admin. Also verifies GET `/api/me` returns `role` and computes `is_exec_locked` = (role=='executive' AND not is_admin). |

## Why a fake DB instead of a real one

`readiness.py` is pure SQL + Python aggregation. The interesting logic — which
path a use case scores on, how `bool_or` domain satisfaction folds into
`classify()` — lives in the Python half. `tests/fakedb.py` implements just enough
of the `db.fetch` interface to return canned rows per query, which lets the tests
assert on real branching behaviour rather than mocking out the function under
test. The SQL itself is exercised for real by `scripts/seed_clean.py` against
Lakebase during deploy, and by the `/api/setup/*` probes at runtime.
