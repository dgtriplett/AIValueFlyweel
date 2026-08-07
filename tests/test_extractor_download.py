"""The extractor download bundle.

This exists because the app's service principal can only authenticate to the one
workspace it runs in, while a utility's data spans several. Serving the tool from
the app means the person who needs it is already on the page explaining why, and
their copy matches the deployed code that will ingest the output.

Two things are worth protecting: the ZIP must be self-contained and runnable, and
it must never carry someone else's data out of the source tree.
"""
import io
import os
import sys
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from fakedb import run  # noqa: E402
from server.routes import ingestion  # noqa: E402


def _zip_bytes() -> bytes:
    """Drain a StreamingResponse. Its body_iterator is async, so it must be
    consumed inside the event loop rather than joined directly."""
    async def collect():
        response = await ingestion.download_extractor()
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return b"".join(chunks)

    return run(collect())


class TestExtractorFiles(unittest.TestCase):
    def test_source_is_present_in_the_repo(self):
        self.assertTrue(ingestion.EXTRACTOR_DIR.is_dir(),
                        f"missing {ingestion.EXTRACTOR_DIR}")

    def test_ships_the_files_needed_to_run(self):
        names = {p.name for p in ingestion._extractor_files()}
        for required in ("extract_schemas.py", "requirements.txt",
                         "workspaces.txt", "README.md"):
            self.assertIn(required, names, f"{required} would not be shipped")

    def test_excludes_caches_and_outputs(self):
        """A .pyc or a leftover output/ CSV in the tree must not ship."""
        for path in ingestion._extractor_files():
            relative = path.relative_to(ingestion.EXTRACTOR_DIR)
            self.assertNotIn("__pycache__", relative.parts)
            self.assertNotIn("output", relative.parts)
            self.assertNotIn(path.suffix.lower(), {".pyc", ".pyo"})

    def test_never_ships_a_csv(self):
        """The one real privacy risk: a CSV left in the source tree is somebody
        else's workspace metadata, and it must not leave with the download."""
        for path in ingestion._extractor_files():
            self.assertNotEqual(path.suffix.lower(), ".csv", str(path))


class TestZipBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _zip_bytes()
        cls.zip = zipfile.ZipFile(io.BytesIO(cls.data))

    def test_is_a_valid_zip(self):
        self.assertIsNone(self.zip.testzip())
        self.assertGreater(len(self.data), 1000)

    def test_everything_sits_under_one_directory(self):
        """Unzipping must not scatter files into the user's cwd."""
        for name in self.zip.namelist():
            self.assertTrue(name.startswith("schema-extractor/"), name)

    def test_contains_a_runnable_script(self):
        source = self.zip.read("schema-extractor/extract_schemas.py").decode()
        self.assertIn("def main()", source)
        compile(source, "extract_schemas.py", "exec")  # raises on a syntax error

    def test_includes_generated_instructions(self):
        names = self.zip.namelist()
        self.assertIn("schema-extractor/RUN-ME-FIRST.txt", names)

    def test_instructions_explain_the_why_and_the_upload_target(self):
        """A downloader who reads nothing else must still learn why they are
        running it locally, that it is metadata-only, and where the output goes."""
        text = self.zip.read("schema-extractor/RUN-ME-FIRST.txt").decode()
        self.assertIn("only has credentials for", text)
        self.assertIn("METADATA ONLY", text)
        self.assertIn("all_schemas.csv", text)
        self.assertIn("all_tables.csv", text)
        self.assertIn("Get started", text)

    def test_instructions_mention_the_documented_flags(self):
        text = self.zip.read("schema-extractor/RUN-ME-FIRST.txt").decode()
        for flag in ("--no-columns", "--warehouse-id", "--output-dir"):
            self.assertIn(flag, text)

    def test_no_csv_in_the_archive(self):
        leaked = [n for n in self.zip.namelist() if n.lower().endswith(".csv")]
        self.assertEqual(leaked, [], f"CSV files leaked into the download: {leaked}")

    def test_rebuilt_per_request_not_cached_stale(self):
        """Generated fresh each time, so the bundle always matches deployed code."""
        again = _zip_bytes()
        self.assertGreater(len(again), 1000)
        one = zipfile.ZipFile(io.BytesIO(again))
        self.assertEqual(set(one.namelist()), set(self.zip.namelist()))


class TestInfoEndpoint(unittest.TestCase):
    def test_describes_the_bundle_before_download(self):
        info = run(ingestion.extractor_info())
        self.assertTrue(info["available"])
        self.assertIn("extract_schemas.py", info["files"])
        self.assertGreater(info["total_bytes"], 0)
        self.assertIn("metadata only", info["reads"])
        self.assertIn("all_schemas.csv", info["produces"])

    def test_reports_unavailable_rather_than_raising(self):
        """A deployment missing the source should say so, not 500 the page."""
        from pathlib import Path
        original = ingestion.EXTRACTOR_DIR
        try:
            ingestion.EXTRACTOR_DIR = Path("/nonexistent-extractor-dir")
            info = run(ingestion.extractor_info())
            self.assertFalse(info["available"])
            self.assertEqual(info["files"], [])
        finally:
            ingestion.EXTRACTOR_DIR = original

    def test_download_gives_an_actionable_error_when_source_is_missing(self):
        from pathlib import Path

        from fastapi import HTTPException
        original = ingestion.EXTRACTOR_DIR
        try:
            ingestion.EXTRACTOR_DIR = Path("/nonexistent-extractor-dir")
            with self.assertRaises(HTTPException) as caught:
                run(ingestion.download_extractor())
            self.assertIn("repository", str(caught.exception.detail))
        finally:
            ingestion.EXTRACTOR_DIR = original


if __name__ == "__main__":
    unittest.main()
