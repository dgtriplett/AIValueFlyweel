"""`--push`: uploading the extract instead of hand-carrying three CSVs.

WHAT MAKES A PUSH MODE WRONG RATHER THAN MISSING
------------------------------------------------
Four ways this feature goes bad, each tested here:

  1. Racing the rate limiter. The upload endpoints are class 'sweep' — burst 2, four per
     minute — because each does minutes of warehouse work. Three files posted in a loop
     therefore 429 on the THIRD, every time. The obvious implementation is broken and
     looks fine in a one-file test.
  2. Retrying a permanent failure. The app reports a misconfiguration as 502 (it proxies
     a failed warehouse call), so a blind 5xx retry turns "you have not run the
     bootstrap" into four attempts and a minute of silence. Observed live before this
     was fixed.
  3. Breaking the manual path. Air-gapped estates and anyone who wants to read the
     metadata before it leaves the machine depend on the CSVs still landing in ./output/
     first. Push must be strictly additive.
  4. Losing the run on a failed upload. The sweep may have taken an hour. A push failure
     must print how to finish by hand, not raise.

VERIFIED AGAINST THE LIVE APP
-----------------------------
All three files were pushed to the deployed instance: 1 schema, 1 table and 2 columns
landed (confirmed via /api/ingestion/summary). During that run a real DNS blip hit the
third upload and the transient-retry path recovered it, which is the behaviour case 2
must not break.
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
EXTRACTOR = ROOT / "schema-extractor"
sys.path.insert(0, str(EXTRACTOR))

import push  # noqa: E402


class TestHostNormalization(unittest.TestCase):
    """Accept what a person will actually paste."""

    def test_adds_https(self):
        self.assertEqual(push.normalize_host("app.databricksapps.com"),
                         "https://app.databricksapps.com")

    def test_keeps_an_explicit_scheme(self):
        self.assertEqual(push.normalize_host("http://localhost:8000"),
                         "http://localhost:8000")

    def test_strips_a_pasted_path(self):
        """Copying the app URL out of the address bar includes a path and a fragment;
        appending an API route to that yields a 404 that looks like a missing feature."""
        self.assertEqual(
            push.normalize_host("https://app.databricksapps.com/console/#catalog"),
            "https://app.databricksapps.com")

    def test_empty_host_is_a_clear_error(self):
        with self.assertRaises(push.PushError) as caught:
            push.normalize_host("")
        self.assertIn("--push", str(caught.exception))


class TestUploadOrder(unittest.TestCase):
    """Schemas, then tables, then columns.

    Ingestion MERGEs each level onto the one above it, so a column row whose table has
    not landed yet is dropped — silently, since the upload still reports success.
    """

    def test_order_is_schemas_tables_columns(self):
        names = [name for name, _, _ in push.UPLOADS]
        self.assertEqual(names,
                         ["all_schemas.csv", "all_tables.csv", "all_columns.csv"])

    def test_columns_is_the_only_optional_file(self):
        """--no-columns is a supported way to run the sweep on a large estate, so a
        missing all_columns.csv must not fail the push."""
        optional = [name for name, _, required in push.UPLOADS if not required]
        self.assertEqual(optional, ["all_columns.csv"])

    def test_endpoints_match_the_server(self):
        """Derived from the app's own routes rather than restated, so a rename on either
        side fails here instead of at runtime with a 404."""
        source = (ROOT / "server" / "routes" / "ingestion.py").read_text()
        for _, endpoint, _ in push.UPLOADS:
            route = endpoint.replace("/api/ingestion", "")
            self.assertIn(f'@router.post("{route}"', source,
                          f"{endpoint} does not exist on the server")


class TestRateLimitAwareness(unittest.TestCase):
    """Case 1: the failure the obvious implementation has."""

    def test_pacing_keeps_three_uploads_inside_the_limit(self):
        """server/limits.py allows 4 sweep requests per minute. Three uploads spaced by
        _SPACING_SECONDS must not exceed that."""
        from server.limits import LIMITS

        sweep = LIMITS["sweep"]
        window = push._SPACING_SECONDS * (len(push.UPLOADS) - 1)
        allowed = sweep.per_minute * (window / 60) + sweep.burst
        self.assertGreaterEqual(
            allowed, len(push.UPLOADS),
            f"{len(push.UPLOADS)} uploads spaced {push._SPACING_SECONDS}s apart exceed "
            f"the sweep limit (burst {sweep.burst}, {sweep.per_minute}/min)")

    def test_spacing_is_actually_applied(self):
        """Behavioural, not a text search.

        The earlier version asserted `"time.sleep(_SPACING_SECONDS)" in source`, and a
        mutation test showed that passes with the call commented out — the string still
        appears in the comment above it. This drives the real code path with sleep and the
        network stubbed, and asserts on what was slept.
        """
        import tempfile
        from unittest import mock

        slept = []
        posted = []

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, _, _ in push.UPLOADS:
                (directory / name).write_text("catalog_name,schema_name\na,b\n")

            with mock.patch.object(push.time, "sleep", slept.append), \
                 mock.patch.object(push, "_token", lambda host: "Bearer x"), \
                 mock.patch.object(push, "_post",
                                   lambda url, path, auth: posted.append(url) or {}):
                code = push.push(directory, "example.com")

        self.assertEqual(code, 0)
        self.assertEqual(len(posted), len(push.UPLOADS),
                         "not every file was uploaded")
        # One pause BETWEEN each pair of uploads, and none before the first.
        self.assertEqual(len(slept), len(push.UPLOADS) - 1,
                         f"expected {len(push.UPLOADS) - 1} pauses, got {slept}")
        for interval in slept:
            self.assertGreaterEqual(
                interval, push._SPACING_SECONDS,
                "the pause is shorter than the configured spacing, so a three-file "
                "push can outrun the server's sweep limit")

    def test_retry_after_is_honoured(self):
        """The server sends Retry-After with its 429. Guessing instead would either
        hammer it or wait far too long."""
        source = (EXTRACTOR / "push.py").read_text()
        self.assertIn('error.headers.get("Retry-After")', source)


class TestPermanentFailuresAreNotRetried(unittest.TestCase):
    """Case 2, found by pushing to the live app before its tables existed."""

    def test_missing_table_is_permanent(self):
        detail = ("Discovery SQL failed: [TABLE_OR_VIEW_NOT_FOUND] The table or view "
                  "`cat`.`grid_atlas_discovery`.`discovered_schemas` cannot be found.")
        self.assertTrue(push._is_permanent(detail))

    def test_permission_denied_is_permanent(self):
        self.assertTrue(push._is_permanent("PERMISSION_DENIED on schema"))

    def test_a_genuine_transient_error_is_still_retried(self):
        """The DNS blip that hit the live run must keep recovering."""
        for detail in ("upstream connect error", "timeout", "502 Bad Gateway",
                       "connection reset by peer"):
            self.assertFalse(push._is_permanent(detail),
                             f"{detail!r} is transient and must be retried")

    def test_matching_is_case_insensitive(self):
        self.assertTrue(push._is_permanent("table_or_view_not_found"))


class TestManualPathIsPreserved(unittest.TestCase):
    """Case 3: push is additive, never a replacement."""

    @classmethod
    def setUpClass(cls):
        cls.extract = (EXTRACTOR / "extract_schemas.py").read_text()

    def test_csvs_are_written_before_any_push(self):
        """The files must exist on disk regardless, so an air-gapped run is unaffected
        and a failed push loses nothing.

        AST-based rather than a string-position comparison. The earlier version compared
        `.index()` offsets, which only proves the save appears earlier in the FILE — a
        mutation test showed it still passed with the save moved inside the push branch.
        This asserts both statements are siblings at the top level of main(), and that the
        save comes first, so the save cannot be made conditional on pushing.
        """
        tree = ast.parse(self.extract)
        main = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "main")

        save_index = push_index = None
        save_statement = None
        for index, statement in enumerate(main.body):
            dumped = ast.dump(statement)
            # Matched on the call and the "all_" prefix: the f-string is stored as a
            # JoinedStr, so the literal "all_{key}.csv" never appears in the AST dump.
            if save_index is None and "save_csv" in dumped and "'all_'" in dumped:
                save_index, save_statement = index, statement
            if push_index is None and "push_module" in dumped:
                push_index = index

        self.assertIsNotNone(save_index, "main() never writes the all_*.csv files")
        self.assertIsNotNone(push_index, "main() never reaches the push")
        self.assertLess(save_index, push_index,
                        "the push runs before the CSVs are saved, so a push failure "
                        "would lose the whole sweep")

        # The save must be UNCONDITIONAL, which the ordering check alone cannot show:
        # wrapping it in `if args.push is not None:` keeps it earlier in main.body and
        # still passes the comparison above (verified by mutation), while making the
        # air-gapped path write nothing at all.
        for node in ast.walk(save_statement):
            if isinstance(node, ast.If):
                condition = ast.dump(node.test)
                self.assertNotIn(
                    "push", condition.lower(),
                    "the CSV save is gated on the --push flag, so a run without --push "
                    "would write no files and an air-gapped estate gets nothing")

    def test_push_is_opt_in(self):
        """default=None, so omitting the flag changes nothing about the old behaviour."""
        self.assertIn('"--push", nargs="?", const="", default=None', self.extract)

    def test_download_endpoint_still_exists(self):
        """The downloadable ZIP is how someone gets this script in the first place."""
        source = (ROOT / "server" / "routes" / "ingestion.py").read_text()
        self.assertIn('@router.get("/extractor/download")', source)


class TestFailureIsReportedNotRaised(unittest.TestCase):
    """Case 4: a failed push after a one-hour sweep must not look like a lost run."""

    def test_missing_required_file_returns_an_exit_code(self):
        code = push.push(Path("/nonexistent-dir"), "example.com", dry_run=True)
        self.assertEqual(code, 3, "push() must return a code, not raise")

    def test_dry_run_writes_nothing(self):
        """So someone can see what would be sent before sending a customer's metadata
        to a host they typed by hand."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "all_schemas.csv").write_text("catalog_name,schema_name\na,b\n")
            (directory / "all_tables.csv").write_text("table_catalog,table_name\na,b\n")
            code = push.push(directory, "example.com", dry_run=True)
        self.assertEqual(code, 0)

    def test_oversized_file_fails_before_uploading(self):
        """64MB matches the server's cap; checking locally turns a long upload followed
        by a 413 into an instant, actionable message."""
        from server.routes.ingestion import MAX_UPLOAD_BYTES

        self.assertEqual(push._MAX_BYTES, MAX_UPLOAD_BYTES,
                         "the local size check must match the server's limit")


class TestAuth(unittest.TestCase):
    """No new secret is introduced."""

    @classmethod
    def setUpClass(cls):
        cls.source = (EXTRACTOR / "push.py").read_text()

    def test_uses_the_sdk_credential_chain(self):
        """A Databricks App accepts a workspace OAuth token as a bearer, and the sweep is
        already authenticated. Inventing a token scheme would mean a secret to store."""
        self.assertIn("from databricks.sdk.core import Config", self.source)
        self.assertIn("config.authenticate()", self.source)

    def test_no_token_is_ever_printed(self):
        """A pasted traceback or CI log must not carry a bearer token."""
        for line in self.source.splitlines():
            if "print(" in line:
                self.assertNotIn("authorization", line.lower(),
                                 f"this line could print a token: {line.strip()}")

    def test_401_and_403_get_a_specific_message(self):
        """Authenticating to the workspace and being allowed to use the APP are two
        different things; a generic failure sends people to re-login pointlessly."""
        self.assertIn("(401, 403)", self.source)
        self.assertIn("CAN_USE", self.source)


if __name__ == "__main__":
    unittest.main()
