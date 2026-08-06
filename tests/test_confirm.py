"""The propose/confirm token gate.

This is the security boundary for agent-initiated writes, so the tests focus on
the properties that make it one:

  - The payload the executor sees comes from the SERVER, never from the confirming
    request. A client that can mutate the page must not be able to substitute
    different values behind an approval the user already gave.
  - A token is single-use, so a replayed confirm (double click, retry, back
    button) is a no-op instead of a duplicate write.
  - Expiry is enforced at consume time, and the three failure modes (unknown,
    already used, expired) are distinguishable so the UI can say something true.

The atomic claim itself is one UPDATE ... WHERE consumed_at IS NULL RETURNING,
which Postgres serializes; FakeDB can't reproduce that, so `test_claim_is_atomic`
asserts the SQL shape instead of racing it.
"""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from fakedb import Row, run  # noqa: E402
from server import confirm as cf  # noqa: E402


class RecordingDB:
    """Captures writes and replays canned reads, so a token's stored payload can
    be asserted independently of what a caller later sends."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.executed: list[tuple] = []
        self.claim_sql: str | None = None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO confirm_tokens" in sql:
            token, intent, payload, before, after, summary, actor, expires = args
            self.rows[token] = {
                "token": token, "intent": intent, "payload_json": payload,
                "before_json": before, "after_json": after, "summary": summary,
                "actor": actor, "expires_at": expires,
                "consumed_at": None, "consumed_by": None,
            }
        return "OK"

    async def fetch(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        return [row] if row else []

    async def fetchrow(self, sql, *args):
        if "UPDATE confirm_tokens" in sql and "consumed_at IS NULL" in sql:
            self.claim_sql = sql
            token, actor = args
            row = self.rows.get(token)
            if row is None or row["consumed_at"] is not None:
                return None
            if row["expires_at"] <= datetime.now(timezone.utc):
                return None
            row["consumed_at"] = datetime.now(timezone.utc)
            row["consumed_by"] = actor
            return Row(intent=row["intent"], payload_json=row["payload_json"])
        if "SELECT consumed_at, expires_at" in sql:
            row = self.rows.get(args[0])
            return Row(consumed_at=row["consumed_at"],
                       expires_at=row["expires_at"]) if row else None
        if "SELECT token, intent, summary" in sql:
            row = self.rows.get(args[0])
            if row is None:
                return None
            return Row(
                token=row["token"], intent=row["intent"], summary=row["summary"],
                before_json=row["before_json"], after_json=row["after_json"],
                expires_at=row["expires_at"], consumed_at=row["consumed_at"],
                expired=row["expires_at"] <= datetime.now(timezone.utc))
        return None


class ConfirmTestCase(unittest.TestCase):
    def setUp(self):
        self._real_db = cf.db
        self.fake = RecordingDB()
        cf.db = self.fake

    def tearDown(self):
        cf.db = self._real_db

    def issue(self, payload=None, intent=cf.INTENT_CREATE_USE_CASES, **kwargs):
        return run(cf.issue_token(
            intent, payload if payload is not None else {"candidates": []},
            actor="alice@example.com", **kwargs))


class TestIssue(ConfirmTestCase):
    def test_returns_a_card(self):
        card = self.issue(before={"n": 1}, after={"n": 3}, summary="Create 2 use cases")
        self.assertIn("token", card)
        self.assertEqual(card["intent"], cf.INTENT_CREATE_USE_CASES)
        self.assertEqual(card["summary"], "Create 2 use cases")
        self.assertEqual(card["before"], {"n": 1})
        self.assertEqual(card["after"], {"n": 3})

    def test_tokens_are_unguessable_and_unique(self):
        tokens = {self.issue()["token"] for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        for token in tokens:
            # 32 bytes base64url-encoded; anything short would be brute-forceable.
            self.assertGreaterEqual(len(token), 40)

    def test_unknown_intent_rejected(self):
        with self.assertRaises(cf.ConfirmError):
            run(cf.issue_token("deleteEverything", {}, actor="a"))

    def test_expiry_is_ten_minutes_out(self):
        card = self.issue()
        expires = datetime.fromisoformat(card["expires_at"])
        delta = expires - datetime.now(timezone.utc)
        self.assertTrue(timedelta(minutes=9) < delta <= cf.TOKEN_TTL)

    def test_payload_persisted_server_side(self):
        """The whole point: the payload is in the store, not in the client's hands."""
        card = self.issue({"candidates": [{"title": "Real Use Case"}]})
        stored = json.loads(self.fake.rows[card["token"]]["payload_json"])
        self.assertEqual(stored["candidates"][0]["title"], "Real Use Case")

    def test_payload_not_echoed_in_the_card(self):
        """The card carries only display halves, so nothing invites the client to
        round-trip the payload back."""
        card = self.issue({"candidates": [{"title": "Secret"}]},
                          before={}, after={"n": 1})
        self.assertNotIn("payload", card)
        self.assertNotIn("Secret", json.dumps(card))


class TestConsume(ConfirmTestCase):
    def test_returns_the_stored_payload(self):
        payload = {"candidates": [{"title": "A"}], "lob_id": 3}
        card = self.issue(payload)
        intent, got = run(cf.consume_token(card["token"], "bob@example.com"))
        self.assertEqual(intent, cf.INTENT_CREATE_USE_CASES)
        self.assertEqual(got, payload)

    def test_single_use(self):
        card = self.issue()
        run(cf.consume_token(card["token"], "bob"))
        with self.assertRaises(cf.ConfirmError) as caught:
            run(cf.consume_token(card["token"], "bob"))
        self.assertIn("already applied", caught.exception.reason)

    def test_unknown_token_is_distinguishable(self):
        with self.assertRaises(cf.ConfirmError) as caught:
            run(cf.consume_token("not-a-real-token", "bob"))
        self.assertIn("not recognized", caught.exception.reason)

    def test_expired_token_rejected_and_distinguishable(self):
        card = self.issue()
        self.fake.rows[card["token"]]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1))
        with self.assertRaises(cf.ConfirmError) as caught:
            run(cf.consume_token(card["token"], "bob"))
        self.assertIn("expired", caught.exception.reason)

    def test_records_who_confirmed(self):
        card = self.issue()
        run(cf.consume_token(card["token"], "carol@example.com"))
        self.assertEqual(self.fake.rows[card["token"]]["consumed_by"], "carol@example.com")

    def test_confirming_user_may_differ_from_proposer(self):
        """Four-eyes review is a legitimate flow, so this must not be blocked."""
        card = self.issue()
        intent, _ = run(cf.consume_token(card["token"], "someone-else@example.com"))
        self.assertEqual(intent, cf.INTENT_CREATE_USE_CASES)

    def test_claim_is_atomic(self):
        """Check-then-update would let two concurrent confirms both write. The
        claim must be a single guarded UPDATE ... RETURNING."""
        card = self.issue()
        run(cf.consume_token(card["token"], "bob"))
        sql = " ".join((self.fake.claim_sql or "").split())
        self.assertIn("UPDATE confirm_tokens", sql)
        self.assertIn("consumed_at IS NULL", sql)
        self.assertIn("expires_at > now()", sql)
        self.assertIn("RETURNING", sql)

    def test_parses_payload_when_driver_returns_text(self):
        """asyncpg hands back jsonb as str without a codec registered."""
        card = self.issue({"a": 1})
        self.fake.rows[card["token"]]["payload_json"] = '{"a": 1}'
        _, payload = run(cf.consume_token(card["token"], "bob"))
        self.assertEqual(payload, {"a": 1})

    def test_concurrent_confirms_yield_exactly_one_winner(self):
        """Even against the fake's sequential claim, only one caller may win."""
        card = self.issue()

        async def scenario():
            async def attempt():
                try:
                    await cf.consume_token(card["token"], "bob")
                    return True
                except cf.ConfirmError:
                    return False
            return await asyncio.gather(*[attempt() for _ in range(5)])

        self.assertEqual(sum(run(scenario())), 1)


class TestPeek(ConfirmTestCase):
    def test_does_not_consume(self):
        card = self.issue(summary="Create 1 use case")
        peeked = run(cf.peek_token(card["token"]))
        self.assertEqual(peeked["summary"], "Create 1 use case")
        self.assertIsNone(peeked["consumed_at"])
        # Still usable afterwards.
        run(cf.consume_token(card["token"], "bob"))

    def test_reports_consumed_and_expired_state(self):
        card = self.issue()
        run(cf.consume_token(card["token"], "bob"))
        peeked = run(cf.peek_token(card["token"]))
        self.assertIsNotNone(peeked["consumed_at"])

    def test_unknown_token_returns_none(self):
        self.assertIsNone(run(cf.peek_token("nope")))

    def test_parses_display_halves_from_text(self):
        card = self.issue(before={"n": 1}, after={"n": 2})
        self.fake.rows[card["token"]]["before_json"] = '{"n": 1}'
        peeked = run(cf.peek_token(card["token"]))
        self.assertEqual(peeked["before_json"], {"n": 1})

    def test_malformed_display_json_degrades(self):
        card = self.issue()
        self.fake.rows[card["token"]]["before_json"] = "not json"
        self.assertEqual(run(cf.peek_token(card["token"]))["before_json"], {})


class TestIntentRegistry(unittest.TestCase):
    def test_every_intent_constant_is_declared_valid(self):
        for name in dir(cf):
            if name.startswith("INTENT_"):
                self.assertIn(getattr(cf, name), cf.VALID_INTENTS, name)

    def test_create_use_cases_has_an_executor(self):
        """An intent with no executor would pass the gate then 500."""
        from server.routes import generate
        self.assertIn(cf.INTENT_CREATE_USE_CASES, generate._EXECUTORS)

    def test_every_registered_executor_is_a_valid_intent(self):
        from server.routes import generate
        for intent in generate._EXECUTORS:
            self.assertIn(intent, cf.VALID_INTENTS)


if __name__ == "__main__":
    unittest.main()
