"""The CI configuration itself.

A workflow is code that runs somewhere you are not watching. The failure mode
worth guarding is not "CI is red" — that is loud — but "CI is green and verified
nothing": a step invoking a script that was renamed, a Python version that no
longer matches the runtime, or a gate quietly dropped from the script the
workflow delegates to.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class TestWorkflowExists(unittest.TestCase):
    def test_workflow_is_present(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing {WORKFLOW}")

    def test_workflow_is_parseable_yaml(self):
        try:
            import yaml
        except ImportError:
            # PyYAML is not a runtime dependency; the structural assertions below
            # work on the text, so skipping here loses nothing important.
            self.skipTest("PyYAML not installed")
        parsed = yaml.safe_load(WORKFLOW.read_text())
        self.assertIn("jobs", parsed)
        self.assertTrue(parsed["jobs"], "workflow declares no jobs")


class TestWorkflowReferencesRealFiles(unittest.TestCase):
    """Every script the workflow runs must exist.

    A renamed script turns its step into an error, but a DELETED gate that is no
    longer referenced turns CI green while checking less — so both directions are
    asserted (see TestGatesAreNotSilentlyDropped).
    """

    def test_every_referenced_script_exists(self):
        text = WORKFLOW.read_text()
        referenced = set(re.findall(r"(?:python3?\s+)((?:scripts|tests)/[\w/]+\.py)",
                                    text))
        self.assertTrue(referenced, "no scripts referenced — is the workflow empty?")
        missing = [name for name in referenced if not (ROOT / name).is_file()]
        self.assertEqual(missing, [],
                         f"the workflow runs scripts that do not exist: {missing}")

    def test_referenced_unittest_modules_exist(self):
        text = WORKFLOW.read_text()
        for module in re.findall(r"unittest\s+(tests\.[\w.]+)", text):
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(path.is_file(),
                            f"workflow runs {module} but {path.name} does not exist")


class TestPythonVersionsMatchTheRuntime(unittest.TestCase):
    def test_workflow_tests_the_deployed_python_version(self):
        """Databricks Apps serves on the version in .python-version.

        If CI only tested another version, the gates would be passing against an
        interpreter the app never runs on.
        """
        deployed = (ROOT / ".python-version").read_text().strip()
        self.assertIn(f'"{deployed}"', WORKFLOW.read_text(),
                      f"CI does not test Python {deployed}, which is what the app "
                      "is deployed on")

    def test_workflow_tests_the_declared_floor(self):
        """pyproject claims requires-python >= X; CI must actually verify X."""
        pyproject = (ROOT / "pyproject.toml").read_text()
        match = re.search(r'requires-python\s*=\s*">=\s*([\d.]+)"', pyproject)
        self.assertIsNotNone(match, "pyproject declares no requires-python")
        floor = match.group(1)
        self.assertIn(f'"{floor}"', WORKFLOW.read_text(),
                      f"pyproject claims support for Python {floor} but CI never "
                      "runs on it — an unverified claim a customer may rely on")

    def test_ruff_target_matches_the_deployed_version(self):
        deployed = (ROOT / ".python-version").read_text().strip()
        expected = "py" + deployed.replace(".", "")
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f'target-version = "{expected}"', pyproject,
                      f"ruff should target {expected} to match .python-version")


class TestGatesAreNotSilentlyDropped(unittest.TestCase):
    """scripts/check.py is the single definition of "does this repo pass"."""

    def setUp(self):
        self.source = (ROOT / "scripts" / "check.py").read_text()

    def test_check_script_exists_and_is_referenced_by_ci(self):
        self.assertTrue((ROOT / "scripts" / "check.py").is_file())
        self.assertIn("scripts/check.py", WORKFLOW.read_text(),
                      "CI must delegate to check.py so the gates are identical "
                      "locally and in CI")

    def test_it_runs_the_unit_tests(self):
        self.assertIn("unittest", self.source)
        self.assertIn("discover", self.source)

    def test_it_lints(self):
        self.assertIn("ruff", self.source)

    def test_it_checks_the_console_bundle(self):
        # The console is hand-written JS with no build step, so a syntax error
        # ships and renders a blank page. This is the only gate before that.
        self.assertIn("--check", self.source)
        self.assertIn("console.js", self.source)

    def test_it_checks_customer_visibility_patch(self):
        self.assertIn("patch_spa_customer_visibility.py", self.source)

    def test_it_checks_for_secrets(self):
        self.assertIn("check_no_secrets", self.source)

    def test_it_verifies_the_app_imports_without_test_stubs(self):
        self.assertIn("import app", self.source)

    def test_a_missing_tool_is_reported_as_skip_not_pass(self):
        """A green summary must never mean "the tool wasn't installed"."""
        self.assertIn("SKIP", self.source)
        self.assertIn("skipped", self.source)

    def test_it_exits_nonzero_on_failure(self):
        self.assertRegex(self.source, r"return 1\b")


class TestTestsReadmeIsAccurate(unittest.TestCase):
    """The README's table must match the files on disk, both directions.

    It listed a `test_value_engine.py` that did not exist — so the module
    computing every dollar figure in the app appeared covered and was not. A
    documented-but-absent entry is worse than no documentation, because it stops
    anyone from looking.
    """

    def setUp(self):
        self.readme = (ROOT / "tests" / "README.md").read_text()
        self.actual = {path.name for path in (ROOT / "tests").glob("test_*.py")}
        self.documented = set(re.findall(r"`(test_\w+\.py)`", self.readme))

    def test_no_documented_test_file_is_missing(self):
        phantom = sorted(self.documented - self.actual)
        self.assertEqual(phantom, [],
                         f"tests/README.md documents files that do not exist: "
                         f"{phantom}")

    def test_every_test_file_is_documented(self):
        undocumented = sorted(self.actual - self.documented)
        self.assertEqual(undocumented, [],
                         f"these test files are not listed in tests/README.md, so "
                         f"nobody knows what they cover: {undocumented}")


class TestSecretScannerCoversKnownShapes(unittest.TestCase):
    """The scanner must catch the credential shapes this app actually handles."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_no_secrets
        self.scanner = check_no_secrets

    def _matches(self, text: str) -> list[str]:
        return [label for label, pattern, _ in self.scanner.PATTERNS
                if any(not self.scanner.looks_like_a_placeholder(m.group(0))
                       for m in pattern.finditer(text))]

    # Credential-shaped fixtures are ASSEMBLED at runtime rather than written as
    # literals. A test for a secret scanner naturally contains strings that trip
    # secret scanners, and the resolution must not be an allow-list entry someone
    # later deletes — nor can these use the placeholder markers the scanner
    # deliberately ignores, or they would prove nothing.
    @staticmethod
    def _fake_pat() -> str:
        import hashlib
        return "dapi" + hashlib.sha256(b"grid-atlas-fixture").hexdigest()[:32]

    @staticmethod
    def _fake_host() -> str:
        return "-".join(["ep", "quiet", "river", "48213756"]) + \
               ".database.us-west-2.cloud.databricks.com"

    @staticmethod
    def _fake_app_host() -> str:
        return "someapp-" + "7474656346204362" + ".aws.databricksapps.com"

    def test_detects_a_databricks_pat(self):
        self.assertIn("databricks PAT", self._matches(self._fake_pat()))

    def test_detects_a_lakebase_endpoint_host(self):
        self.assertIn("Lakebase endpoint host", self._matches(self._fake_host()))

    def test_detects_a_deployed_app_hostname(self):
        self.assertIn("deployed app hostname",
                      self._matches(self._fake_app_host()))

    def test_detects_a_private_key_block(self):
        header = "-" * 5 + "BEGIN RSA PRIVATE " + "KEY" + "-" * 5
        self.assertIn("private key block", self._matches(header))

    def test_allows_documentation_placeholders(self):
        """INSTALL.md must be able to show the SHAPE of a value.

        A scanner that flags its own documentation gets disabled, so this is a
        requirement rather than a nicety.
        """
        for placeholder in (
            "ep-xxxx.database.us-west-2.cloud.databricks.com",
            "https://your-app.aws.databricksapps.com",
            "dapiXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        ):
            self.assertEqual(self._matches(placeholder), [],
                             f"placeholder wrongly flagged: {placeholder}")

    def test_the_repo_itself_is_clean(self):
        """Runs the real scanner over the real tree — the assertion that counts."""
        self.assertEqual(self.scanner.main(), 0,
                         "a credential or workspace-specific value is committed")


if __name__ == "__main__":
    unittest.main()
