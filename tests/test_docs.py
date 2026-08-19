"""Documentation must not make claims the code contradicts.

Docs drift silently, and the failure is expensive in a specific way: a customer
follows an instruction that no longer works, or trusts a stated guarantee the code
stopped providing. This suite pins only the claims that are MECHANICALLY checkable
— referenced files exist, documented env vars are real, documented commands accept
the flags shown, and no doc describes the migration behaviour that was removed.

It deliberately does not check prose. The goal is to catch "this instruction is now
wrong", not to police wording.
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
DOCS = ["README.md", "ARCHITECTURE.md", "INSTALL.md", "OPERATIONS.md",
        "DEMO_MODE.md", "DELTA_SHARING.md"]


def doc_text(name: str) -> str:
    return (ROOT / name).read_text()


def all_docs_text() -> str:
    return "\n".join(doc_text(name) for name in DOCS
                     if (ROOT / name).is_file())


class TestDocsExist(unittest.TestCase):
    def test_every_indexed_doc_is_present(self):
        missing = [name for name in DOCS if not (ROOT / name).is_file()]
        self.assertEqual(missing, [], f"missing docs: {missing}")

    def test_readme_links_the_operations_doc(self):
        # The doc a customer needs when something goes wrong must be findable.
        self.assertIn("OPERATIONS.md", doc_text("README.md"))


class TestReferencedFilesExist(unittest.TestCase):
    """A markdown link to a moved or deleted file is a dead end."""

    # Matches markdown links to repo-relative paths, e.g. [`x`](server/db.py).
    LINK = re.compile(r"\]\((?!https?://|#)([^)#]+)")

    def test_all_relative_links_resolve(self):
        broken = []
        for name in DOCS:
            if not (ROOT / name).is_file():
                continue
            for target in self.LINK.findall(doc_text(name)):
                target = target.strip()
                if not target or target.startswith("mailto:"):
                    continue
                if not (ROOT / target).exists():
                    broken.append(f"{name} -> {target}")
        self.assertEqual(broken, [], f"broken relative links: {broken}")


class TestEnvVarsAreReal(unittest.TestCase):
    """Every env var a doc tells you to set must be one the code reads."""

    # Read by the code but intentionally undocumented: injected by the Databricks
    # Apps runtime rather than set by an operator, or read only to detect the
    # environment. Listing them explicitly means a NEW undocumented setting fails
    # the test below rather than blending in.
    RUNTIME_INJECTED = {
        "DATABRICKS_APP_NAME",      # set by the platform; presence = "in Apps"
        "DATABRICKS_HOST",          # injected
        "DATABRICKS_CONFIG_PROFILE",  # alias for DATABRICKS_PROFILE, local only
        "DATABRICKS_CLIENT_ID",     # injected SP credentials
        "DATABRICKS_CLIENT_SECRET",
    }

    def _env_vars_read_by_code(self) -> set[str]:
        text = "\n".join(path.read_text()
                         for path in (ROOT / "server").rglob("*.py"))
        text += (ROOT / "app.py").read_text()
        # os.environ.get("X"), os.environ["X"], os.getenv("X")
        return set(re.findall(
            r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]+)["']"""
            r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]""", text)
        ).union() - {""}

    def test_every_setting_the_code_reads_is_documented(self):
        """The direction that actually matters.

        A setting that changes behaviour but appears in no doc is one an operator
        cannot discover — they find it by reading source, or never. Checking the
        reverse direction (documented-but-unread) misses this entirely.
        """
        read = {name for pair in re.findall(
            r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]+)["']"""
            r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]""",
            "\n".join(path.read_text()
                      for path in (ROOT / "server").rglob("*.py"))
            + (ROOT / "app.py").read_text())
            for name in pair if name}
        self.assertTrue(read, "found no env vars in the code — bad regex?")

        docs = all_docs_text()
        undocumented = sorted(name for name in read - self.RUNTIME_INJECTED
                              if name not in docs)
        self.assertEqual(
            undocumented, [],
            "the code reads these settings but no doc mentions them, so an "
            f"operator cannot discover them: {undocumented}")

    def test_documented_vars_are_read_by_the_code(self):
        server_text = "\n".join(
            path.read_text() for path in (ROOT / "server").rglob("*.py"))
        server_text += (ROOT / "app.py").read_text()

        documented = set(re.findall(r"`(PG[A-Z]+|DATABRICKS_[A-Z_]+|ATLAS_[A-Z_]+|"
                                    r"GENIE_[A-Z_]+|SERVING_ENDPOINT|AI_QUERY_ENDPOINT|DEMO_MODE|"
                                    r"LOG_LEVEL|RATE_LIMITS)`", all_docs_text()))
        self.assertTrue(documented, "no env vars found in the docs — bad regex?")

        unread = sorted(name for name in documented
                        if f'"{name}"' not in server_text
                        and f"'{name}'" not in server_text)
        self.assertEqual(unread, [],
                         f"documented but never read by the code: {unread}")

    def test_new_settings_are_documented(self):
        """LOG_LEVEL and RATE_LIMITS change behaviour, so they must be findable."""
        for name in ("LOG_LEVEL", "RATE_LIMITS"):
            self.assertIn(name, doc_text("README.md"),
                          f"{name} is not documented in the README")
            self.assertIn(name, (ROOT / "app.yaml").read_text(),
                          f"{name} is not listed in app.yaml")


class TestDocumentedCommandsAreValid(unittest.TestCase):
    """A command in a doc must accept the flags the doc shows.

    Parsed from the argparse definitions rather than executed: running deploys and
    migrations in a test is not viable, but a renamed flag is exactly the kind of
    drift that leaves a customer stuck.
    """

    def _flags(self, script: str) -> set[str]:
        text = (ROOT / "scripts" / script).read_text()
        return set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', text))

    def test_migrate_flags_exist(self):
        flags = self._flags("migrate.py")
        for flag in ("--profile", "--project", "--status", "--allow-drift",
                     "--grant-app-sp"):
            self.assertIn(flag, flags, f"scripts/migrate.py has no {flag}")

    def test_check_flags_exist(self):
        self.assertIn("--fast", self._flags("check.py"))

    def test_documented_migrate_invocations_use_real_flags(self):
        available = self._flags("migrate.py")
        used = set()
        for name in DOCS:
            if not (ROOT / name).is_file():
                continue
            for line in doc_text(name).split("\n"):
                if "scripts/migrate.py" in line:
                    used.update(re.findall(r"(--[\w-]+)", line))
        self.assertTrue(used, "no documented migrate.py invocations found")
        unknown = sorted(used - available)
        self.assertEqual(unknown, [],
                         f"docs pass flags migrate.py does not accept: {unknown}")

    def test_documented_scripts_exist(self):
        referenced = set(re.findall(r"(scripts/[\w_]+\.py)", all_docs_text()))
        self.assertTrue(referenced)
        missing = sorted(name for name in referenced
                         if not (ROOT / name).is_file())
        self.assertEqual(missing, [], f"docs reference missing scripts: {missing}")


class TestNoStaleMigrationClaims(unittest.TestCase):
    """The removed behaviour must not still be documented.

    Startup used to replay every migration on every boot. Two docs described that,
    and a customer reading either would skip `scripts/migrate.py` and then wonder
    why their schema was missing tables.
    """

    def test_no_doc_claims_migrations_apply_on_startup(self):
        offenders = []
        for name in DOCS:
            if not (ROOT / name).is_file():
                continue
            for number, line in enumerate(doc_text(name).split("\n"), 1):
                lowered = line.lower()
                if "migration" not in lowered:
                    continue
                # "never applied on startup" and similar are correct statements.
                if re.search(r"(applied|apply|run|lands?)\b[^.]{0,40}\b"
                             r"(on startup|at startup|automatically)", lowered) \
                        and "never" not in lowered and "not " not in lowered \
                        and "cannot" not in lowered:
                    offenders.append(f"{name}:{number}: {line.strip()[:90]}")
        self.assertEqual(
            offenders, [],
            "these lines still claim migrations apply on startup, which would "
            f"lead a customer to skip scripts/migrate.py: {offenders}")

    def test_no_doc_references_the_removed_function(self):
        stale = [name for name in DOCS
                 if (ROOT / name).is_file() and "run_migrations" in doc_text(name)]
        self.assertEqual(stale, [],
                         f"these docs reference the removed run_migrations(): "
                         f"{stale}")

    def test_operations_documents_the_drift_procedure(self):
        """Drift is the one migration state needing a human decision."""
        text = doc_text("OPERATIONS.md").lower()
        self.assertIn("drift", text)
        self.assertIn("--allow-drift", doc_text("OPERATIONS.md"))


class TestArchitectureListsShippedMigrations(unittest.TestCase):
    def test_every_migration_file_is_accounted_for(self):
        """A migration nobody documented is a table nobody can explain."""
        text = doc_text("ARCHITECTURE.md")
        undocumented = []
        for path in sorted((ROOT / "server" / "migrations").glob("*.sql")):
            stem = path.stem                      # e.g. 006_branding
            number = stem.split("_")[0]           # e.g. 006
            # Either the filename or its number must appear (the README uses a
            # "001_init … 006_branding" range form).
            if stem not in text and f"{number}_" not in text:
                undocumented.append(path.name)
        self.assertEqual(undocumented, [],
                         f"migrations not mentioned in ARCHITECTURE.md: "
                         f"{undocumented}")


class TestNoHardcodedCountsThatWillRot(unittest.TestCase):
    """A stated test count is wrong the moment a test is added.

    The README used to claim "281 tests" while the suite had 519 — harmless on its
    own, but it is the same drift that let a documented-but-missing test file hide
    an untested value engine. Counts of SEED data (240 use cases, 34 assumptions)
    are fine: those are product facts, and other tests verify them.
    """

    def test_readme_does_not_state_a_test_count(self):
        offenders = [line.strip() for line in doc_text("README.md").split("\n")
                     if re.search(r"\b\d{2,}\s+(stdlib-only\s+)?tests?\b", line)]
        self.assertEqual(
            offenders, [],
            "the README states a test count, which rots on the next commit — "
            f"describe the suite instead: {offenders}")


if __name__ == "__main__":
    unittest.main()
