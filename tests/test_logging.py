"""Structured logging: JSON shape, request correlation, and secret redaction.

Redaction is the test that matters most here. Log output is the easiest place to
leak an OAuth token — they arrive in exception strings from the Lakebase and
serving-endpoint clients, get logged by a generic `except` that nobody wrote with
secrets in mind, and then sit in a log aggregator that a wider audience can read
than the app itself. So the redaction cases below are drawn from the token shapes
this app actually handles.
"""
import base64
import json
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import logging_setup  # noqa: E402


class CaptureHandler(logging.Handler):
    """Collects formatted output so tests assert on what would be written."""

    def __init__(self, formatter):
        super().__init__()
        self.setFormatter(formatter)
        self.addFilter(logging_setup.RequestIdFilter())
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


class LoggingTestCase(unittest.TestCase):
    """stubs.py disables logging globally; these tests need it back on."""

    def setUp(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)


class TestRedaction(LoggingTestCase):
    def test_bearer_token(self):
        out = logging_setup.redact(
            "auth failed: Authorization: Bearer dapi0123456789abcdef0123456789")
        self.assertIn("<redacted>", out)
        self.assertNotIn("dapi0123456789abcdef", out)

    def test_jwt(self):
        """Lakebase OAuth credentials are JWTs, and asyncpg's InvalidPasswordError
        carries them verbatim — the single likeliest way this app leaks one.

        Built from parts at runtime rather than written as a literal: a JWT-shaped
        string in a tracked file trips secret scanners, and a fixture that has to be
        allow-listed is a fixture someone eventually deletes.
        """
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(b'{"sub":"1234567890"}').decode().rstrip("=")
        signature = "S" + "x" * 42
        jwt = f"{header}.{payload}.{signature}"
        self.assertTrue(jwt.startswith("eyJ"), "fixture must look like a real JWT")

        out = logging_setup.redact(f"password authentication failed (token {jwt})")
        self.assertIn("<redacted-jwt>", out)
        self.assertNotIn(signature, out)

    def test_password_in_dsn(self):
        out = logging_setup.redact(
            "could not connect: host=x user=y password=s3cr3t-token-value sslmode=require")
        self.assertIn("password=<redacted>", out)
        self.assertNotIn("s3cr3t-token-value", out)
        # The non-secret parts stay, or the line stops being useful for debugging.
        self.assertIn("sslmode=require", out)

    def test_token_key_in_json(self):
        out = logging_setup.redact('{"token": "abcdefghijklmnopqrstuvwxyz012345"}')
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", out)

    def test_leaves_ordinary_text_alone(self):
        # Over-redaction is its own failure: it hides the diagnosis.
        message = "applied migration 003_discovery.sql in 412ms"
        self.assertEqual(logging_setup.redact(message), message)

    def test_short_values_are_not_mangled(self):
        # A 20-char floor keeps things like `token=abc` (a test fixture, not a
        # secret) readable while still catching real credentials.
        self.assertEqual(logging_setup.redact("token=abc"), "token=abc")

    def test_applies_to_json_formatted_output(self):
        """Redaction must run in the formatter, not depend on call sites."""
        handler = CaptureHandler(logging_setup.JsonFormatter())
        logger = logging.getLogger("test.redact.json")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False   # keep test output out of the suite's stdout

        logger.info("connect failed: password=supersecrettokenvalue123")
        payload = json.loads(handler.lines[0])
        self.assertNotIn("supersecrettokenvalue123", payload["msg"])

    def test_applies_to_exception_tracebacks(self):
        """The likeliest leak: a token inside an exception nobody inspected."""
        handler = CaptureHandler(logging_setup.JsonFormatter())
        logger = logging.getLogger("test.redact.exc")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False   # a captured traceback is not suite output

        try:
            raise RuntimeError("auth: Bearer dapiSECRET0123456789abcdefXYZ")
        except RuntimeError:
            logger.exception("db connect failed")

        payload = json.loads(handler.lines[0])
        self.assertNotIn("dapiSECRET0123456789abcdefXYZ", json.dumps(payload))
        self.assertIn("error", payload)


class TestJsonFormatter(LoggingTestCase):
    def _emit(self, level=logging.INFO, message="hello", args=()):
        handler = CaptureHandler(logging_setup.JsonFormatter())
        logger = logging.getLogger("test.json.shape")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.log(level, message, *args)
        return json.loads(handler.lines[0])

    def test_is_one_valid_json_object_per_line(self):
        payload = self._emit()
        self.assertEqual(payload["msg"], "hello")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "test.json.shape")

    def test_includes_required_fields(self):
        payload = self._emit()
        for field in ("ts", "level", "logger", "msg", "request_id"):
            self.assertIn(field, payload)

    def test_timestamp_is_iso_utc(self):
        payload = self._emit()
        self.assertTrue(payload["ts"].endswith("Z"), payload["ts"])
        self.assertIn("T", payload["ts"])

    def test_interpolates_lazy_args(self):
        # %-style args are the correct idiom (no formatting cost when filtered);
        # verify they actually render.
        payload = self._emit(message="applied %s in %dms", args=("001_init.sql", 42))
        self.assertEqual(payload["msg"], "applied 001_init.sql in 42ms")

    def test_survives_unserializable_values(self):
        # A logging call must never raise; default=str covers odd objects.
        payload = self._emit(message="object %s", args=(object(),))
        self.assertIn("object", payload["msg"])

    def test_no_request_id_outside_a_request(self):
        self.assertEqual(self._emit()["request_id"], "-")


class TestRequestIdPropagation(LoggingTestCase):
    def test_id_lands_on_records_while_set(self):
        handler = CaptureHandler(logging_setup.JsonFormatter())
        logger = logging.getLogger("test.reqid")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        token = logging_setup.request_id_var.set("abc123def456")
        try:
            logger.info("during")
        finally:
            logging_setup.request_id_var.reset(token)
        logger.info("after")

        self.assertEqual(json.loads(handler.lines[0])["request_id"], "abc123def456")
        self.assertEqual(json.loads(handler.lines[1])["request_id"], "-")

    def test_concurrent_requests_do_not_share_an_id(self):
        """The property a plain module global would silently get wrong.

        Two interleaved coroutines must each see their own id. If this fails, every
        log line under load is attributed to whichever request set the value last —
        which is worse than no id at all, because it is confidently wrong.
        """
        import asyncio

        handler = CaptureHandler(logging_setup.JsonFormatter())
        logger = logging.getLogger("test.reqid.concurrent")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        async def request(request_id: str, delay: float):
            token = logging_setup.request_id_var.set(request_id)
            try:
                await asyncio.sleep(delay)   # forces interleaving
                logger.info("work %s", request_id)
            finally:
                logging_setup.request_id_var.reset(token)

        async def both():
            await asyncio.gather(request("aaaa", 0.02), request("bbbb", 0.01))

        asyncio.run(both())

        seen = {json.loads(line)["msg"].split()[-1]:
                json.loads(line)["request_id"] for line in handler.lines}
        self.assertEqual(seen, {"aaaa": "aaaa", "bbbb": "bbbb"})

    def test_generated_ids_are_distinct_and_short(self):
        ids = {logging_setup.new_request_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        self.assertTrue(all(len(i) == 16 for i in ids))


class TestTextFormatter(LoggingTestCase):
    def test_readable_and_redacted(self):
        handler = CaptureHandler(logging_setup.TextFormatter())
        logger = logging.getLogger("test.text")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        token = logging_setup.request_id_var.set("deadbeefcafe0000")
        try:
            logger.warning("token=abcdefghijklmnopqrstuvwxyz0123")
        finally:
            logging_setup.request_id_var.reset(token)

        line = handler.lines[0]
        self.assertIn("WARN", line)
        self.assertIn("[deadbeef]", line)   # truncated id, enough to correlate
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", line)


class TestConfigure(LoggingTestCase):
    def setUp(self):
        super().setUp()
        root = logging.getLogger()
        saved_handlers, saved_level = list(root.handlers), root.level
        self.addCleanup(setattr, root, "level", saved_level)

        def restore():
            for handler in list(root.handlers):
                root.removeHandler(handler)
            for handler in saved_handlers:
                root.addHandler(handler)
        self.addCleanup(restore)

    def test_json_in_databricks_apps_text_locally(self):
        logging_setup.configure(json_output=True)
        self.assertIsInstance(logging.getLogger().handlers[0].formatter,
                              logging_setup.JsonFormatter)
        logging_setup.configure(json_output=False)
        self.assertIsInstance(logging.getLogger().handlers[0].formatter,
                              logging_setup.TextFormatter)

    def test_is_idempotent(self):
        """Called twice must not double every log line."""
        logging_setup.configure(json_output=True)
        logging_setup.configure(json_output=True)
        self.assertEqual(len(logging.getLogger().handlers), 1)

    def test_level_is_configurable(self):
        logging_setup.configure(level="DEBUG")
        self.assertEqual(logging.getLogger().level, logging.DEBUG)
        logging_setup.configure(level="warning")   # case-insensitive
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_env_var_sets_level(self):
        os.environ["LOG_LEVEL"] = "ERROR"
        self.addCleanup(os.environ.pop, "LOG_LEVEL", None)
        logging_setup.configure()
        self.assertEqual(logging.getLogger().level, logging.ERROR)

    def test_autodetects_databricks_apps(self):
        os.environ["DATABRICKS_APP_NAME"] = "grid-atlas"
        self.addCleanup(os.environ.pop, "DATABRICKS_APP_NAME", None)
        logging_setup.configure()
        self.assertIsInstance(logging.getLogger().handlers[0].formatter,
                              logging_setup.JsonFormatter)


class TestMiddleware(LoggingTestCase):
    """Drive the real ASGI app so the middleware is exercised as it ships.

    Starlette's TestClient needs httpx, which is not a runtime dependency of this
    app and so must not become a test-only one — CI would then be verifying a
    dependency set the app never runs with. Calling `app(scope, receive, send)`
    directly is the same code path with nothing stubbed.
    """

    def setUp(self):
        super().setUp()
        import app as app_module

        self.app = app_module.app
        self.handler = CaptureHandler(logging_setup.JsonFormatter())
        root = logging.getLogger()
        self.saved = list(root.handlers)
        root.handlers = [self.handler]
        root.setLevel(logging.INFO)
        self.addCleanup(setattr, root, "handlers", self.saved)

    def get(self, path, headers=()):
        import asyncio

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "scheme": "http",
            "server": ("test", 80), "client": ("1.2.3.4", 9999),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        }
        captured: dict = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["headers"] = {k.decode().lower(): v.decode()
                                       for k, v in message["headers"]}

        asyncio.run(self.app(scope, receive, send))
        return captured

    def lines(self):
        return [json.loads(line) for line in self.handler.lines]

    def test_response_carries_a_request_id(self):
        response = self.get("/api/domains/coverage-matrix")
        self.assertEqual(response["status"], 200)
        self.assertEqual(len(response["headers"]["x-request-id"]), 16)

    def test_upstream_request_id_is_honoured(self):
        # So a trace spans hops rather than restarting at this service.
        response = self.get("/api/domains/coverage-matrix",
                            [("x-request-id", "upstream-trace-1")])
        self.assertEqual(response["headers"]["x-request-id"], "upstream-trace-1")

    def test_logs_one_line_correlated_to_the_response(self):
        response = self.get("/api/domains/coverage-matrix")
        matching = [line for line in self.lines()
                    if line["request_id"] == response["headers"]["x-request-id"]]
        self.assertEqual(len(matching), 1)
        self.assertIn("GET /api/domains/coverage-matrix -> 200", matching[0]["msg"])

    def test_health_and_static_are_not_logged(self):
        """Otherwise the platform's health probe buries every real line."""
        self.get("/api/health")
        self.get("/assets/favicon.ico")
        self.assertEqual(
            [line["msg"] for line in self.lines()], [],
            "health checks and static assets must not produce request log lines")


class TestNoPrintStatements(LoggingTestCase):
    """print() has no level, no timestamp, and no request id — ban it in server code."""

    def test_server_code_uses_logging(self):
        import ast
        from pathlib import Path

        root = Path(__file__).parent.parent
        offenders = []
        # scripts/ is CLI output for a human at a terminal; print is correct there.
        targets = list((root / "server").rglob("*.py")) + [root / "app.py"]
        for path in targets:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "print"):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "these use print() instead of a module logger, so the output has no "
            f"level, timestamp, or request id: {offenders}")


if __name__ == "__main__":
    unittest.main()
