"""Optional-parameter negotiation in _llm_json.

Regression coverage for a bug that only surfaced against a live workspace:
`databricks-claude-sonnet-5` rejects `temperature` with a hard 400 rather than
ignoring it, so every agent silently degraded to heuristics on that endpoint. The
original code only retried when the error mentioned `response_format`.

Endpoints disagree about which optional parameters they accept and reject the
unsupported ones outright, so the retry must drop whichever parameter the error
actually names — and must NOT keep retrying on errors that have nothing to do with
parameters (auth, quota, an unparseable reply), which would just burn calls.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from fakedb import run  # noqa: E402
from server.routes import agents  # noqa: E402

GOOD_JSON = '{"use_cases": []}'


class FakeCompletions:
    """Records the kwargs of each call and replies per a scripted policy."""

    def __init__(self, policy):
        self.policy = policy
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.policy(kwargs)          # may raise to simulate a 400

        class Message:
            def __init__(self, c): self.content = c

        class Choice:
            def __init__(self, c): self.message = Message(c)

        class Response:
            def __init__(self, c): self.choices = [Choice(c)]

        return Response(content)


class FakeClient:
    def __init__(self, policy):
        self.chat = type("Chat", (), {"completions": FakeCompletions(policy)})()

    @property
    def calls(self):
        return self.chat.completions.calls


class NegotiationTestCase(unittest.TestCase):
    def setUp(self):
        import server.llm as llm_module
        self._real_get = llm_module.get_llm_client
        self._llm_module = llm_module

    def tearDown(self):
        self._llm_module.get_llm_client = self._real_get

    def drive(self, policy, **kwargs):
        client = FakeClient(policy)
        self._llm_module.get_llm_client = lambda: client
        result = run(agents._llm_json("prompt", **kwargs))
        return result, client.calls


class TestErrorClassifiers(unittest.TestCase):
    def test_detects_temperature_rejection(self):
        exc = Exception("BAD_REQUEST: Model us.anthropic.claude-sonnet-5 does not "
                        "support the temperature parameter.")
        self.assertTrue(agents._temperature_rejected(exc))
        self.assertFalse(agents._schema_rejected(exc))

    def test_detects_schema_rejection(self):
        for message in ("response_format is not supported",
                        "invalid json_schema in request"):
            exc = Exception(message)
            self.assertTrue(agents._schema_rejected(exc), message)
            self.assertFalse(agents._temperature_rejected(exc), message)

    def test_unrelated_error_matches_neither(self):
        exc = Exception("PERMISSION_DENIED: no CAN_QUERY on endpoint")
        self.assertFalse(agents._temperature_rejected(exc))
        self.assertFalse(agents._schema_rejected(exc))

    def test_none_is_safe(self):
        self.assertFalse(agents._temperature_rejected(None))
        self.assertFalse(agents._schema_rejected(None))


class TestNegotiation(NegotiationTestCase):
    def test_happy_path_uses_everything_in_one_call(self):
        (parsed, used_llm, note), calls = self.drive(
            lambda kw: GOOD_JSON, response_schema={"type": "json_schema"})
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)
        self.assertIsNone(note)
        self.assertEqual(len(calls), 1)
        self.assertIn("temperature", calls[0])
        self.assertIn("response_format", calls[0])

    def test_temperature_rejection_retries_without_it(self):
        """The live sonnet-5 failure. Must recover, keeping the schema."""
        def policy(kw):
            if "temperature" in kw:
                raise Exception("BAD_REQUEST: Model us.anthropic.claude-sonnet-5 "
                                "does not support the temperature parameter.")
            return GOOD_JSON

        (parsed, used_llm, _), calls = self.drive(
            policy, response_schema={"type": "json_schema"})
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm, "should have recovered, not fallen back")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("temperature", calls[1])
        # The schema is unrelated to the failure, so it must be retained.
        self.assertIn("response_format", calls[1])

    def test_schema_rejection_retries_without_it(self):
        def policy(kw):
            if "response_format" in kw:
                raise Exception("response_format is not supported by this endpoint")
            return GOOD_JSON

        (parsed, used_llm, _), calls = self.drive(
            policy, response_schema={"type": "json_schema"})
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)
        self.assertNotIn("response_format", calls[-1])
        self.assertIn("temperature", calls[-1])

    def test_both_rejected_falls_back_to_the_minimal_call(self):
        def policy(kw):
            if "temperature" in kw:
                raise Exception("does not support the temperature parameter")
            if "response_format" in kw:
                raise Exception("response_format is not supported")
            return GOOD_JSON

        (parsed, used_llm, _), calls = self.drive(
            policy, response_schema={"type": "json_schema"})
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)
        final = calls[-1]
        self.assertNotIn("temperature", final)
        self.assertNotIn("response_format", final)
        # Whatever every endpoint accepts must still be present.
        for required in ("model", "messages", "max_tokens"):
            self.assertIn(required, final)

    def test_unrelated_error_does_not_retry(self):
        """Auth/quota failures won't be fixed by dropping parameters, so retrying
        would just burn three more calls against a broken endpoint."""
        def policy(kw):
            raise Exception("PERMISSION_DENIED: no CAN_QUERY on endpoint")

        (parsed, used_llm, note), calls = self.drive(
            policy, response_schema={"type": "json_schema"})
        self.assertIsNone(parsed)
        self.assertFalse(used_llm)
        self.assertIn("heuristic", note)
        self.assertEqual(len(calls), 1, "must not retry an unrelated failure")

    def test_unparseable_reply_does_not_retry(self):
        (parsed, used_llm, note), calls = self.drive(lambda kw: "sorry, no JSON here")
        self.assertIsNone(parsed)
        self.assertFalse(used_llm)
        self.assertEqual(len(calls), 1)

    def test_never_exceeds_four_attempts(self):
        """A pathological endpoint must not cause unbounded retries."""
        def policy(kw):
            raise Exception("does not support the temperature parameter and "
                            "response_format is not supported")

        (parsed, used_llm, _), calls = self.drive(
            policy, response_schema={"type": "json_schema"})
        self.assertIsNone(parsed)
        self.assertFalse(used_llm)
        self.assertLessEqual(len(calls), 4)

    def test_no_schema_requested_still_negotiates_temperature(self):
        def policy(kw):
            if "temperature" in kw:
                raise Exception("does not support the temperature parameter")
            return GOOD_JSON

        (parsed, used_llm, _), calls = self.drive(policy)
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)
        self.assertNotIn("temperature", calls[-1])

    def test_code_fenced_reply_is_parsed(self):
        (parsed, used_llm, _), _ = self.drive(
            lambda kw: "```json\n{\"use_cases\": []}\n```")
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)

    def test_prose_wrapped_json_is_parsed(self):
        (parsed, _, _), _ = self.drive(
            lambda kw: 'Sure! Here you go: {"use_cases": []} Hope that helps.')
        self.assertEqual(parsed, {"use_cases": []})

    def test_empty_reply_falls_back(self):
        (parsed, used_llm, note), _ = self.drive(lambda kw: "")
        self.assertIsNone(parsed)
        self.assertFalse(used_llm)
        self.assertIsNotNone(note)


if __name__ == "__main__":
    unittest.main()


class TestContentBlocks(NegotiationTestCase):
    """Regression: sonnet-5 can return `content` as a LIST of content blocks.

    `.strip()` on that raised "'list' object has no attribute 'strip'", so the
    agents fell back to heuristics even though the model had answered correctly.
    Found only by calling the live endpoint.
    """

    def test_string_content_still_works(self):
        (parsed, used_llm, _), _ = self.drive(lambda kw: GOOD_JSON)
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)

    def test_list_of_dict_blocks(self):
        (parsed, used_llm, _), _ = self.drive(
            lambda kw: [{"type": "text", "text": GOOD_JSON}])
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)

    def test_reply_split_across_blocks(self):
        (parsed, used_llm, _), _ = self.drive(lambda kw: [
            {"type": "text", "text": '{"use_cases":'},
            {"type": "text", "text": " []}"},
        ])
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)

    def test_list_of_plain_strings(self):
        (parsed, _, _), _ = self.drive(lambda kw: [GOOD_JSON])
        self.assertEqual(parsed, {"use_cases": []})

    def test_block_objects_with_a_text_attribute(self):
        class Block:
            def __init__(self, text): self.text = text

        (parsed, _, _), _ = self.drive(lambda kw: [Block(GOOD_JSON)])
        self.assertEqual(parsed, {"use_cases": []})

    def test_none_and_empty_list_fall_back_cleanly(self):
        for reply in (None, [], [{"type": "text", "text": ""}]):
            (parsed, used_llm, note), _ = self.drive(lambda kw, r=reply: r)
            self.assertIsNone(parsed, repr(reply))
            self.assertFalse(used_llm, repr(reply))
            self.assertIsNotNone(note)


class TestNoUnnegotiatedCallSites(unittest.TestCase):
    def test_agents_module_has_one_llm_call_site(self):
        """Every LLM call must route through _llm_json. A second hand-rolled
        `chat.completions.create` is how the sonnet-5 temperature rejection kept
        breaking detect-dependencies after the shared helper was already fixed."""
        import inspect

        source = inspect.getsource(agents)
        self.assertEqual(source.count("chat.completions.create"), 1,
                         "found an LLM call outside _llm_json; route it through "
                         "_llm_json so it inherits parameter negotiation")

    def test_other_routers_do_not_call_the_client_directly(self):
        import inspect

        from server.routes import generate, taxonomy
        from server import normalization  # noqa: F401 - pure logic, no LLM calls

        for module in (generate, taxonomy):
            self.assertEqual(
                inspect.getsource(module).count("chat.completions.create"), 0,
                f"{module.__name__} calls the LLM client directly")


class TestReasoningModels(NegotiationTestCase):
    """Regression: reasoning models spend output tokens BEFORE the answer.

    Live, `databricks-claude-sonnet-5` consumed a 1400-token budget entirely on a
    `reasoning` block and returned finish_reason=length with an empty answer, so
    every value estimate silently fell back to a heuristic. Two defences: skip
    reasoning blocks when flattening, and floor max_tokens.
    """

    def test_reasoning_blocks_are_skipped(self):
        """Including the reasoning trace would let the brace-scraper parse the
        model's thinking instead of its answer."""
        (parsed, used_llm, _), _ = self.drive(lambda kw: [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "{\"junk\": 1}"}]},
            {"type": "text", "text": GOOD_JSON},
        ])
        self.assertEqual(parsed, {"use_cases": []})
        self.assertTrue(used_llm)

    def test_reasoning_with_a_text_field_still_skipped(self):
        (parsed, _, _), _ = self.drive(lambda kw: [
            {"type": "reasoning", "text": '{"wrong": true}'},
            {"type": "text", "text": GOOD_JSON},
        ])
        self.assertEqual(parsed, {"use_cases": []})

    def test_thinking_blocks_are_skipped_too(self):
        (parsed, _, _), _ = self.drive(lambda kw: [
            {"type": "thinking", "text": '{"wrong": true}'},
            {"type": "text", "text": GOOD_JSON},
        ])
        self.assertEqual(parsed, {"use_cases": []})

    def test_reasoning_only_reply_falls_back(self):
        """The observed failure: budget exhausted on reasoning, no answer."""
        (parsed, used_llm, note), _ = self.drive(lambda kw: [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": ""}]},
        ])
        self.assertIsNone(parsed)
        self.assertFalse(used_llm)
        self.assertIsNotNone(note)

    def test_max_tokens_is_floored(self):
        """A caller asking for a small budget must still get room for an answer."""
        (_parsed, _used, _note), calls = self.drive(lambda kw: GOOD_JSON, max_tokens=1400)
        self.assertGreaterEqual(calls[0]["max_tokens"], agents.MIN_OUTPUT_TOKENS)

    def test_larger_request_is_not_reduced(self):
        (_p, _u, _n), calls = self.drive(lambda kw: GOOD_JSON, max_tokens=20000)
        self.assertEqual(calls[0]["max_tokens"], 20000)

    def test_block_objects_with_a_reasoning_type_are_skipped(self):
        class Block:
            def __init__(self, type_, text):
                self.type = type_
                self.text = text

        (parsed, _, _), _ = self.drive(lambda kw: [
            Block("reasoning", '{"wrong": true}'),
            Block("text", GOOD_JSON),
        ])
        self.assertEqual(parsed, {"use_cases": []})
