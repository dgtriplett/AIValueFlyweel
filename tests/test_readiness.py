"""Readiness scoring: the classify() truth table and the dual-path selection.

The dual path is the riskiest logic added by the domain layer — it decides which
requirement model is authoritative for each use case, and getting it wrong either
silently discards curated module requirements or reports false readiness. These
tests pin the precedence rules stated in server/readiness.py's docstring.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401  (must precede any server.* import)
from fakedb import FakeDB, Row, run  # noqa: E402

# readiness imports `db` at module scope, so patch the module attribute.
from server import readiness  # noqa: E402

MODULE_Q = "FROM use_cases uc\n        LEFT JOIN uc_requires_asset"
DOMAIN_Q = "domain_satisfaction"
PREREQ_Q = "FROM uc_enables_uc e"


def _module_row(uc_id, total, ready, locked=False):
    return Row(use_case_id=uc_id, requires_locked=locked,
               required_total=total, required_ready=ready)


def _domain_row(uc_id, total, ready, pending=None):
    return Row(use_case_id=uc_id, required_total=total, required_ready=ready,
               pending_domains=pending if pending is not None else [])


class TestClassify(unittest.TestCase):
    """classify() is pure; enumerate the states rather than sampling them."""

    def test_no_requirements_is_shovel_ready(self):
        # total==0 must be 100%, not a division-by-zero or a 0% "blocked".
        self.assertEqual(readiness.classify(0, 0, 0, 0), ("shovel_ready", 1.0))

    def test_all_data_and_prereqs_met(self):
        self.assertEqual(readiness.classify(3, 3, 2, 2), ("shovel_ready", 1.0))

    def test_data_ready_but_prereqs_outstanding(self):
        # The distinct fourth state: not a data gap, so it must NOT read as blocked.
        label, pct = readiness.classify(3, 3, 2, 1)
        self.assertEqual(label, "awaiting_prerequisites")
        self.assertEqual(pct, 1.0)

    def test_half_data_is_nearly_ready(self):
        self.assertEqual(readiness.classify(1, 2, 0, 0)[0], "nearly_ready")

    def test_just_below_half_is_blocked(self):
        # 2/5 = 0.4 -> blocked; the 0.5 boundary is inclusive on nearly_ready.
        self.assertEqual(readiness.classify(2, 5, 0, 0)[0], "blocked")
        self.assertEqual(readiness.classify(1, 2, 0, 0)[0], "nearly_ready")

    def test_no_data_is_blocked(self):
        self.assertEqual(readiness.classify(0, 4, 0, 0), ("blocked", 0.0))

    def test_prereqs_cannot_rescue_incomplete_data(self):
        # Data gates first: prereqs all built must not upgrade a data gap.
        self.assertEqual(readiness.classify(1, 4, 1, 1)[0], "blocked")


class TestDualPath(unittest.TestCase):
    def setUp(self):
        self._real_db = readiness.db

    def tearDown(self):
        readiness.db = self._real_db

    def _map(self, fake):
        readiness.db = fake
        return run(readiness.readiness_map())

    def test_module_path_when_no_domains(self):
        """An install with an unpopulated domain layer must behave exactly like
        the pre-domain app."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 2, 1)])
                .on(DOMAIN_Q, [])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["requirement_model"], "module")
        self.assertEqual(out["readiness"], "nearly_ready")
        self.assertEqual((out["required_ready"], out["required_total"]), (1, 2))

    def test_domain_path_preferred_when_declared(self):
        """Domain counts must win over module counts — this is the vendor
        substitution fix: modules say 1/3, domains say 2/2 -> ready."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 3, 1)])
                .on(DOMAIN_Q, [_domain_row(1, 2, 2)])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["requirement_model"], "domain")
        self.assertEqual(out["readiness"], "shovel_ready")
        self.assertEqual((out["required_ready"], out["required_total"]), (2, 2))

    def test_requires_locked_forces_module_path(self):
        """A human-curated module requirement set outranks the permissive domain
        rule, even when domains would report ready."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 3, 1, locked=True)])
                .on(DOMAIN_Q, [_domain_row(1, 2, 2)])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["requirement_model"], "module")
        self.assertEqual(out["readiness"], "blocked")  # 1/3 < 0.5

    def test_empty_domain_row_falls_back_to_module(self):
        """A use case present in the domain aggregate but with zero REQUIRED
        domains (only 'helpful' ones) must not be scored 0/0 = ready."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 4, 0)])
                .on(DOMAIN_Q, [_domain_row(1, 0, 0)])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["requirement_model"], "module")
        self.assertEqual(out["readiness"], "blocked")

    def test_pending_domains_surfaced(self):
        pending = [{"name": "outage_records", "label": "Outage & Interruption Records"}]
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 1, 1)])
                .on(DOMAIN_Q, [_domain_row(1, 2, 1, pending=pending)])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["pending_domains"], pending)
        self.assertEqual(out["readiness"], "nearly_ready")

    def test_pending_domains_accepts_json_string(self):
        """asyncpg returns jsonb as a str when no codec is registered; the
        aggregate must survive both shapes."""
        raw = '[{"name": "market_prices", "label": "Market & Locational Prices"}]'
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 1, 1)])
                .on(DOMAIN_Q, [_domain_row(1, 2, 1, pending=raw)])
                .on(PREREQ_Q, []))
        out = self._map(fake)[1]
        self.assertEqual(out["pending_domains"][0]["name"], "market_prices")

    def test_malformed_pending_domains_degrades_to_empty(self):
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 1, 1)])
                .on(DOMAIN_Q, [_domain_row(1, 1, 1, pending="not json")])
                .on(PREREQ_Q, []))
        self.assertEqual(self._map(fake)[1]["pending_domains"], [])

    def test_prereqs_folded_into_domain_path(self):
        """Domain data complete + an unbuilt prerequisite = awaiting_prerequisites,
        and the pending prerequisite is named."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(7, 1, 1)])
                .on(DOMAIN_Q, [_domain_row(7, 2, 2)])
                .on(PREREQ_Q, [Row(uc_id=7, prereq_id=3,
                                   prereq_title="Land AMI", built=False)]))
        out = self._map(fake)[7]
        self.assertEqual(out["readiness"], "awaiting_prerequisites")
        self.assertEqual(out["pending_prereqs"], [{"id": 3, "title": "Land AMI"}])
        self.assertEqual((out["prereqs_built"], out["prereqs_total"]), (0, 1))

    def test_use_cases_scored_independently(self):
        """Path selection is per use case, not global."""
        fake = (FakeDB()
                .on(MODULE_Q, [_module_row(1, 2, 2), _module_row(2, 2, 0)])
                .on(DOMAIN_Q, [_domain_row(2, 3, 3)])
                .on(PREREQ_Q, []))
        out = self._map(fake)
        self.assertEqual(out[1]["requirement_model"], "module")
        self.assertEqual(out[1]["readiness"], "shovel_ready")
        self.assertEqual(out[2]["requirement_model"], "domain")
        self.assertEqual(out[2]["readiness"], "shovel_ready")

    def test_readiness_for_unknown_id_has_full_shape(self):
        """Callers index into this dict; a missing id must not KeyError later."""
        readiness.db = FakeDB()
        out = run(readiness.readiness_for(999))
        for key in ("readiness", "ready_pct", "required_total", "required_ready",
                    "prereqs_total", "prereqs_built", "pending_prereqs",
                    "requirement_model", "pending_domains"):
            self.assertIn(key, out)


class TestSingleSourceOfTruth(unittest.TestCase):
    """The readiness rule must be defined exactly once.

    Five modules previously restated ("curated", "governed") as a local constant.
    Nothing broke — until the rule changed, at which point four of them would have
    silently disagreed with how readiness is actually computed, and the app would
    report two different answers to "is this satisfied?" depending on which endpoint
    you asked. This is the most load-bearing rule in the app, so it gets a test.
    """

    def test_readiness_defines_it(self):
        self.assertEqual(readiness.READY_STATUSES, ("curated", "governed"))

    def test_no_module_redefines_it_locally(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).parent.parent / "server"
        offenders = []
        pattern = re.compile(r'^\s*(READY|READY_STATUSES)\s*=\s*\(\s*["\']curated')
        for path in root.rglob("*.py"):
            if path.name == "readiness.py":
                continue  # the one legitimate definition
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.match(line):
                    offenders.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(
            offenders, [],
            "these modules restate the readiness rule instead of importing "
            f"READY_STATUSES from readiness.py: {offenders}")

    def test_consumers_import_it(self):
        """The modules that need the rule should be getting it from one place."""
        from server.routes import domains, flow, joint_funding, source_recommendations
        for module in (domains, flow, joint_funding, source_recommendations):
            self.assertIs(module.READY, readiness.READY_STATUSES,
                          f"{module.__name__} is not using the shared constant")


if __name__ == "__main__":
    unittest.main()
