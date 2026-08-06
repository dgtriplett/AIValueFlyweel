"""The shipped P&U domain vocabulary must stay internally consistent.

These are cheap invariants that catch the failure mode this content is most prone
to: a typo in a module name silently dropping a mapping, so a domain looks
unsatisfiable and every use case needing it reads as blocked. Failing loudly in
CI beats debugging a phantom readiness gap in a customer demo.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from pu_catalog import MODULES  # noqa: E402
from pu_domains import DOMAINS, SERVES  # noqa: E402

# Must match the CHECK constraint on data_domains.category in
# server/migrations/002_domains.sql.
ALLOWED_CATEGORIES = {
    "operational", "asset", "customer", "grid", "market",
    "financial", "regulatory", "safety", "external", "workforce",
}


class TestDomainVocabulary(unittest.TestCase):
    def test_names_unique(self):
        names = [d[0] for d in DOMAINS]
        dupes = {n for n in names if names.count(n) > 1}
        self.assertEqual(dupes, set(), f"duplicate domain names: {dupes}")

    def test_names_are_snake_case_keys(self):
        for name, *_ in DOMAINS:
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$",
                             f"{name!r} is not a snake_case key")

    def test_every_domain_fully_populated(self):
        for entry in DOMAINS:
            self.assertEqual(len(entry), 5, f"malformed entry: {entry!r}")
            name, label, category, description, examples = entry
            self.assertTrue(label.strip(), f"{name}: empty label")
            self.assertTrue(description.strip(), f"{name}: empty description")
            self.assertTrue(examples.strip(), f"{name}: empty example_attributes")

    def test_categories_match_db_constraint(self):
        bad = {d[2] for d in DOMAINS} - ALLOWED_CATEGORIES
        self.assertEqual(bad, set(), f"categories not permitted by the CHECK: {bad}")

    def test_labels_unique(self):
        """Duplicate labels would make the UI ambiguous even if keys differ."""
        labels = [d[1] for d in DOMAINS]
        dupes = {l for l in labels if labels.count(l) > 1}
        self.assertEqual(dupes, set(), f"duplicate labels: {dupes}")


class TestServesMappings(unittest.TestCase):
    def setUp(self):
        self.valid_modules = {(c, m) for c, m, *_ in MODULES}
        self.domain_names = {d[0] for d in DOMAINS}

    def test_serves_keys_match_vocabulary_exactly(self):
        """Every domain needs a SERVES entry (even if empty) and vice versa, so a
        renamed domain can't leave an orphaned mapping behind."""
        self.assertEqual(set(SERVES), self.domain_names,
                         f"symmetric difference: {set(SERVES) ^ self.domain_names}")

    def test_all_module_references_resolve(self):
        """The important one: a typo here silently drops the mapping at seed time."""
        bad = [(d, p) for d, pairs in SERVES.items() for p in pairs
               if p not in self.valid_modules]
        self.assertEqual(bad, [], f"unknown (source_category, module) refs: {bad}")

    def test_no_duplicate_pairs_within_a_domain(self):
        for domain, pairs in SERVES.items():
            self.assertEqual(len(pairs), len(set(pairs)),
                             f"{domain} lists a module more than once")

    def test_every_domain_has_at_least_one_source(self):
        """A domain no module can serve is permanently unsatisfiable, which reads
        as a false permanent gap. If one is ever intentional, document it here."""
        empty = sorted(d for d, pairs in SERVES.items() if not pairs)
        self.assertEqual(empty, [], f"domains with no serving module: {empty}")

    def test_every_module_serves_at_least_one_domain(self):
        """An unmapped module can never satisfy a domain requirement, so landing
        it would show no readiness movement on the domain path."""
        mapped = {p for pairs in SERVES.values() for p in pairs}
        unmapped = sorted(self.valid_modules - mapped)
        self.assertEqual(unmapped, [], f"modules serving no domain: {unmapped}")


if __name__ == "__main__":
    unittest.main()
