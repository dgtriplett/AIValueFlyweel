# Tests

Run everything:

```bash
python3 -m unittest discover -s tests -v
```

The suite deliberately uses only the **standard library** (`unittest`,
`unittest.mock`) plus what the app already depends on. No pytest, no test
containers, no live Lakebase — so it runs in CI, in a sandbox, and on a laptop
that has never talked to Databricks.

| File | Covers |
|---|---|
| `test_pu_domains.py` | The shipped domain vocabulary is internally consistent and every module mapping resolves against `pu_catalog.MODULES`. |
| `test_domain_derivation.py` | The seed derives `uc_requires_domain` from the module edges correctly — including strongest-necessity resolution — and covers the whole portfolio. |
| `test_readiness.py` | `classify()` truth table and the **dual-path** selection in `readiness_map()` (module vs. domain vs. `requires_locked`), driven by a fake DB. |
| `test_value_engine.py` | The parameterized value model: component arithmetic, assumption substitution, low/high bands, and malformed input. |
| `test_normalization.py` | Deterministic canonical matching (exact / alias / normalized) and that manual mappings are never overwritten. |
| `test_generation.py` | Use-case candidate parsing: strict-schema validation, closed-vocab filtering, name-collision de-dupe, stable candidate ids. |
| `test_confirm.py` | Propose/confirm token lifecycle — single use, TTL expiry, payload integrity. |
| `test_taxonomy.py` | Taxonomy dimension vocabularies and effective-dating. |

## Why a fake DB instead of a real one

`readiness.py` is pure SQL + Python aggregation. The interesting logic — which
path a use case scores on, how `bool_or` domain satisfaction folds into
`classify()` — lives in the Python half. `tests/fakedb.py` implements just enough
of the `db.fetch` interface to return canned rows per query, which lets the tests
assert on real branching behaviour rather than mocking out the function under
test. The SQL itself is exercised for real by `scripts/seed_clean.py` against
Lakebase during deploy, and by the `/api/setup/*` probes at runtime.
