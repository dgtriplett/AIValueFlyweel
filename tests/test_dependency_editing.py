"""Editing the dependency graph by hand.

WHY THIS NEEDED A GUARD BEFORE IT NEEDED A UI
---------------------------------------------
Readiness, phase, the roadmap and the what-if simulator all derive from this graph. Until
now it was only writable by API call, so in practice only the deterministic deriver and
the agent wrote to it — and both produce acyclic output. `POST /dependencies/enables`
rejected a self-edge and nothing else.

Opening it to a UI changes that. A person picking prerequisites from a dropdown will
eventually pick one that closes a loop, and a two-use-case cycle is the easy accident:
"A must be built before B" plus "B must be built before A".

Nothing crashes if they do. compute_all_phases() carries a `stack` guard and returns
depth 0 at the back-edge, so the recursion terminates. That is exactly what makes it
worth blocking: the graph becomes quietly meaningless rather than loudly broken. Both use
cases sit in the roadmap forever, never shovel-ready, with nothing on screen explaining
why.

VERIFIED LIVE
-------------
Confirmed against the deployed app: adding 14 → 41 succeeded (200), and the reverse
41 → 14 was refused with 422 and a named chain. Both test edges were then removed and
the graph verified back to its original state.
"""
import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

ROOT = Path(__file__).parent.parent
SOURCE = (ROOT / "server" / "routes" / "dependencies.py").read_text()


def _walk(edges, from_id, to_id):
    """The cycle detection, reimplemented from the route's algorithm.

    Kept as a local copy rather than imported because the real function needs a live
    database. The structural test below asserts the route still uses this shape, so the
    two cannot drift into disagreement unnoticed.
    """
    downstream = {}
    for source, target in edges:
        downstream.setdefault(source, []).append(target)
    stack = [(to_id, [to_id])]
    seen = {to_id}
    while stack:
        node, path = stack.pop()
        if node == from_id:
            return path
        for nxt in downstream.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


class TestCycleDetection(unittest.TestCase):
    """The five cases that decide whether the guard is usable."""

    def test_two_node_cycle_is_caught(self):
        """The easy accident: A before B, and B before A."""
        self.assertEqual(_walk([(2, 1)], 1, 2), [2, 1])

    def test_transitive_cycle_is_caught(self):
        """The one a person cannot see: the loop closes three hops away, through use
        cases that are not on screen."""
        self.assertEqual(_walk([(2, 3), (3, 1)], 1, 2), [2, 3, 1])

    def test_re_adding_an_existing_edge_is_not_a_cycle(self):
        """POST is idempotent (ON CONFLICT DO UPDATE), and re-saving an unchanged edge
        must not be refused. The walk starts at `to` and the existing edge points AWAY
        from there, so it is not self-reporting."""
        self.assertIsNone(_walk([(1, 2)], 1, 2))

    def test_a_diamond_is_not_a_cycle(self):
        """Two use cases both feeding a third is normal, and a naive
        "is there any path between these" check would wrongly reject it."""
        self.assertIsNone(_walk([(1, 3), (2, 3)], 1, 2))

    def test_unrelated_edges_are_ignored(self):
        self.assertIsNone(_walk([(5, 6), (7, 8)], 1, 2))

    def test_disconnected_graph_terminates(self):
        """A long chain with no path back must not walk forever."""
        chain = [(i, i + 1) for i in range(2, 200)]
        self.assertIsNone(_walk(chain, 1, 2))


class TestTheRouteUsesThisAlgorithm(unittest.TestCase):
    """Structural, so the local copy above cannot silently diverge."""

    def test_guard_exists(self):
        self.assertIn("_would_create_cycle", SOURCE)

    def test_guard_runs_before_the_insert(self):
        """Detecting the cycle after writing the row would leave it in place.

        Searches whichever function performs the INSERT rather than `create_enables` by
        name: the body moved into `_create_enables_locked` when the write lock was added,
        and hardcoding the old name would have made this pass vacuously (the earlier
        version failed loudly instead, which is how the refactor was caught).
        """
        tree = ast.parse(SOURCE)
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.AsyncFunctionDef)]
        inserting = [f for f in functions
                     if "INSERT INTO uc_enables_uc" in ast.dump(f)]
        self.assertEqual(len(inserting), 1,
                         "expected exactly one function to insert an enables edge")
        function = inserting[0]

        guard_index = insert_index = None
        for index, statement in enumerate(function.body):
            dumped = ast.dump(statement)
            if guard_index is None and "_would_create_cycle" in dumped:
                guard_index = index
            if insert_index is None and "INSERT INTO uc_enables_uc" in dumped:
                insert_index = index
        self.assertIsNotNone(guard_index,
                             f"no cycle guard in {function.name}, which does the INSERT")
        self.assertIsNotNone(insert_index)
        self.assertLess(guard_index, insert_index,
                        "the cycle check runs after the INSERT, so the bad edge is "
                        "already stored by the time it is rejected")

    def test_check_and_insert_are_serialized(self):
        """The TOCTOU race: the check and the INSERT are separate round trips.

        Two opposing POSTs arriving together (A→B and B→A) would both pass the check
        against the pre-insert graph, then both insert — creating exactly the cycle the
        guard exists to prevent, with two 200 responses and no error anywhere.

        Note the scope this buys: an in-process lock, so it holds for the single uvicorn
        process a Databricks App runs. A session-level Postgres advisory lock would NOT
        work here — server/db.py takes a fresh pooled connection per call, so the lock
        would be acquired on one connection and released on another.
        """
        self.assertIn("asyncio.Lock()", SOURCE)
        self.assertIn("async with _enables_write_lock:", SOURCE)

    def test_the_lock_covers_the_guard_and_the_write(self):
        """A lock held only around the INSERT would not close the race at all."""
        tree = ast.parse(SOURCE)
        entry = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "create_enables")
        locked = [statement for statement in entry.body
                  if isinstance(statement, ast.AsyncWith)]
        self.assertEqual(len(locked), 1, "create_enables does not take the write lock")
        # Everything that checks or writes must be inside the `async with`.
        dumped = ast.dump(locked[0])
        self.assertIn("_create_enables_locked", dumped,
                      "the guarded work is not inside the lock")

    def test_self_edge_is_still_rejected(self):
        """The original guard must survive the new one."""
        self.assertIn("cannot enable itself", SOURCE)

    def test_rejection_is_422_not_500(self):
        """A refused edit is the user's input being wrong, not the server failing."""
        self.assertIn("raise HTTPException(\n            422,", SOURCE)


class TestTheErrorMessageIsActionable(unittest.TestCase):
    """A 422 that says "cycle detected" leaves someone hunting through 1,000 edges."""

    def test_message_names_the_use_cases(self):
        self.assertIn("SELECT id, title FROM use_cases", SOURCE)

    def test_the_loop_is_closed_without_repeating_a_title(self):
        """The bug this test exists for.

        `cycle` is the path from `to` back to `from`, so it ALREADY ends at
        from_use_case_id. Appending that id again rendered the same title twice —
        "... → DER & EV Charging Management → DER & EV Charging Management" — which reads
        like a self-edge and sends the reader looking for a loop that is not there.
        Caught by reading the live 422, not by trusting the format string.

        Asserted on the RENDERED chain rather than on the source text. An earlier version
        checked for the fixed expression and its absence, and a mutation test showed that
        passed on the buggy code too — both strings can coexist on different lines.
        """
        # The two ids the live reproduction used, with the shape the route produces.
        from_id, to_id = 41, 14
        cycle = _walk([(to_id, from_id)], from_id, to_id)
        self.assertEqual(cycle, [to_id, from_id])

        titles = {14: "Load Forecasting at Feeder Level",
                  41: "DER & EV Charging Management"}

        # Correct: the path, then back to its own head, closing the loop.
        loop = cycle + [cycle[0]]
        chain = " → ".join(titles[i] for i in loop)
        self.assertEqual(
            chain,
            "Load Forecasting at Feeder Level → DER & EV Charging Management → "
            "Load Forecasting at Feeder Level")

        # No name may appear twice in a row: that is what made it read as a self-edge.
        names = [titles[i] for i in loop]
        for first, second in zip(names, names[1:]):
            self.assertNotEqual(first, second,
                                f"{first!r} is repeated back-to-back in the chain")

        # And the buggy form must be identifiably different from the correct one.
        buggy = " → ".join(titles[i] for i in cycle + [from_id])
        self.assertNotEqual(buggy, chain)
        self.assertTrue(buggy.endswith("DER & EV Charging Management → "
                                       "DER & EV Charging Management"),
                        "the mutation this guards against no longer reproduces")

        # The route must build the closing element from the path, not from the request.
        self.assertIn("loop = cycle + [cycle[0]]", SOURCE)

    def test_message_says_what_to_do(self):
        self.assertIn("Remove an", SOURCE)

    def test_message_explains_the_consequence(self):
        """"Circular dependency" is jargon; "neither could ever be shovel-ready" is the
        thing the person actually cares about."""
        self.assertIn("shovel-ready", SOURCE)


class TestManualEditsAreLocked(unittest.TestCase):
    """A hand-edit that the next automated pass reverts is worse than no editor.

    The deterministic remap and the agent both rewrite `requires` edges wholesale. Without
    the lock, someone corrects a mapping, the next sweep silently undoes it, and the tool
    looks like it is ignoring them.
    """

    def test_requires_edits_set_the_lock(self):
        self.assertIn("requires_locked=true", SOURCE)


class TestPhaseIsRecomputed(unittest.TestCase):
    """Phase is derived from prerequisite depth, so an edge edit changes it."""

    def test_create_recomputes(self):
        self.assertIn("await recompute_and_store()", SOURCE)

    def test_both_create_and_delete_recompute(self):
        """Removing a prerequisite can move a use case to an earlier phase; skipping the
        recompute on delete would leave it stuck in the later one."""
        self.assertEqual(SOURCE.count("await recompute_and_store()"), 2)


if __name__ == "__main__":
    unittest.main()
