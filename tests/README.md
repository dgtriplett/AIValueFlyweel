# Tests

Run everything:

```bash
python3 -m unittest discover -s tests -v
```

Or run every gate CI runs — tests, lint, secret scan, console-bundle syntax, and a
clean import of `app.py` against the real (unstubbed) dependencies:

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
| `test_generation.py` | Use-case candidate parsing: strict-schema validation, closed-vocab filtering, name-collision de-dupe, stable candidate ids. |
| `test_confirm.py` | Propose/confirm token lifecycle — single use, TTL expiry, payload integrity. |
| `test_taxonomy.py` | Taxonomy dimension vocabularies and effective-dating. |
| `test_migrator.py` | Migrations apply **at most once**, in order, and drift aborts before applying anything. Also that `startup_check` never executes DDL. |
| `test_logging.py` | JSON log shape, per-request correlation ids under concurrency, secret redaction, and an AST check banning `print()` in `server/`. |
| `test_ci.py` | The workflow references files that exist, tests the deployed Python version and the declared floor, and no gate has been silently dropped from `check.py`. |
| `test_limits.py` | Rate limits are per-actor and per-class, the limiter fails open, query budgets stay isolated across concurrent requests, and every expensive endpoint declares a limit. |
| `test_docs.py` | Docs make no claim the code contradicts: links resolve, documented flags exist, every setting the code reads is documented, and nothing still says migrations apply on startup. |
| `test_console_nav.py` | Every menu item routes somewhere real, no view is orphaned, menus carry the right ARIA roles, and the two CSS invariants that made the dropdowns invisible (nav clipping, header z-index) stay fixed. |
| `test_knowledge.py` | KB slugs (reserved-word and collision handling), folder paths (subtree matching, LIKE escaping, circular moves), **attachment validation against forged MIME types and path traversal**, the search SQL's parameter binding across all 32 filter combinations, and the two new migrations' DDL. |
| `test_whatif.py` | The what-if simulator and account scoping, which share `ready_assets()`: overrides win over stored state, the simulator reuses the real readiness logic instead of reimplementing it, it writes nothing, and concurrent requests never share an account. |
| `test_snapshots.py` | The trend: snapshots store computed FIGURES not foreign keys (a view would rewrite history when an assumption is recalibrated), they de-duplicate per minute, automatic triggers use `capture_quietly` so a charting failure never reports a successful change as failed, and the status WRITE path is account-scoped. |
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

## Why a fake DB instead of a real one

`readiness.py` is pure SQL + Python aggregation. The interesting logic — which
path a use case scores on, how `bool_or` domain satisfaction folds into
`classify()` — lives in the Python half. `tests/fakedb.py` implements just enough
of the `db.fetch` interface to return canned rows per query, which lets the tests
assert on real branching behaviour rather than mocking out the function under
test. The SQL itself is exercised for real by `scripts/seed_clean.py` against
Lakebase during deploy, and by the `/api/setup/*` probes at runtime.
