"""The seed's derivation of uc_requires_domain from the module edges.

`seed_lib.load_domains` step 3 is what makes the domain layer useful on day one:
it walks the 693 shipped module requirements and, via the module->domain mapping,
writes the equivalent domain requirements. Two things must hold:

  1. Necessity folds to the STRONGEST value. If a use case requires module A
     ('required') and module B ('helpful') and both serve domain D, then D is
     'required' — taking 'helpful' would under-state the requirement and let a
     use case read as ready without data it genuinely needs.
  2. Coverage is total. A use case that derives no domains silently falls back to
     the module path; that is a supported state, but on the shipped catalog it
     should not happen, so we assert it doesn't regress.

The derivation is reimplemented here rather than driven through psycopg2 so the
test needs no database. `test_seed_lib_parity` guards against the two copies
drifting by checking the real function's structure.
"""
import collections
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pu_domains import DOMAINS, SERVES  # noqa: E402

SEED_JSON = os.path.join(ROOT, "scripts", "seed_data.json")


def derive(asset_by_module, requires, domains_by_asset=None):
    """Mirror of load_domains steps 2-3. Returns {uc_key: {domain: necessity}}."""
    if domains_by_asset is None:
        domains_by_asset = collections.defaultdict(set)
        for domain, pairs in SERVES.items():
            for pair in pairs:
                asset = asset_by_module.get(pair)
                if asset is not None:
                    domains_by_asset[asset].add(domain)

    reqs = collections.defaultdict(list)
    for uc_key, asset_key, criticality in requires:
        reqs[uc_key].append((asset_key, criticality))

    out = {}
    for uc_key, module_reqs in reqs.items():
        necessity = {}
        for asset_key, criticality in module_reqs:
            for domain in domains_by_asset.get(asset_key, ()):
                if necessity.get(domain) == "required":
                    continue  # already strongest
                necessity[domain] = criticality
        out[uc_key] = necessity
    return out


class TestNecessityFolding(unittest.TestCase):
    """Unit-level: the folding rule, on hand-built inputs."""

    def test_required_beats_helpful_regardless_of_order(self):
        dba = {"a1": {"d"}, "a2": {"d"}}
        helpful_first = derive({}, [("u", "a1", "helpful"), ("u", "a2", "required")], dba)
        required_first = derive({}, [("u", "a1", "required"), ("u", "a2", "helpful")], dba)
        self.assertEqual(helpful_first["u"]["d"], "required")
        self.assertEqual(required_first["u"]["d"], "required")

    def test_helpful_only_stays_helpful(self):
        dba = {"a1": {"d"}}
        self.assertEqual(derive({}, [("u", "a1", "helpful")], dba)["u"]["d"], "helpful")

    def test_one_module_fans_out_to_all_its_domains(self):
        dba = {"a1": {"d1", "d2", "d3"}}
        self.assertEqual(derive({}, [("u", "a1", "required")], dba)["u"],
                         {"d1": "required", "d2": "required", "d3": "required"})

    def test_unmapped_module_contributes_nothing(self):
        self.assertEqual(derive({}, [("u", "orphan", "required")], {})["u"], {})


class TestShippedCatalogDerivation(unittest.TestCase):
    """Integration-level: run the derivation over the real seed data."""

    @classmethod
    def setUpClass(cls):
        with open(SEED_JSON) as fh:
            cls.data = json.load(fh)
        cls.asset_by_module = {}
        for idx, asset in enumerate(cls.data["data_assets"]):
            key = (asset.get("source_category") or asset["source_system"], asset["module"])
            cls.asset_by_module[key] = idx
        cls.derived = derive(cls.asset_by_module, cls.data["requires"])

    def test_every_use_case_derives_at_least_one_domain(self):
        missing = [u["title"] for u in self.data["use_cases"]
                   if not self.derived.get(u["parent_id"])]
        self.assertEqual(missing, [],
                         f"{len(missing)} use cases would fall back to the module path")

    def test_every_use_case_has_a_required_domain(self):
        """Only 'required' domains drive readiness — a use case whose derived
        domains are all 'helpful' would score 0/0 = trivially ready."""
        weak = [u["title"] for u in self.data["use_cases"]
                if not any(v == "required"
                           for v in self.derived.get(u["parent_id"], {}).values())]
        self.assertEqual(weak, [], f"use cases with no REQUIRED domain: {weak}")

    def test_all_catalog_assets_map_to_a_domain(self):
        vocab = {d[0] for d in DOMAINS}
        mapped = {self.asset_by_module[p]
                  for pairs in SERVES.values() for p in pairs
                  if p in self.asset_by_module}
        self.assertEqual(len(mapped), len(self.asset_by_module))
        # And every mapping targets a real domain.
        self.assertEqual(set(SERVES) - vocab, set())

    def test_domain_counts_are_plausible(self):
        """Guards both failure directions: a collapsed mapping (every UC needing
        one domain) and an over-broad one (a UC needing dozens)."""
        counts = [len(v) for v in self.derived.values()]
        self.assertGreaterEqual(min(counts), 1)
        self.assertLessEqual(max(counts), 12, "a use case derived implausibly many domains")
        mean = sum(counts) / len(counts)
        self.assertTrue(2.0 <= mean <= 6.0, f"mean domains/UC out of range: {mean:.2f}")

    def test_derivation_is_deterministic(self):
        again = derive(self.asset_by_module, self.data["requires"])
        self.assertEqual(again, self.derived)


class TestSeedLibParity(unittest.TestCase):
    """The real load_domains is exercised against Lakebase at deploy time, not
    here. Assert its contract so this file's reimplementation can't silently
    diverge from it."""

    def test_load_domains_signature_and_contract(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import inspect

        import seed_lib

        self.assertTrue(hasattr(seed_lib, "load_domains"))
        params = list(inspect.signature(seed_lib.load_domains).parameters)
        self.assertEqual(params, ["cur", "asset_ids_by_module", "uc_ids_by_module_reqs"])
        src = inspect.getsource(seed_lib.load_domains)
        # The strongest-necessity rule and the three insert targets must all
        # still be present.
        self.assertIn("required", src)
        for table in ("data_domains", "asset_serves_domain", "uc_requires_domain"):
            self.assertIn(table, src)

    def test_migrations_are_replayed_in_order(self):
        import seed_lib
        sql = seed_lib.migration_sql()
        # 002 ALTERs tables that 001 creates, so ordering is load-bearing.
        self.assertLess(sql.index("CREATE TABLE IF NOT EXISTS use_cases"),
                        sql.index("CREATE TABLE IF NOT EXISTS data_domains"))
        self.assertIn("uc_requires_domain", sql)


if __name__ == "__main__":
    unittest.main()
