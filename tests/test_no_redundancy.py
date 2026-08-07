"""Invariants that keep two code paths from answering the same question differently.

This app was assembled by merging two codebases and then adding six features, which
is precisely how you end up with N implementations of one rule that agree today and
diverge later. These tests pin the consolidations rather than trusting review to
catch a re-divergence.

Each test corresponds to a real duplication that existed and was fixed.
"""
import inspect
import os
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import stubs  # noqa: E402,F401
from server import value_engine as ve  # noqa: E402
from server import readiness  # noqa: E402

SERVER = ROOT / "server"


class TestOneValueEngine(unittest.TestCase):
    """Every dollar figure must come from one evaluator."""

    def test_no_module_reimplements_value_computation(self):
        """A second implementation of component x assumption arithmetic would let
        two endpoints report different value for the same use case."""
        offenders = []
        for path in SERVER.rglob("*.py"):
            if path.name == "value_engine.py":
                continue
            source = path.read_text()
            # The signature of a hand-rolled evaluator is ARITHMETIC on a
            # multiplier against assumption lookups. Constructing a value-model
            # dict (a prompt example, a heuristic fallback) is fine and common, so
            # match the multiplication rather than the field names.
            if re.search(r'(multiplier|\bmult\b)\s*\*\s*\w*assumption', source, re.I) \
                    or re.search(r'assumptions\[[^\]]+\]\s*\*', source):
                offenders.append(str(path.relative_to(SERVER)))
        self.assertEqual(offenders, [],
                         f"these evaluate value models outside value_engine: {offenders}")

    def test_shared_cost_helpers_are_used_not_copied(self):
        from server.routes import joint_funding, source_recommendations
        for module in (joint_funding, source_recommendations):
            self.assertIs(module.use_case_value, ve.use_case_value, module.__name__)
            self.assertIs(module.asset_cost, ve.asset_cost, module.__name__)
            self.assertIs(module.EFFORT_COST, ve.EFFORT_COST, module.__name__)

    def test_no_module_redefines_the_effort_cost_table(self):
        """Both recommenders rank by value-per-cost; divergent tables would rank
        the same investment differently."""
        offenders = []
        pattern = re.compile(r'^\s*_?EFFORT_COST\s*=\s*\{')
        for path in SERVER.rglob("*.py"):
            if path.name == "value_engine.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.match(line):
                    offenders.append(f"{path.relative_to(SERVER)}:{number}")
        self.assertEqual(offenders, [], f"local cost tables: {offenders}")


class TestUnlocksAgreesWithImpact(unittest.TestCase):
    """/use-cases/{id}/unlocks and /impact/{node} answer the same question.

    Regression: /unlocks checked data completeness only, while /impact also required
    upstream prerequisites to be built. The two endpoints therefore disagreed about
    what "unlocked" means, and /unlocks overstated it — a use case waiting on
    upstream work was reported as an immediate unlock.
    """

    def test_unlocks_requires_prerequisites_to_be_built(self):
        from server.routes import use_cases
        source = inspect.getsource(use_cases.get_unlocks)
        self.assertIn("prereqs_built", source,
                      "/unlocks does not consider prerequisites; it will overstate "
                      "unlocks and disagree with /api/impact")
        self.assertIn("BUILT_STATUSES", source,
                      "/unlocks should use the shared built-status definition")

    def test_unlocks_reports_awaiting_prerequisites_separately(self):
        """Data-complete-but-prereq-blocked is a real fast-follow; it should be
        surfaced, not silently dropped."""
        from server.routes import use_cases
        source = inspect.getsource(use_cases.get_unlocks)
        self.assertIn("awaiting_prerequisites", source)

    def test_impact_still_requires_prerequisites(self):
        from server.routes import impact
        source = inspect.getsource(impact)
        self.assertIn("prereqs_ok", source)


class TestOneReadinessRule(unittest.TestCase):
    def test_built_statuses_defined_once(self):
        self.assertEqual(readiness.BUILT_STATUSES, ("live", "value_realized"))
        offenders = []
        pattern = re.compile(r'^\s*BUILT_STATUSES\s*=')
        for path in SERVER.rglob("*.py"):
            if path.name == "readiness.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.match(line):
                    offenders.append(f"{path.relative_to(SERVER)}:{number}")
        self.assertEqual(offenders, [], f"local BUILT_STATUSES: {offenders}")


class TestNoDuplicateLlmPlumbing(unittest.TestCase):
    def test_one_llm_call_site(self):
        """Every model call must inherit the parameter negotiation and
        content-block handling in _llm_json. A second hand-rolled call site is how
        the sonnet-5 temperature rejection kept resurfacing."""
        offenders = []
        for path in SERVER.rglob("*.py"):
            source = path.read_text()
            count = source.count("chat.completions.create")
            if count and path.name not in ("agents.py", "chat.py", "llm.py"):
                offenders.append(f"{path.relative_to(SERVER)} ({count})")
        self.assertEqual(offenders, [],
                         f"LLM calls outside the sanctioned modules: {offenders}")

    def test_content_flattening_is_shared(self):
        """chat.py and agents.py both receive model output; a second flattener
        would handle reasoning blocks differently."""
        from server.routes import agents, chat
        source = inspect.getsource(chat)
        self.assertIn("flatten_content", source)
        self.assertTrue(callable(agents.flatten_content))

    def test_nested_json_unwrapping_is_shared(self):
        """generation and taxonomy both parse model arrays that can arrive doubly
        nested; taxonomy imports generation's unwrapper rather than copying it."""
        from server import taxonomy
        self.assertIn("unwrap_list", inspect.getsource(taxonomy.parse_classifications))


if __name__ == "__main__":
    unittest.main()
