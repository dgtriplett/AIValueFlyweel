"""Knowledge base: slugs, folder paths, attachment validation, search SQL.

The attachment tests are the security-relevant ones. These bytes are uploaded by one
user and served back to another, so the declared type has to be verified against the
actual content — a file claiming to be a PDF that is really HTML becomes stored XSS
the moment a browser renders it. Each rejection case below is a payload that would
otherwise be stored and served under a type it does not have.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import knowledge as kb  # noqa: E402

ROOT = Path(__file__).parent.parent


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(kb.slugify("Protection Standards"), "protection-standards")

    def test_strips_punctuation(self):
        self.assertEqual(kb.slugify("SAIDI/SAIFI Definitions!"),
                         "saidisaifi-definitions")

    def test_transliterates_accents(self):
        # Percent-encoding would be unreadable in a URL, an export, and a
        # [[wiki link]] alike.
        self.assertEqual(kb.slugify("Réseau électrique"), "reseau-electrique")

    def test_collapses_whitespace_runs(self):
        self.assertEqual(kb.slugify("A   B \t C"), "a-b-c")

    def test_caps_length(self):
        self.assertLessEqual(len(kb.slugify("word " * 100)), kb.MAX_SLUG_LENGTH)

    def test_never_ends_with_a_separator(self):
        # A trailing dash after truncation looks like a broken link.
        for title in ("word " * 30, "a-" * 60, "Ends With Dash -"):
            self.assertFalse(kb.slugify(title).endswith("-"), title[:20])

    def test_untitleable_input_still_yields_a_slug(self):
        """Punctuation-only or non-Latin titles must not produce an empty slug."""
        for title in ("!!!", "###", "。。。", "   "):
            slug = kb.slugify(title)
            self.assertTrue(slug, f"empty slug for {title!r}")
            self.assertTrue(slug.startswith("article-"), slug)

    def test_fallback_is_stable_for_the_same_title(self):
        # Deterministic, so re-saving does not silently change the URL.
        self.assertEqual(kb.slugify("!!!"), kb.slugify("!!!"))

    def test_reserved_slugs_are_escaped(self):
        """A route literal must not be shadowed by an article slug.

        /kb/articles/search is a real route; an article slugged "search" would be
        unreachable. That exact route-shadowing class of bug has already been fixed
        three times elsewhere in this app.
        """
        for reserved in ("search", "new", "versions", "export"):
            self.assertNotEqual(kb.slugify(reserved), reserved)
            self.assertNotIn(kb.slugify(reserved), kb.RESERVED_SLUGS)

    def test_collision_gets_a_numeric_suffix(self):
        self.assertEqual(
            kb.slugify("Protection Standards", existing={"protection-standards"}),
            "protection-standards-2")

    def test_collision_skips_taken_suffixes(self):
        existing = {"standards", "standards-2", "standards-3"}
        self.assertEqual(kb.slugify("Standards", existing=existing), "standards-4")

    def test_collision_result_respects_the_length_cap(self):
        long_title = "x" * kb.MAX_SLUG_LENGTH
        base = kb.slugify(long_title)
        result = kb.slugify(long_title, existing={base})
        self.assertLessEqual(len(result), kb.MAX_SLUG_LENGTH)
        self.assertNotEqual(result, base)


class TestFolderPaths(unittest.TestCase):
    def test_root_folder(self):
        self.assertEqual(kb.folder_path(None, "Standards & Specs"),
                         "/standards-specs/")

    def test_nested_folder(self):
        self.assertEqual(kb.folder_path("/standards-specs/", "Protection"),
                         "/standards-specs/protection/")

    def test_always_has_both_slashes(self):
        """A trailing slash is what stops a prefix match hitting a sibling.

        Without it, `LIKE '/standards%'` also matches '/standards-2/', so asking for
        one folder's subtree silently returns another folder's articles.
        """
        for path in (kb.folder_path(None, "A"), kb.folder_path("/a/", "B")):
            self.assertTrue(path.startswith("/"))
            self.assertTrue(path.endswith("/"))

    def test_sibling_prefix_does_not_match_subtree(self):
        standards = kb.folder_path(None, "Standards")
        standards_2 = kb.folder_path(None, "Standards 2")
        self.assertFalse(standards_2.startswith(standards),
                         f"{standards_2} would be returned when querying "
                         f"the subtree of {standards}")

    def test_like_metacharacters_are_escaped(self):
        """A folder named "50% load" must not turn % into a wildcard."""
        pattern = kb.descendant_pattern("/50%_load/")
        self.assertIn("\\%", pattern)
        self.assertIn("\\_", pattern)
        self.assertTrue(pattern.endswith("%"), "must still match descendants")

    def test_circular_move_is_detected(self):
        # Moving a folder into its own descendant detaches the whole branch: the
        # sidebar silently stops rendering those articles.
        self.assertTrue(kb.is_circular_move("/a/", "/a/b/"))
        self.assertTrue(kb.is_circular_move("/a/", "/a/"))
        self.assertFalse(kb.is_circular_move("/a/", "/c/"))
        self.assertFalse(kb.is_circular_move("/a/", "/ab/"),
                         "a name prefix is not a descendant")


class TestAttachmentValidation(unittest.TestCase):
    PDF = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>"
    ZIP = b"PK\x03\x04\x14\x00\x00\x00"
    DOCX = ("application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document")

    def test_accepts_a_real_pdf(self):
        mime, digest = kb.validate_attachment("study.pdf", "application/pdf", self.PDF)
        self.assertEqual(mime, "application/pdf")
        self.assertEqual(len(digest), 64)

    def test_accepts_office_formats(self):
        for name, mime in (("a.docx", self.DOCX),
                           ("b.xlsx", "application/vnd.openxmlformats-officedocument"
                                      ".spreadsheetml.sheet")):
            resolved, _ = kb.validate_attachment(name, mime, self.ZIP)
            self.assertEqual(resolved, mime)

    def test_rejects_html_disguised_as_pdf(self):
        """The stored-XSS case: served back under a PDF type, rendered as HTML."""
        with self.assertRaises(kb.AttachmentRejected) as caught:
            kb.validate_attachment("evil.pdf", "application/pdf",
                                   b"<html><script>alert(1)</script></html>")
        self.assertIn("does not look like", str(caught.exception))

    def test_rejects_a_zip_renamed_to_pdf(self):
        with self.assertRaises(kb.AttachmentRejected) as caught:
            kb.validate_attachment("renamed.pdf", "application/pdf", self.ZIP)
        self.assertIn("contents look like", str(caught.exception))

    def test_rejects_an_unlisted_type(self):
        for name, mime, content in (
            ("x.exe", "application/x-msdownload", b"MZ\x90\x00"),
            ("x.svg", "image/svg+xml", b"<svg onload=alert(1)>"),
            ("x.html", "text/html", b"<html>"),
        ):
            with self.assertRaises(kb.AttachmentRejected):
                kb.validate_attachment(name, mime, content)

    def test_rejects_a_mismatched_extension(self):
        # Declared type and filename must agree, or the download route's filename
        # and its Content-Type disagree about what the file is.
        with self.assertRaises(kb.AttachmentRejected) as caught:
            kb.validate_attachment("study.docx", "application/pdf", self.PDF)
        self.assertIn("extension", str(caught.exception))

    def test_rejects_empty(self):
        with self.assertRaises(kb.AttachmentRejected):
            kb.validate_attachment("a.pdf", "application/pdf", b"")

    def test_rejects_oversize(self):
        oversize = self.PDF + b"\x00" * kb.MAX_ATTACHMENT_BYTES
        with self.assertRaises(kb.AttachmentRejected) as caught:
            kb.validate_attachment("big.pdf", "application/pdf", oversize)
        self.assertIn("limit", str(caught.exception))

    def test_size_cap_fits_a_real_utility_document(self):
        """15MB interconnection studies are routine; the cap must clear them."""
        self.assertGreaterEqual(kb.MAX_ATTACHMENT_BYTES, 20 * 1024 * 1024)

    def test_text_types_are_accepted_without_sniffing(self):
        # No magic number exists for these. Safe because the download route sends
        # Content-Disposition: attachment, so nothing is rendered inline.
        for name, mime in (("a.txt", "text/plain"), ("b.csv", "text/csv"),
                           ("c.md", "text/markdown")):
            resolved, _ = kb.validate_attachment(name, mime, b"anything at all")
            self.assertEqual(resolved, mime)

    def test_checksum_is_content_addressed(self):
        _, first = kb.validate_attachment("a.pdf", "application/pdf", self.PDF)
        _, same = kb.validate_attachment("b.pdf", "application/pdf", self.PDF)
        _, other = kb.validate_attachment("c.pdf", "application/pdf",
                                          self.PDF + b" more")
        self.assertEqual(first, same, "same bytes must hash the same")
        self.assertNotEqual(first, other)

    def test_mime_parameters_are_stripped(self):
        resolved, _ = kb.validate_attachment("a.pdf", "application/pdf; charset=binary",
                                            self.PDF)
        self.assertEqual(resolved, "application/pdf")


class TestSniffMime(unittest.TestCase):
    def test_recognises_known_headers(self):
        self.assertEqual(kb.sniff_mime(b"%PDF-1.4"), "application/pdf")
        self.assertEqual(kb.sniff_mime(b"\x89PNG\r\n\x1a\n"), "image/png")
        self.assertEqual(kb.sniff_mime(b"\xff\xd8\xff\xe0"), "image/jpeg")
        self.assertEqual(kb.sniff_mime(b"PK\x03\x04"), "application/zip")

    def test_unknown_and_empty(self):
        self.assertIsNone(kb.sniff_mime(b"just some text"))
        self.assertIsNone(kb.sniff_mime(b""))


class TestPathTraversal(unittest.TestCase):
    """The Volume writer takes a filename; it must not be able to escape."""

    def test_strips_directory_components(self):
        self.assertEqual(kb.safe_volume_filename("../../etc/passwd"), "passwd")
        self.assertEqual(kb.safe_volume_filename("C:\\Windows\\evil.pdf"), "evil.pdf")
        self.assertEqual(kb.safe_volume_filename("a/b/c/report.pdf"), "report.pdf")

    def test_result_is_a_single_safe_segment(self):
        for hostile in ("../../etc/passwd", "....//x.pdf", "a/b\\c", "..", ".",
                        "a b;rm -rf /.pdf", "\x00null.pdf"):
            safe = kb.safe_volume_filename(hostile)
            self.assertNotIn("/", safe, hostile)
            self.assertNotIn("\\", safe, hostile)
            self.assertFalse(safe.startswith("."), f"{hostile!r} -> {safe!r}")
            self.assertTrue(safe, hostile)

    def test_keeps_ordinary_names_intact(self):
        self.assertEqual(kb.safe_volume_filename("Protection_Standard-v2.pdf"),
                         "Protection_Standard-v2.pdf")


class TestSearchSql(unittest.TestCase):
    """The statement is assembled from filters, so every branch must bind cleanly."""

    ALL_COMBINATIONS = [
        dict(has_query=q, folder=f, tag=t, status=s, entity=e)
        for q in (True, False) for f in (True, False) for t in (True, False)
        for s in (True, False) for e in (True, False)
    ]

    def test_placeholders_are_contiguous_from_one(self):
        """asyncpg errors if $n has a gap, and every combination is reachable."""
        for kwargs in self.ALL_COMBINATIONS:
            sql = kb.build_search_sql(**kwargs)
            numbers = sorted({int(n) for n in re.findall(r"\$(\d+)", sql)})
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                             f"non-contiguous placeholders for {kwargs}: {numbers}")

    def test_no_literal_interpolation_of_values(self):
        """Every user value must arrive as a bind parameter."""
        for kwargs in self.ALL_COMBINATIONS:
            sql = kb.build_search_sql(**kwargs)
            self.assertNotIn("%s", sql)
            self.assertNotIn(".format(", sql)

    def test_orders_by_rank_only_when_ranking(self):
        with_query = kb.build_search_sql(has_query=True, folder=False, tag=False,
                                         status=False, entity=False)
        self.assertIn("AS rank", with_query)
        self.assertIn("ORDER BY rank DESC", with_query)

        without = kb.build_search_sql(has_query=False, folder=False, tag=False,
                                      status=False, entity=False)
        self.assertNotIn("ORDER BY rank", without,
                         "ordering by a column that is not selected is an error")
        self.assertIn("ORDER BY a.updated_at DESC", without)

    def test_uses_websearch_to_tsquery(self):
        """to_tsquery raises on the syntax people actually type; websearch does not."""
        sql = kb.build_search_sql(has_query=True, folder=False, tag=False,
                                  status=False, entity=False)
        self.assertIn("websearch_to_tsquery", sql)
        self.assertNotRegex(sql, r"[^_]plainto_tsquery")

    def test_hides_archived_unless_asked(self):
        default = kb.build_search_sql(has_query=False, folder=False, tag=False,
                                      status=False, entity=False)
        self.assertIn("a.status <> 'archived'", default)
        explicit = kb.build_search_sql(has_query=False, folder=False, tag=False,
                                       status=True, entity=False)
        self.assertNotIn("<> 'archived'", explicit,
                         "an explicit status filter must be able to reach archived")

    def test_folder_filter_covers_the_subtree(self):
        sql = kb.build_search_sql(has_query=False, folder=True, tag=False,
                                  status=False, entity=False)
        self.assertIn("LIKE", sql, "asking for a folder must include what's beneath it")

    def test_query_matches_title_substrings_too(self):
        # Full-text search cannot do infix matching; a partial equipment code should
        # still find its article.
        sql = kb.build_search_sql(has_query=True, folder=False, tag=False,
                                  status=False, entity=False)
        self.assertIn("ILIKE", sql)


class TestWikiLinks(unittest.TestCase):
    def test_extracts_and_slugifies(self):
        self.assertEqual(
            kb.extract_wiki_links("See [[Protection Standards]] then [[Runbooks]]."),
            ["protection-standards", "runbooks"])

    def test_handles_a_display_alias(self):
        self.assertEqual(kb.extract_wiki_links("[[Protection Standards|the standard]]"),
                         ["protection-standards"])

    def test_ignores_empty_and_unclosed(self):
        self.assertEqual(kb.extract_wiki_links("[[]] and [[ ]] and [[unclosed"), [])

    def test_no_links(self):
        self.assertEqual(kb.extract_wiki_links(""), [])
        self.assertEqual(kb.extract_wiki_links("plain prose"), [])


class TestMigrationDdl(unittest.TestCase):
    """Static checks on the DDL, since the suite has no Postgres."""

    @classmethod
    def setUpClass(cls):
        cls.sql = (ROOT / "server" / "migrations" / "007_knowledge.sql").read_text()

    def test_creates_the_expected_tables(self):
        for table in ("kb_folders", "kb_articles", "kb_article_versions",
                      "kb_links", "kb_attachments"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.sql)

    def test_is_idempotent(self):
        """Migrations are applied once, but a re-run must not fail."""
        creates = re.findall(r"CREATE TABLE (?!IF NOT EXISTS)", self.sql)
        self.assertEqual(creates, [], "a CREATE TABLE without IF NOT EXISTS")
        indexes = re.findall(r"CREATE (?:UNIQUE )?INDEX (?!IF NOT EXISTS)", self.sql)
        self.assertEqual(indexes, [], "an index without IF NOT EXISTS")

    def test_index_names_are_unique(self):
        names = re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)", self.sql)
        self.assertEqual(len(names), len(set(names)), f"duplicate index name: {names}")

    def test_root_folder_uniqueness_uses_a_partial_index(self):
        """NULL != NULL, so a plain UNIQUE(parent_id, name) allows unlimited
        duplicate root folders."""
        self.assertIn("kb_folders_unique_root", self.sql)
        self.assertIn("WHERE parent_id IS NULL", self.sql)

    def test_attachment_storage_is_constrained(self):
        """A row claiming 'volume' with a NULL path would fail only on download."""
        self.assertIn("kb_attachments_storage_consistent", self.sql)
        self.assertIn("CHECK (", self.sql)

    def test_versions_are_unique_per_article(self):
        self.assertIn("UNIQUE (article_id, version)", self.sql)

    def test_links_cannot_duplicate(self):
        self.assertIn("UNIQUE (article_id, entity_type, entity_id, relation)",
                      self.sql)

    def test_cascades_from_article_deletion(self):
        # Versions, links and attachments are meaningless without their article.
        for table in ("kb_article_versions", "kb_links", "kb_attachments"):
            section = self.sql[self.sql.index(f"CREATE TABLE IF NOT EXISTS {table}"):]
            section = section[:section.index(");")]
            self.assertIn("ON DELETE CASCADE", section, table)

    def test_search_vector_is_generated_not_triggered(self):
        """A GENERATED column cannot drift from the row it describes."""
        self.assertIn("GENERATED ALWAYS AS", self.sql)
        self.assertIn("STORED", self.sql)
        self.assertNotIn("CREATE TRIGGER", self.sql)

    def test_title_outranks_body_in_search(self):
        self.assertIn("setweight", self.sql)
        self.assertIn("'A'", self.sql)

    def test_optional_extension_failure_is_contained(self):
        """pg_trgm may not be grantable; that must not fail the migration."""
        self.assertIn("EXCEPTION WHEN insufficient_privilege", self.sql)

    def test_entity_types_are_a_closed_vocabulary(self):
        match = re.search(r"entity_type TEXT NOT NULL\s*\n\s*CHECK \(entity_type IN "
                          r"\(([^)]+)\)", self.sql)
        self.assertIsNotNone(match, "entity_type has no CHECK constraint")
        self.assertIn("'use_case'", match.group(1))


class TestTitleUniquenessMigration(unittest.TestCase):
    """008 closes a check-then-insert race that could double-count value."""

    @classmethod
    def setUpClass(cls):
        cls.sql = (ROOT / "server" / "migrations"
                   / "008_use_case_title_unique.sql").read_text()

    def test_adds_a_case_insensitive_unique_index(self):
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS use_cases_title_unique",
                      self.sql)
        self.assertIn("lower(title)", self.sql,
                      "the route compares case-insensitively, so the index must too")

    def test_merges_duplicates_before_adding_the_index(self):
        """Otherwise the migration aborts on a database that has duplicates.

        Anchored on the executable statements, not on a mention of the temp table:
        the first version matched the explanatory comment above the DO block, which
        made it fail on correct SQL.
        """
        merge_at = self.sql.index("CREATE TEMP TABLE uc_merge_map")
        index_at = self.sql.index("CREATE UNIQUE INDEX IF NOT EXISTS")
        self.assertLess(merge_at, index_at,
                        "the merge must run before the index is created")

    def test_repoints_child_rows(self):
        """A merge that deletes a duplicate without moving its children orphans
        requirements and value records."""
        for table in ("uc_requires_domain", "uc_requires_asset", "value_records",
                      "roadmap_items"):
            self.assertIn(table, self.sql, f"{table} rows would be orphaned")

    def test_keeps_the_lowest_id(self):
        self.assertIn("min(id)", self.sql,
                      "keep the original, not an arbitrary duplicate")

    def test_writes_an_audit_trail(self):
        """A silently changed use-case count during an upgrade is unexplainable."""
        self.assertIn("audit_log", self.sql)
        self.assertIn("merge_duplicate", self.sql)

    def test_deletes_edges_in_both_directions(self):
        section = self.sql[self.sql.index("DELETE FROM uc_enables_uc"):]
        section = section[:200]
        self.assertIn("from_use_case_id", section)
        self.assertIn("to_use_case_id", section)


if __name__ == "__main__":
    unittest.main()
