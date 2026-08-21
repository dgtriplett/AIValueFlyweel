"""Rate limits and query budgets.

Two properties matter most and are both easy to get subtly wrong:

  1. Limits are PER ACTOR. A shared counter would mean one person clicking fast
     locks out a room — which in a workshop is worse than the load being limited.
  2. Budgets are per REQUEST under concurrency. A module global would charge one
     request's queries to another's budget, so the failure would be attributed to
     whichever request happened to be unlucky.

Also pinned: the limiter must fail OPEN. It exists to stop accidental load, and an
outage caused by the thing preventing outages is a bad trade.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from fastapi import HTTPException  # noqa: E402

from server import limits  # noqa: E402


class FakeRequest:
    """Just enough of starlette's Request for actor_key()."""

    class _Client:
        def __init__(self, host):
            self.host = host

    def __init__(self, headers=None, host="10.0.0.1"):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = self._Client(host) if host else None


class LimitsTestCase(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.addCleanup(limits.reset)
        # Tests must not depend on how the ambient environment set RATE_LIMITS.
        self._saved = limits.LIMITS_ENABLED
        limits.LIMITS_ENABLED = True
        self.addCleanup(setattr, limits, "LIMITS_ENABLED", self._saved)


class TestBucket(LimitsTestCase):
    def test_allows_up_to_capacity_then_refuses(self):
        bucket = limits.Bucket(capacity=3, refill_per_second=1.0, tokens=3.0,
                               last_refill=0.0)
        for _ in range(3):
            allowed, _ = bucket.take(now=0.0)
            self.assertTrue(allowed)
        allowed, retry_after = bucket.take(now=0.0)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_refills_continuously(self):
        """A token bucket, not a fixed window.

        A fixed window permits the whole allowance at the end of one window and
        again at the start of the next — a 2x burst at the boundary.
        """
        bucket = limits.Bucket(capacity=2, refill_per_second=1.0, tokens=0.0,
                               last_refill=0.0)
        self.assertFalse(bucket.take(now=0.0)[0])
        self.assertTrue(bucket.take(now=1.01)[0], "should refill after ~1s")

    def test_never_refills_past_capacity(self):
        bucket = limits.Bucket(capacity=2, refill_per_second=1.0, tokens=0.0,
                               last_refill=0.0)
        bucket.take(now=1_000.0)   # a long idle period
        self.assertLessEqual(bucket.tokens, 2.0)

    def test_retry_after_reflects_the_actual_deficit(self):
        bucket = limits.Bucket(capacity=1, refill_per_second=0.5, tokens=0.0,
                               last_refill=0.0)
        _, retry_after = bucket.take(now=0.0)
        self.assertAlmostEqual(retry_after, 2.0, places=2)


class TestPerActorIsolation(LimitsTestCase):
    def test_one_actor_exhausting_does_not_affect_another(self):
        limit = limits.LIMITS["sweep"]
        for _ in range(limit.burst):
            self.assertTrue(limit.check("alice")[0])
        self.assertFalse(limit.check("alice")[0])
        self.assertTrue(limit.check("bob")[0],
                        "bob must not be limited by alice's usage")

    def test_limit_classes_are_independent(self):
        """Exhausting research must not block chat — different costs, different
        allowances."""
        for _ in range(limits.LIMITS["research"].burst):
            limits.LIMITS["research"].check("alice")
        self.assertFalse(limits.LIMITS["research"].check("alice")[0])
        self.assertTrue(limits.LIMITS["chat"].check("alice")[0])


class TestActorKey(LimitsTestCase):
    def test_prefers_the_platform_identity_header(self):
        # Set by Databricks Apps and not forgeable from the browser.
        request = FakeRequest({"X-Forwarded-Email": "Drew@Example.com",
                               "X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(limits.actor_key(request), "drew@example.com")

    def test_normalizes_case_so_one_user_is_one_actor(self):
        self.assertEqual(
            limits.actor_key(FakeRequest({"X-Forwarded-Email": "A@B.com"})),
            limits.actor_key(FakeRequest({"X-Forwarded-Email": "a@b.com"})))

    def test_falls_back_to_forwarded_for_first_hop(self):
        request = FakeRequest({"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        self.assertEqual(limits.actor_key(request), "ip:1.2.3.4")

    def test_falls_back_to_peer_address(self):
        self.assertEqual(limits.actor_key(FakeRequest(host="10.0.0.7")),
                         "ip:10.0.0.7")

    def test_handles_a_missing_client(self):
        # Possible with some ASGI transports; must not raise.
        self.assertEqual(limits.actor_key(FakeRequest(host=None)), "anonymous")


class TestEnforce(LimitsTestCase):
    def test_raises_429_with_retry_after(self):
        request = FakeRequest({"X-Forwarded-Email": "a@b.com"})
        for _ in range(limits.LIMITS["sweep"].burst):
            limits.enforce("sweep", request)
        with self.assertRaises(HTTPException) as caught:
            limits.enforce("sweep", request)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Retry-After", caught.exception.headers)
        self.assertGreaterEqual(int(caught.exception.headers["Retry-After"]), 1)

    def test_message_explains_it_is_a_guard_rail(self):
        """A bare "429 Too Many Requests" reads like a quota or a ban."""
        request = FakeRequest({"X-Forwarded-Email": "a@b.com"})
        for _ in range(limits.LIMITS["sweep"].burst):
            limits.enforce("sweep", request)
        with self.assertRaises(HTTPException) as caught:
            limits.enforce("sweep", request)
        self.assertIn("guard rail", caught.exception.detail)

    def test_disabled_by_env_allows_everything(self):
        limits.LIMITS_ENABLED = False
        request = FakeRequest({"X-Forwarded-Email": "a@b.com"})
        for _ in range(200):
            limits.enforce("sweep", request)   # must not raise

    def test_unknown_limit_fails_open(self):
        """A typo in a limit name must not break the endpoint it guards.

        The limiter prevents accidental load; taking an endpoint down to do so
        would be a worse outcome than the load.
        """
        limits.enforce("no-such-limit", FakeRequest())   # must not raise


class TestActorTableIsBounded(LimitsTestCase):
    def test_does_not_grow_without_limit(self):
        """A caller varying its identity header must not grow memory forever."""
        limit = limits.LIMITS["write"]
        for i in range(limits._MAX_TRACKED_ACTORS + 500):
            limit.check(f"actor-{i}")
        self.assertLessEqual(len(limit.buckets), limits._MAX_TRACKED_ACTORS)

    def test_evicts_stale_buckets_first(self):
        limit = limits.Limit("t", burst=1, per_minute=60)
        now = time.monotonic()
        # An idle bucket is full anyway, so dropping it changes no decision.
        limit.buckets["stale"] = limits.Bucket(
            1.0, 1.0, 1.0, now - limits._STALE_AFTER_SECONDS - 10)
        limit.buckets["fresh"] = limits.Bucket(1.0, 1.0, 0.0, now)
        limits._evict_stale(limit.buckets, now)
        self.assertNotIn("stale", limit.buckets)
        self.assertIn("fresh", limit.buckets)


class TestSnapshot(LimitsTestCase):
    def test_reports_enough_to_diagnose_a_429_report(self):
        limits.LIMITS["chat"].check("a@b.com")
        snapshot = limits.snapshot()
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["limits"]["chat"]["tracked_actors"], 1)
        self.assertIn("burst", snapshot["limits"]["chat"])
        self.assertIn("per_minute", snapshot["limits"]["chat"])

    def test_never_exposes_actor_identities(self):
        """The snapshot is served on /api/health, so it must not leak who used it."""
        limits.LIMITS["chat"].check("secret.person@example.com")
        self.assertNotIn("secret.person", str(limits.snapshot()))


class TestQueryBudget(LimitsTestCase):
    def test_allows_up_to_the_limit(self):
        budget = limits.QueryBudget(3)
        for _ in range(3):
            budget.charge()
        self.assertEqual(budget.used, 3)
        self.assertEqual(budget.remaining, 0)

    def test_raises_past_the_limit(self):
        budget = limits.QueryBudget(2, label="This turn")
        budget.charge()
        budget.charge()
        with self.assertRaises(limits.BudgetExceeded) as caught:
            budget.charge()
        self.assertIn("This turn", str(caught.exception))

    def test_remaining_never_goes_negative(self):
        budget = limits.QueryBudget(1)
        budget.charge()
        try:
            budget.charge()
        except limits.BudgetExceeded:
            pass
        self.assertEqual(budget.remaining, 0)


class TestQueryBudgetChargesRealQueries(LimitsTestCase):
    """Charged at db.fetch/execute, so it counts queries rather than estimating."""

    def test_db_queries_are_charged(self):
        import asyncio

        from server.db import db

        async def run():
            with limits.query_budget(4, "test") as budget:
                for _ in range(4):
                    await db.fetch("SELECT 1")
                self.assertEqual(budget.used, 4)
                with self.assertRaises(limits.BudgetExceeded):
                    await db.fetch("SELECT 1")

        asyncio.run(run())

    def test_execute_is_charged_too(self):
        import asyncio

        from server.db import db

        async def run():
            with limits.query_budget(2, "test") as budget:
                await db.execute("UPDATE t SET x=1")
                await db.execute("UPDATE t SET x=2")
                self.assertEqual(budget.used, 2)
                with self.assertRaises(limits.BudgetExceeded):
                    await db.execute("UPDATE t SET x=3")

        asyncio.run(run())

    def test_no_budget_means_no_limit_and_no_error(self):
        """Almost every request sets no budget; those must be unaffected."""
        import asyncio

        from server.db import db

        async def run():
            for _ in range(300):
                await db.fetch("SELECT 1")
            self.assertIsNone(limits.current_budget.get())

        asyncio.run(run())

    def test_budget_is_cleared_after_the_block(self):
        import asyncio

        async def run():
            with limits.query_budget(5, "test"):
                self.assertIsNotNone(limits.current_budget.get())
            self.assertIsNone(limits.current_budget.get())

        asyncio.run(run())

    def test_budget_is_cleared_even_when_exceeded(self):
        """A leaked budget would charge the NEXT request on this worker."""
        import asyncio

        from server.db import db

        async def run():
            try:
                with limits.query_budget(1, "test"):
                    await db.fetch("SELECT 1")
                    await db.fetch("SELECT 1")
            except limits.BudgetExceeded:
                pass
            self.assertIsNone(limits.current_budget.get())

        asyncio.run(run())

    def test_concurrent_requests_have_separate_budgets(self):
        """The property a module global would silently get wrong."""
        import asyncio

        from server.db import db

        async def request(limit):
            with limits.query_budget(limit, "req") as budget:
                for _ in range(limit):
                    await db.fetch("SELECT 1")
                    await asyncio.sleep(0)   # force interleaving
                return budget.used

        async def run():
            return await asyncio.gather(request(3), request(7), request(5))

        self.assertEqual(asyncio.run(run()), [3, 7, 5])


class TestBudgetIsNotSwallowed(LimitsTestCase):
    """BudgetExceeded must propagate past the chat loop's broad handlers.

    THE BUG THIS CAUGHT: BudgetExceeded is a RuntimeError, and the chat loop wraps
    each tool call in `except Exception` to report a failure to the model rather
    than failing the whole turn. That converted the budget into a tool-error string
    the model could route around — so the budget bounded nothing, and the request
    kept querying. The outer handler was worse: it turned it into a 502 "assistant
    unavailable", pointing support at the serving endpoint for what was actually
    this request exceeding its own database budget.

    Enforced structurally (does the code re-raise?) rather than by simulating a
    turn, because reaching the tool-call path requires a live model.
    """

    def setUp(self):
        super().setUp()
        import inspect

        from server.routes import chat
        self.source = inspect.getsource(chat)

    def test_tool_call_handler_reraises_the_budget(self):
        # The `except BudgetExceeded: raise` must appear BEFORE the broad handler,
        # or Python matches the broad one first.
        budget_at = self.source.find("except BudgetExceeded")
        self.assertNotEqual(budget_at, -1,
                            "the chat loop does not re-raise BudgetExceeded — the "
                            "query budget bounds nothing")
        broad_at = self.source.find("except Exception as exc:  # noqa: BLE001")
        self.assertLess(budget_at, broad_at,
                        "except BudgetExceeded must precede except Exception, or "
                        "the broad handler matches first")

    def test_both_swallow_points_reraise(self):
        """Two handlers could swallow it: the per-tool one and the outer one.

        There are THREE `except BudgetExceeded` clauses in the module and the
        distinction matters: the two inside `_chat_turn` must re-raise, while the
        one in the `chat` wrapper is where it becomes a 429. Asserting a bare count
        conflates them — as an earlier version of this test did.
        """
        lines = self.source.split("\n")
        reraise = 0
        converts = 0
        for index, line in enumerate(lines):
            if not line.strip().startswith("except BudgetExceeded"):
                continue
            # The clause body: the next few non-comment, non-blank lines.
            body = [candidate.strip() for candidate in lines[index + 1:index + 8]
                    if candidate.strip() and not candidate.strip().startswith("#")]
            if body and body[0] == "raise":
                reraise += 1
            elif any("HTTPException(429" in statement for statement in body):
                converts += 1

        self.assertEqual(reraise, 2,
                         "both handlers inside _chat_turn must bare-`raise` the "
                         f"budget so it reaches the wrapper (found {reraise})")
        self.assertEqual(converts, 1,
                         "exactly one handler should convert the budget into a "
                         f"429 (found {converts})")

    def test_a_budget_exception_is_not_confusable_with_a_tool_failure(self):
        """Reads as a distinct type at every catch site."""
        self.assertTrue(issubclass(limits.BudgetExceeded, Exception))
        self.assertIsNot(limits.BudgetExceeded, RuntimeError)


class TestLimiterRunsBeforeBodyValidation(LimitsTestCase):
    """A flood of MALFORMED requests must be limited too.

    If the limiter ran after body validation, an attacker or a broken client
    sending garbage would never be limited — every request would 422 having already
    consumed the model call or warehouse query behind it. Driving the real app is
    the only way to establish the ordering, since it is FastAPI's, not ours.
    """

    def _request(self, method, path, body=b""):
        import asyncio

        import app as app_module

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "scheme": "http",
            "server": ("test", 80), "client": ("9.9.9.9", 1),
            "headers": [(b"x-forwarded-email", b"limits-test@example.com"),
                        (b"content-type", b"application/json")],
        }
        captured = {}

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]

        asyncio.run(app_module.app(scope, receive, send))
        return captured["status"]

    def _post(self, path, body):
        return self._request("POST", path, body)

    def test_invalid_bodies_still_consume_the_limit(self):
        import json

        burst = limits.LIMITS["chat"].burst
        garbage = json.dumps({"not_the_expected_field": "x"}).encode()

        statuses = [self._post("/api/chat", garbage) for _ in range(burst + 2)]
        self.assertTrue(all(status == 422 for status in statuses[:burst]),
                        f"expected the first {burst} to fail validation: {statuses}")
        self.assertEqual(statuses[burst], 429,
                         "a flood of malformed requests was not rate limited — the "
                         f"limiter is running after body validation: {statuses}")

    def test_new_model_posts_consume_research_limit_before_validation(self):
        import json

        cases = {
            "/api/genie/ask": {"not_the_expected_field": "x"},
            "/api/agents/detect-dependencies": {"not_the_expected_field": "x"},
            "/api/agents/recommend": {"top_n": "not-an-integer"},
            "/api/agents/estimate-value": {"use_case_id": "not-an-integer"},
            "/api/agents/decompose-source": {"not_the_expected_field": "x"},
        }
        burst = limits.LIMITS["research"].burst

        for path, payload in cases.items():
            with self.subTest(path=path):
                limits.reset()
                body = json.dumps(payload).encode()
                statuses = [self._post(path, body) for _ in range(burst + 1)]
                self.assertEqual(statuses[:burst], [422] * burst)
                self.assertEqual(statuses[burst], 429)

    def test_customer_enhancements_uses_research_limit(self):
        actor = "limits-test@example.com"
        for _ in range(limits.LIMITS["research"].burst):
            self.assertTrue(limits.LIMITS["research"].check(actor)[0])

        self.assertEqual(
            self._request("GET", "/api/agents/customer-enhancements"), 429)

    def test_rate_limits_off_bypasses_new_route_dependencies(self):
        import json

        limits.LIMITS_ENABLED = False
        burst = limits.LIMITS["research"].burst
        garbage = json.dumps({"not_the_expected_field": "x"}).encode()
        statuses = [self._post("/api/genie/ask", garbage)
                    for _ in range(burst + 2)]
        self.assertEqual(statuses, [422] * (burst + 2))


class TestExpensiveEndpointsAreLimited(unittest.TestCase):
    """Every endpoint that costs money or warehouse time must declare a limit.

    Written as an inventory check rather than one test per endpoint: the failure
    to guard against is a NEW expensive endpoint added later with no limit, which
    a per-endpoint test suite would not notice.
    """

    # Endpoint path fragment -> the limit class it must use.
    EXPECTED = {
        "server/routes/chat.py": [('@router.post("", ', "chat")],
        "server/routes/agents.py": [('/detect-dependencies"', "research"),
                                     ("get", '/customer-enhancements"',
                                      "research"),
                                     ('/recommend"', "research"),
                                     ('/estimate-value"', "research"),
                                     ('/decompose-source"', "research")],
        "server/routes/genie.py": [('/ask"', "research"),
                                    ('/provision"', "sweep")],
        "server/routes/research.py": [('/company"', "research"), ('/apply"', "write")],
        "server/routes/generate.py": [('/use-cases"', "generate"),
                                      ('/use-cases/commit"', "write")],
        "server/routes/taxonomy.py": [('/classify"', "generate")],
        "server/routes/ingestion.py": [('/bootstrap"', "sweep"),
                                       ('/enrich/schemas"', "sweep"),
                                       ('/enrich/tables"', "sweep"),
                                       ('/upload/schemas"', "sweep"),
                                       ('/upload/tables"', "sweep"),
                                       ('/upload/columns"', "sweep"),
                                       ('/canonicalize"', "generate"),
                                       ('/attribute"', "generate")],
        "server/routes/inventory.py": [('/artifacts/sync"', "sweep"),
                                       ('/rules/test"', "generate")],
        # Eight prose sections at 8000 output tokens costs like company research,
        # not like a short classification call — hence 'research', not 'generate'.
        "server/routes/proposals.py": [('/use-cases/{use_case_id}"', "research")],
        "server/routes/live.py": [('/sync"', "sweep"), ('/sync-genie"', "sweep")],
    }

    def test_each_expensive_endpoint_declares_its_limit(self):
        from pathlib import Path

        root = Path(__file__).parent.parent
        missing = []
        for relative, expectations in self.EXPECTED.items():
            lines = (root / relative).read_text().split("\n")
            for expectation in expectations:
                if len(expectation) == 2:
                    fragment, limit_name = expectation
                    method = "post"
                else:
                    method, fragment, limit_name = expectation
                for index, line in enumerate(lines):
                    if not (line.startswith(f"@router.{method}(")
                            and fragment in line):
                        continue
                    # A decorator can wrap across lines. Reading only the first one
                    # reported a correctly-limited endpoint as unlimited, which
                    # means the same bug would have hidden a REAL missing limit on
                    # any wrapped decorator. Join until the parens balance.
                    decorator, depth = "", 0
                    for candidate in lines[index:]:
                        decorator += candidate
                        depth += candidate.count("(") - candidate.count(")")
                        if depth <= 0:
                            break
                    if f'limiter("{limit_name}")' not in decorator:
                        missing.append(f"{relative} {fragment} "
                                       f"(expected {limit_name})")
                    break
                else:
                    missing.append(f"{relative} {fragment} — endpoint not found")
        self.assertEqual(missing, [],
                         f"expensive endpoints without the expected rate limit: "
                         f"{missing}")

    def test_every_warehouse_touching_post_is_limited(self):
        """Derived from the code, not from a list I maintain by hand.

        THE GAP THIS CAUGHT: the three CSV upload endpoints each parse a file and
        run batched warehouse MERGEs, and all three shipped unlimited — the
        hand-written inventory above only checked endpoints I had thought to list,
        which is precisely the blind spot an inventory has. Any POST whose handler
        reaches the warehouse must declare a limit.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).parent.parent
        # Functions that execute against the SQL warehouse.
        warehouse_calls = {"run_sql", "_merge_rows", "run_statement"}
        unlimited = []

        for path in sorted((root / "server" / "routes").glob("*.py")):
            text = path.read_text()
            tree = ast.parse(text)
            lines = text.split("\n")
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                post = [d for d in node.decorator_list
                        if isinstance(d, ast.Call)
                        and getattr(d.func, "attr", "") == "post"]
                if not post:
                    continue
                calls = {getattr(inner.func, "id", None)
                         or getattr(inner.func, "attr", None)
                         for inner in ast.walk(node)
                         if isinstance(inner, ast.Call)}
                if not (calls & warehouse_calls):
                    continue
                decorator_text = lines[post[0].lineno - 1]
                if "limiter(" not in decorator_text:
                    unlimited.append(
                        f"server/routes/{path.name}:{post[0].lineno} "
                        f"{node.name}()")

        self.assertEqual(
            unlimited, [],
            "these POST endpoints reach the SQL warehouse but declare no rate "
            f"limit, so concurrent runs can saturate it: {unlimited}")

    def test_every_llm_calling_module_is_covered(self):
        """A module that calls the model must appear in EXPECTED above.

        Catches the real regression: someone adds an LLM endpoint in a new module
        and nothing here fails, so it ships unlimited.
        """
        from pathlib import Path

        root = Path(__file__).parent.parent
        # joint_funding calls llm_text from a GET (a narrative brief). GETs are not
        # rate limited here; it is a single call on an explicit user action, and the
        # 'write'/'generate' classes cover the paths that mutate or cost the most.
        exempt = {"server/routes/joint_funding.py", "server/routes/agents.py"}
        uncovered = []
        for path in (root / "server" / "routes").glob("*.py"):
            relative = f"server/routes/{path.name}"
            if relative in exempt or relative in self.EXPECTED:
                continue
            text = path.read_text()
            if "_llm_json" in text or "llm_text" in text or "_llm_raw" in text:
                uncovered.append(relative)
        self.assertEqual(uncovered, [],
                         f"these modules call the model but declare no rate limit: "
                         f"{uncovered}")


if __name__ == "__main__":
    unittest.main()
