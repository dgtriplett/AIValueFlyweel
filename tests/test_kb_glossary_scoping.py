"""The knowledge base and the glossary must not span accounts.

THE FAILURE THIS GUARDS
-----------------------
`kb_articles`, `kb_folders` and `glossary_terms` all carry `account_id` (migration
009), but the routes ignored it entirely: reads spanned every row and writes omitted
the column, so anything created defaulted to NULL and became visible to all tenants.

Concretely, on a multi-account instance any caller could:

  * list and full-text search every utility's articles — titles AND highlighted body
    excerpts, via `/api/kb/articles?q=`;
  * read, edit, archive or hard-delete one by slug (slugs derive from titles, so they
    are guessable);
  * download any uploaded document by walking `/api/kb/attachments/{id}` upward — the
    ids are sequential integers;
  * read and rewrite another utility's business glossary.

WHY THE ASSERTIONS LOOK LIKE THIS
---------------------------------
These are SQL-shape tests, not database tests. There is no Postgres in this suite
(see tests/README.md), so what is checked is that every statement a handler issues
against a scoped table carries the account predicate and binds the resolved id —
which is exactly the thing that was missing. A test that only checked the returned
rows would pass against the vulnerable code, because the fake returns whatever it is
told regardless of the WHERE clause.

`_scoped_tables` deliberately drives the REAL handlers rather than asserting on
source text, so a handler that stops calling the helper fails here.
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import accounts  # noqa: E402
from server.routes import flow, knowledge as kbr  # noqa: E402
from fakedb import FakeDB, Row, run  # noqa: E402

ACCOUNT = 5

# Statements that legitimately span accounts, with the reason. Anything else touching
# a scoped table without `account_id` is a leak.
ALLOWED_UNSCOPED = (
    # kb_articles.slug is globally UNIQUE (migration 007), so the collision-avoidance
    # candidate list must span all rows or the INSERT violates the constraint.
    # Reads slugs only — no titles, bodies or account ids.
    "SELECT slug FROM kb_articles",
)

SCOPED_TABLES = ("kb_articles", "kb_folders", "glossary_terms")


class Req:
    headers = {"x-forwarded-email": "user@utility.com"}


class RecordingDB(FakeDB):
    """Answers plausibly and records every statement with its arguments."""

    def __init__(self):
        super().__init__(has_pool=True)
        self.calls: list[tuple[str, tuple]] = []

    def _article(self):
        return Row(id=1, title="T", body_md="b", summary="s", version=1, slug="t",
                   status="draft", created_at=None, updated_at=None, folder_id=None,
                   article_id=1, entity_type="use_case", entity_id=1,
                   relation="related")

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM accounts" in sql:
            return [Row(id=ACCOUNT)]
        if "SELECT slug FROM kb_articles" in sql:
            return [Row(slug="taken")]
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM accounts" in sql:
            return Row(id=ACCOUNT)
        # Uniqueness probes must MISS so create/rename proceeds to the write.
        if "kb_folders" in sql and ("WHERE path = $1" in sql or "id <> $2" in sql):
            return None
        if "kb_folders" in sql:
            return Row(id=1, name="f", parent_id=None, path="/f/")
        if "kb_article_versions" in sql:
            return Row(title="T", body_md="b", summary="s", version=1)
        if "kb_attachments" in sql:
            return Row(id=1, filename="a.pdf", mime_type="application/pdf",
                       storage="lakebase", volume_path=None, content=b"x",
                       article_id=1)
        if "kb_articles" in sql or "kb_links" in sql:
            return self._article()
        if "glossary_terms" in sql:
            # The duplicate probe must miss; the write must return a row.
            if sql.strip().upper().startswith("SELECT"):
                return None
            return Row(id=9, term="Feeder")
        return Row(n=0)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "OK"


class ScopingTestCase(unittest.TestCase):
    def setUp(self):
        accounts.invalidate_default()
        self.addCleanup(accounts.invalidate_default)
        self.db = RecordingDB()
        for target in (accounts, kbr, flow):
            patcher = mock.patch.object(target, "db", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        common = mock.patch("server.common.db", self.db)
        common.start()
        self.addCleanup(common.stop)

    def drive(self, coro_factory):
        """Run a handler, tolerating the 404/422 a bare fake sometimes provokes."""
        self.db.calls.clear()
        try:
            run(coro_factory())
        except Exception:  # noqa: BLE001 - the QUERIES are what is under test
            pass
        return self.db.calls

    def assert_all_scoped(self, calls, label):
        for sql, args in calls:
            if not any(table in sql for table in SCOPED_TABLES):
                continue
            if any(allowed in sql for allowed in ALLOWED_UNSCOPED):
                continue
            flat = " ".join(sql.split())
            self.assertIn(
                "account_id", sql,
                f"{label} issued an unscoped statement against a scoped table, "
                f"which reads or writes across tenants: {flat[:160]}")
            self.assertIn(
                ACCOUNT, args,
                f"{label} has an account predicate but never bound the resolved "
                f"account, so the placeholder is filled by something else: "
                f"{flat[:160]}")

    def assert_placeholders_match(self, calls, label):
        """An off-by-one in $n numbering is a runtime error, not a leak."""
        for sql, args in calls:
            numbers = [int(n) for n in re.findall(r"\$(\d+)", sql)]
            if not numbers:
                continue
            self.assertEqual(
                max(numbers), len(args),
                f"{label}: highest placeholder ${max(numbers)} but {len(args)} "
                f"arguments — asyncpg would raise: {' '.join(sql.split())[:140]}")


class TestKnowledgeBaseIsScoped(ScopingTestCase):
    HANDLERS = {
        "folder_tree": lambda: kbr.folder_tree(),
        "create_folder": lambda: kbr.create_folder(kbr.FolderIn(name="F"), Req()),
        "update_folder": lambda: kbr.update_folder(
            1, kbr.FolderPatch(name="G"), Req()),
        "delete_folder": lambda: kbr.delete_folder(1, Req()),
        "create_article": lambda: kbr.create_article(
            kbr.ArticleIn(title="T"), Req()),
        "get_article": lambda: kbr.get_article("t"),
        "update_article": lambda: kbr.update_article(
            "t", kbr.ArticleUpdate(title="T2"), Req()),
        "delete_article": lambda: kbr.delete_article("t", Req()),
        "hard_delete_article": lambda: kbr.delete_article("t", Req(), hard=True),
        "article_versions": lambda: kbr.article_versions("t"),
        "restore_version": lambda: kbr.restore_version("t", 1, Req()),
        "add_link": lambda: kbr.add_link(
            "t", kbr.LinkIn(entity_type="use_case", entity_id=1), Req()),
        "remove_link": lambda: kbr.remove_link(1, Req()),
        "articles_for_entity": lambda: kbr.articles_for_entity("use_case", 1),
        "download_attachment": lambda: kbr.download_attachment(1),
        "delete_attachment": lambda: kbr.delete_attachment(1, Req()),
    }

    def test_every_handler_scopes_every_statement(self):
        for label, factory in self.HANDLERS.items():
            with self.subTest(handler=label):
                calls = self.drive(factory)
                self.assertTrue(calls, f"{label} issued no queries at all")
                self.assert_all_scoped(calls, label)

    def test_every_handler_binds_contiguous_placeholders(self):
        for label, factory in self.HANDLERS.items():
            with self.subTest(handler=label):
                self.assert_placeholders_match(self.drive(factory), label)

    def test_new_articles_are_stamped_with_the_account(self):
        calls = self.drive(lambda: kbr.create_article(kbr.ArticleIn(title="T"), Req()))
        inserts = [(s, a) for s, a in calls if "INSERT INTO kb_articles" in s]
        self.assertTrue(inserts, "no article insert happened")
        sql, args = inserts[0]
        self.assertIn("account_id", sql,
                      "an article created without account_id is visible to every "
                      "tenant, because NULL means 'shared'")
        self.assertIn(ACCOUNT, args)

    def test_new_folders_are_stamped_with_the_account(self):
        calls = self.drive(lambda: kbr.create_folder(kbr.FolderIn(name="F"), Req()))
        inserts = [(s, a) for s, a in calls if "INSERT INTO kb_folders" in s]
        self.assertTrue(inserts)
        self.assertIn("account_id", inserts[0][0])
        self.assertIn(ACCOUNT, inserts[0][1])

    def test_attachment_download_is_scoped_through_its_article(self):
        """kb_attachments has no account_id and sequential ids — the walkable case."""
        calls = self.drive(lambda: kbr.download_attachment(1))
        reads = [s for s, _ in calls if "kb_attachments" in s]
        self.assertTrue(reads)
        self.assertTrue(
            any("kb_articles" in s and "account_id" in s for s in reads),
            "the attachment read must join its owning article and scope on it")

    def test_hard_delete_carries_the_predicate_on_the_delete_itself(self):
        calls = self.drive(lambda: kbr.delete_article("t", Req(), hard=True))
        deletes = [(s, a) for s, a in calls if "DELETE FROM kb_articles" in s]
        self.assertTrue(deletes)
        self.assertIn("account_id", deletes[0][0],
                      "an irreversible delete must not be reachable by id alone")

    def test_search_sql_scopes_when_an_account_resolves(self):
        sql = kbr.kb.build_search_sql(has_query=True, folder=False, tag=False,
                                      status=False, entity=False, account=True)
        self.assertIn("a.account_id", sql)
        self.assertIn("a.account_id IS NULL", sql,
                      "the shared reference library must stay visible")

    def test_search_placeholders_stay_contiguous_with_scoping(self):
        """The account predicate must not collide with limit/offset numbering."""
        for has_query in (True, False):
            for folder in (True, False):
                for tag in (True, False):
                    for status in (True, False):
                        for entity in (True, False):
                            sql = kbr.kb.build_search_sql(
                                has_query=has_query, folder=folder, tag=tag,
                                status=status, entity=entity, account=True)
                            numbers = sorted({int(n)
                                              for n in re.findall(r"\$(\d+)", sql)})
                            self.assertEqual(
                                numbers, list(range(1, len(numbers) + 1)),
                                f"non-contiguous placeholders: {numbers}")


class TestGlossaryIsScoped(ScopingTestCase):
    HANDLERS = {
        "list_glossary": lambda: flow.list_glossary(include_derived=False),
        "create_term": lambda: flow.create_term(
            flow.GlossaryTermIn(term="Feeder", definition="d"), Req()),
        "update_term": lambda: flow.update_term(
            9, flow.GlossaryTermIn(term="Feeder", definition="d2"), Req()),
        "delete_term": lambda: flow.delete_term(9, Req()),
    }

    def test_every_handler_scopes_every_statement(self):
        for label, factory in self.HANDLERS.items():
            with self.subTest(handler=label):
                calls = self.drive(factory)
                self.assertTrue(calls, f"{label} issued no queries")
                self.assert_all_scoped(calls, label)

    def test_every_handler_binds_contiguous_placeholders(self):
        for label, factory in self.HANDLERS.items():
            with self.subTest(handler=label):
                self.assert_placeholders_match(self.drive(factory), label)

    def test_new_terms_are_stamped_with_the_account(self):
        calls = self.drive(self.HANDLERS["create_term"])
        inserts = [(s, a) for s, a in calls if "INSERT INTO glossary_terms" in s]
        self.assertTrue(inserts, "no term insert happened")
        self.assertIn("account_id", inserts[0][0])
        self.assertIn(ACCOUNT, inserts[0][1])

    def test_update_has_an_ownership_predicate(self):
        calls = self.drive(self.HANDLERS["update_term"])
        updates = [(s, a) for s, a in calls if "UPDATE glossary_terms" in s]
        self.assertTrue(updates)
        self.assertIn("account_id", updates[0][0],
                      "selecting by row id alone let any caller rewrite any "
                      "utility's vocabulary")

    def test_delete_has_an_ownership_predicate(self):
        calls = self.drive(self.HANDLERS["delete_term"])
        deletes = [(s, a) for s, a in calls if "DELETE FROM glossary_terms" in s]
        self.assertTrue(deletes)
        self.assertIn("account_id", deletes[0][0])

    def test_the_list_read_is_scoped(self):
        calls = self.drive(self.HANDLERS["list_glossary"])
        reads = [s for s, _ in calls if "FROM glossary_terms" in s]
        self.assertTrue(reads)
        self.assertIn("account_id", reads[0])


class TestSharedRowsStayVisible(ScopingTestCase):
    """NULL account_id is the shipped reference library, not a leak.

    If the predicate were a bare equality, every seeded KB folder and reference term
    would vanish for every tenant — a scoping fix that breaks the product.
    """

    def test_scope_clause_keeps_null_rows(self):
        clause, params = run(accounts.scope_clause("a"))
        self.assertIn("a.account_id IS NULL", clause)
        self.assertEqual(params, [ACCOUNT])

    def test_scope_clause_at_keeps_null_rows_and_renumbers(self):
        clause, params = run(accounts.scope_clause_at(4, "a"))
        self.assertIn("a.account_id = $4", clause)
        self.assertIn("a.account_id IS NULL", clause)
        self.assertEqual(params, [ACCOUNT])

    def test_scope_clause_at_is_unscoped_before_any_account_exists(self):
        empty = FakeDB(has_pool=True)
        with mock.patch.object(accounts, "db", empty):
            accounts.invalidate_default()
            clause, params = run(accounts.scope_clause_at(3))
        self.assertEqual((clause, params), ("true", []))


if __name__ == "__main__":
    unittest.main()
