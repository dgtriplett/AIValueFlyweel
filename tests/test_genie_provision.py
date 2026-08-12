"""Genie space provisioning.

WHY THIS IS TESTED HARDER THAN IT LOOKS
--------------------------------------
Creating the space is one HTTP POST. Everything that can go wrong is in the payload,
and nothing in the payload fails loudly: a space configured over a table that does not
exist, or with instructions that omit the units, still creates successfully and returns
200. The damage shows up later as a confident wrong answer in front of a customer.

So the tests here assert the three things that make the space CORRECT rather than
merely created:

  1. It points at the tables the mirror actually builds. Named literals drift — the
     first version of this module listed `assets` and `lobs`, neither of which
     mirror_to_uc() has ever created. That is verified against live.py's DDL, not
     against a second copy of the list.
  2. It tells Genie the two things it cannot infer: that value is $M/year, and that the
     two tables have no join key. Both tables have an `id`, so a plausible-looking
     `use_cases.id = data_assets.id` returns rows — a fabricated answer with no error.
  3. It refuses to replace an existing space. Instructions and benchmarks get tuned by
     hand and this app does not store them, so an overwrite is unrecoverable.
"""
import ast
import json
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import genie_provision as gp  # noqa: E402

ROOT = Path(__file__).parent.parent


class TestTablesMatchTheMirror(unittest.TestCase):
    """The failure that motivated this file.

    MIRROR_TABLES is a hand-written list of what another module creates. My first
    version said ("use_cases", "assets", "lobs"); the mirror creates use_cases and
    data_assets and has no lobs table at all. A space built from that list would have
    failed on two of three tables — and the create call itself would still have
    succeeded, because Genie does not validate table existence at creation time.

    Derived from live.py's CREATE TABLE statements so the two cannot drift apart.
    """

    @classmethod
    def setUpClass(cls):
        source = (ROOT / "server" / "live.py").read_text()
        # The mirror writes into an f-string-interpolated `{fq}` prefix.
        cls.created = set(re.findall(r"CREATE TABLE \{fq\}\.(\w+)", source))
        cls.mirror_source = source

    def test_the_mirror_creates_at_least_one_table(self):
        """Guards the regex itself. If mirror_to_uc is rewritten to build its DDL some
        other way, this test fails loudly instead of silently finding nothing and
        letting every assertion below pass vacuously."""
        self.assertTrue(self.created,
                        "could not find any CREATE TABLE in live.py — the regex this "
                        "test depends on no longer matches the mirror")

    def test_every_configured_table_is_actually_created(self):
        for table in gp.MIRROR_TABLES:
            self.assertIn(table, self.created,
                          f"the space is configured over {table!r}, which "
                          f"mirror_to_uc() does not create (it creates "
                          f"{sorted(self.created)}). Genie would answer every "
                          "question about it with 'table not found'.")

    def test_no_mirrored_table_is_left_out(self):
        """The other direction: a table the mirror populates but the space ignores is
        data the user paid to materialize and cannot query."""
        for table in self.created:
            self.assertIn(table, gp.MIRROR_TABLES,
                          f"the mirror creates {table!r} but the space does not "
                          "include it, so it is invisible to Genie")

    def test_matchable_columns_exist_in_the_mirror_ddl(self):
        """Entity matching on a column that does not exist is rejected at create time
        with an error that does not name the column."""
        for table, columns in gp._MATCHABLE.items():
            ddl = re.search(r"CREATE TABLE \{fq\}\.%s \((.*?)\)\"\"\"" % table,
                            self.mirror_source, re.S)
            self.assertIsNotNone(ddl, f"no DDL found for {table}")
            declared = set(re.findall(r"(\w+)\s+(?:INT|STRING|DOUBLE)", ddl.group(1)))
            for column in columns:
                self.assertIn(column, declared,
                              f"{table}.{column} is configured for entity matching "
                              f"but is not in the mirror DDL (has {sorted(declared)})")


class TestSerializedSpace(unittest.TestCase):
    """The payload shape, established by probing the live API one rejection at a time.

    Each assertion below corresponds to a real 400 from
    POST /api/2.0/genie/spaces. The API validates one field per response and its errors
    are terse, so the sequence took several round trips to work out; pinning it here is
    the only thing that stops a refactor rediscovering it the hard way.
    """

    def setUp(self):
        self.payload = json.loads(gp.build_serialized_space({
            "use_cases": ["id", "title", "domain", "hypothesized_value_mm", "readiness"],
            "data_assets": ["id", "source_category", "vendor", "ingestion_status"],
        }))

    def test_is_a_json_string_not_an_object(self):
        """`serialized_space` is a STRING field containing JSON. Passing a dict is a
        400 that does not explain itself."""
        self.assertIsInstance(gp.build_serialized_space({}), str)

    def test_version_is_two(self):
        self.assertEqual(self.payload["version"], 2)

    def test_has_the_required_sections(self):
        for key in ("version", "data_sources", "instructions"):
            self.assertIn(key, self.payload)

    def test_instruction_content_is_an_array_of_strings(self):
        """The live API: 'Expected an array for content but found "All value figures
        are in MILLIONS..."'. A bare string is the natural thing to write and it is
        rejected outright."""
        for instruction in self.payload["instructions"]["text_instructions"]:
            self.assertIsInstance(instruction["content"], list,
                                  "content must be an array of strings")
            for line in instruction["content"]:
                self.assertIsInstance(line, str)

    def test_exactly_one_text_instruction(self):
        """The live API: 'text_instructions must contain at most one item'.

        One instruction per rule is the obvious structure and it is rejected. The rules
        are joined into a single document, which also matches what the API returns for a
        hand-built space. INSTRUCTIONS stays a list only for authoring and testing.
        """
        self.assertEqual(len(self.payload["instructions"]["text_instructions"]), 1)

    def test_the_instruction_has_a_32_hex_id(self):
        """The live API: 'must be provided and non-empty. Expected lowercase 32-hex
        UUID without hyphens.'"""
        for instruction in self.payload["instructions"]["text_instructions"]:
            self.assertRegex(instruction["id"], r"^[0-9a-f]{32}$")

    def test_tables_are_sorted_by_identifier(self):
        """The live API: 'data_sources.tables must be sorted by identifier'.

        MIRROR_TABLES is ordered for readability (use_cases first), which is the wrong
        order here, so this does not hold by accident.
        """
        identifiers = [t["identifier"] for t in self.payload["data_sources"]["tables"]]
        self.assertEqual(identifiers, sorted(identifiers))

    def test_column_configs_are_sorted_by_name(self):
        """The live API: 'column_configs must be sorted by column_name'.

        Columns arrive in ordinal_position order from information_schema, so without an
        explicit sort this fails.
        """
        for table in self.payload["data_sources"]["tables"]:
            names = [c["column_name"] for c in table["column_configs"]]
            self.assertEqual(names, sorted(names), f"{table['identifier']} unsorted")

    def test_benchmarks_are_omitted_entirely(self):
        """Not sent empty — omitted.

        A benchmark question requires at least one `answer`, and an answer is SQL
        asserted to be CORRECT; Genie scores itself against it. Shipping hand-written
        SQL as ground truth for a schema whose contents vary per install would mean
        that a subtly wrong query silently becomes the standard the space is measured
        against. Absent is honest; empty-or-guessed is not.
        """
        self.assertNotIn("benchmarks", self.payload)

    def test_starter_questions_ship_as_an_instruction(self):
        """They still have to reach the space — they are what tells a new user what to
        ask. Dropping them along with the benchmarks would lose that."""
        blob = json.dumps(self.payload)
        for question in gp.STARTER_QUESTIONS:
            self.assertIn(question, blob)

    def test_tables_are_fully_qualified(self):
        """A two-part name resolves against whatever the warehouse default catalog is,
        which is not necessarily the mirror's catalog."""
        for table in self.payload["data_sources"]["tables"]:
            self.assertEqual(table["identifier"].count("."), 2,
                             f"{table['identifier']} is not catalog.schema.table")

    def test_only_declared_columns_are_configured(self):
        """Columns are filtered against what the warehouse reports, so a stale entry in
        _MATCHABLE cannot reach the API."""
        payload = json.loads(gp.build_serialized_space({"use_cases": ["title"]}))
        use_cases = next(t for t in payload["data_sources"]["tables"]
                         if t["identifier"].endswith(".use_cases"))
        self.assertEqual([c["column_name"] for c in use_cases["column_configs"]],
                         ["title"])

    def test_numeric_columns_are_not_entity_matched(self):
        use_cases = next(t for t in self.payload["data_sources"]["tables"]
                         if t["identifier"].endswith(".use_cases"))
        names = [c["column_name"] for c in use_cases["column_configs"]]
        self.assertNotIn("hypothesized_value_mm", names,
                         "entity matching on a value column is noise")
        self.assertNotIn("id", names)

    def test_missing_column_data_still_yields_a_valid_table_entry(self):
        """A table the warehouse could not describe must still appear, with no column
        configs, rather than being dropped from the space."""
        payload = json.loads(gp.build_serialized_space({}))
        identifiers = [t["identifier"] for t in payload["data_sources"]["tables"]]
        self.assertEqual(len(identifiers), len(gp.MIRROR_TABLES))

    def test_the_join_loses_nothing(self):
        """The join into one document must not drop or truncate a rule.

        Deliberately NOT `for i in INSTRUCTIONS: assertIn(i, document)` — that is
        tautological, since the document is built from INSTRUCTIONS and deleting a rule
        changes both sides together. (Verified: removing a rule leaves that form green.
        Removal is caught instead by the content tests below, which name each rule.)

        What this checks is the join itself: every rule's full text reaches the payload
        intact, byte-for-byte, and the count of bulleted lines matches the count of rules
        — so a join that silently truncated, deduplicated or reordered would fail here.
        """
        content = self.payload["instructions"]["text_instructions"][0]["content"]
        document = "".join(content)

        bulleted = [line for line in content if line.startswith("- ")]
        self.assertEqual(
            len(bulleted), len(gp.INSTRUCTIONS) + len(gp.STARTER_QUESTIONS),
            "the number of bulleted lines does not match the rules plus the starter "
            "questions, so the join dropped or duplicated something")

        # Full text, not a prefix: a truncating join would still pass a substring check
        # on a short rule.
        for instruction in gp.INSTRUCTIONS:
            self.assertIn(f"- {instruction}\n", document,
                          "a rule reached the payload altered or truncated")

        # And the document must not have collapsed to just its header.
        self.assertGreater(len(document), sum(len(i) for i in gp.INSTRUCTIONS),
                           "the joined document is shorter than its own inputs")


class TestInstructionsCoverTheAmbiguities(unittest.TestCase):
    """Instructions are the difference between a useful space and a confident liar.

    Each assertion here corresponds to a specific wrong answer that is available to a
    model reading only the schema.
    """

    def setUp(self):
        self.text = " ".join(gp.INSTRUCTIONS).lower()

    def test_states_the_units(self):
        """`hypothesized_value_mm` is $M/year. Nothing in the name says "per year", and
        a model that reads 125.4 as dollars is off by six orders of magnitude."""
        self.assertIn("millions", self.text)
        self.assertIn("per year", self.text)

    def test_forbids_joining_the_two_tables(self):
        """The most dangerous available mistake: both tables have `id`, so
        `use_cases.id = data_assets.id` returns rows and produces a fabricated
        use-case-to-source mapping with no error anywhere."""
        self.assertIn("cannot be joined", self.text)
        self.assertIn("never join on id", self.text)

    def test_explains_the_readiness_vocabulary(self):
        """Four values that are not a scale, one of which ('awaiting_prerequisites')
        means the data IS complete — the opposite of the natural reading."""
        for value in ("shovel_ready", "awaiting_prerequisites", "nearly_ready",
                      "blocked"):
            self.assertIn(value, self.text)

    def test_says_blocked_is_not_cancelled(self):
        self.assertIn("not cancelled", self.text)

    def test_says_which_statuses_count_as_usable(self):
        """'landed' sounds finished but does not count toward readiness."""
        self.assertIn("curated", self.text)
        self.assertIn("governed", self.text)

    def test_warns_against_double_counting(self):
        self.assertIn("double-count", self.text)

    def test_explains_that_domain_is_the_lob(self):
        self.assertIn("line of business", self.text)


class TestRefusesToOverwrite(unittest.TestCase):
    """A tuned space is unrecoverable if replaced, so this is a hard refusal."""

    def test_existing_space_id_blocks_provisioning(self):
        import asyncio

        async def run():
            original = gp.GENIE_SPACE_ID
            gp.GENIE_SPACE_ID = "01f189a42a8d1b3e975828e73077d9c3"
            try:
                with self.assertRaises(gp.GenieProvisionError) as caught:
                    await gp.provision(title="t", description="d", warehouse_id="w")
                # The route maps this specific wording to 409 rather than 422.
                self.assertIn("already set", str(caught.exception))
            finally:
                gp.GENIE_SPACE_ID = original

        asyncio.run(run())

    def test_refusal_happens_before_any_work(self):
        """It must short-circuit before touching the warehouse. Refusing only after a
        mirror rebuild would make a no-op request expensive."""
        source = (ROOT / "server" / "genie_provision.py").read_text()
        tree = ast.parse(source)
        function = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == "provision")
        # Find the statement index of the GENIE_SPACE_ID guard and of the columns read.
        guard_index = columns_index = None
        for index, statement in enumerate(function.body):
            dumped = ast.dump(statement)
            if guard_index is None and "GENIE_SPACE_ID" in dumped:
                guard_index = index
            if columns_index is None and "_mirror_columns" in dumped:
                columns_index = index
        self.assertIsNotNone(guard_index, "no GENIE_SPACE_ID guard in provision()")
        self.assertIsNotNone(columns_index)
        self.assertLess(guard_index, columns_index,
                        "provision() reads the warehouse before checking whether a "
                        "space already exists")

    def test_missing_warehouse_is_refused(self):
        import asyncio

        async def run():
            original = gp.GENIE_SPACE_ID
            gp.GENIE_SPACE_ID = ""
            try:
                with self.assertRaises(gp.GenieProvisionError) as caught:
                    await gp.provision(title="t", description="d", warehouse_id="")
                self.assertIn("warehouse", str(caught.exception).lower())
            finally:
                gp.GENIE_SPACE_ID = original

        asyncio.run(run())


class TestRoute(unittest.TestCase):
    """The HTTP surface: status codes a UI branches on, and the one step it cannot do."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server" / "routes" / "genie.py").read_text()

    def test_conflict_is_distinguished_from_bad_request(self):
        """409 vs 422 tells the UI whether to offer "use the existing space" or
        "fix this and retry"."""
        self.assertIn("409", self.source)
        self.assertIn("422", self.source)

    def test_mirror_failure_aborts_provisioning(self):
        """A space over missing tables looks configured and answers nothing. Creating it
        anyway would be worse than failing."""
        self.assertIn("mirror_to_uc", self.source)
        self.assertIn('if not mirror.get("ok")', self.source)

    def test_status_reports_whether_provisioning_is_possible(self):
        """So setup can render a button instead of a paragraph of instructions."""
        self.assertIn("can_provision", self.source)

    def test_provisioning_is_audited(self):
        self.assertIn('write_audit("genie_space"', self.source)

    def test_provisioning_is_rate_limited(self):
        """It rebuilds the mirror through the warehouse — not something to allow in a
        tight loop."""
        self.assertIn('@router.post("/provision", dependencies=[Depends(limiter(',
                      self.source)

    def test_reports_the_remaining_manual_step(self):
        """An app cannot rewrite its own app.yaml and redeploy, so the response has to
        say exactly what to set — otherwise the space exists and Ask still says
        unconfigured, which reads as a failure."""
        self.assertIn("next_step", (ROOT / "server" / "genie_provision.py").read_text())


class TestTheMirrorAgreesWithTheApp(unittest.TestCase):
    """The bug this file's whole premise predicted, found by actually asking Genie.

    Provisioning worked, the space answered fluently, and the number was wrong. Asked
    for the portfolio total it said $1,978.61M; every screen in the app says $3,498.25M.
    Neither system was broken — the mirror was selecting the STORED
    `hypothesized_value_json->>'mid_mm'`, which is the value computed against default
    assumptions and frozen at seed time, while the app recomputes every value through
    value_engine against the account's calibrated assumptions. A 77% discrepancy,
    reported confidently, in the one surface an executive is most likely to quote.

    So the rule is: the mirror must never compute value itself. It goes through the same
    engine as the UI, or the two answer different questions.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server" / "live.py").read_text()

    def test_mirror_uses_the_value_engine(self):
        self.assertIn("compute_value_range", self.source,
                      "the Genie mirror must recompute value through value_engine, not "
                      "read a stored figure — otherwise Genie reports a total no screen "
                      "in the app agrees with")

    def test_mirror_loads_the_calibrated_assumptions(self):
        """Recomputing with defaults would reproduce the same wrong number."""
        self.assertIn("load_assumptions", self.source)

    def test_mirror_does_not_read_the_frozen_mid(self):
        """The specific expression that caused it.

        Checked against the SQL string literals only, not the whole file: the comment
        explaining this bug necessarily quotes the expression, and a plain substring
        search matches its own documentation.
        """
        statements = re.findall(r'db\.fetch\("""(.*?)"""', self.source, re.S)
        self.assertTrue(statements, "no db.fetch queries found in live.py")
        for statement in statements:
            self.assertNotIn("mid_mm", statement,
                             "a mirror query reads the frozen mid_mm; that value "
                             "ignores calibrated assumptions")

    def test_realized_value_also_goes_through_the_engine(self):
        """realized_value_amount is only one of three ways realized value is set (there
        is also an override flag and a parameterized formula). Reading the column
        directly silently drops the other two."""
        self.assertIn("compute_realized", self.source)


class TestTheHandoffToDeploy(unittest.TestCase):
    """The response tells the operator to run a flag; that flag has to exist.

    Caught while writing this: `next_step` advertised
    `scripts/deploy.py --genie-space-id`, which did not exist — genie_space_id was only
    ever read from the deploy cache, so there was no way to set it at all. The message
    would have sent someone to an "unrecognized arguments" error at the one moment they
    had a working space id in hand.
    """

    @classmethod
    def setUpClass(cls):
        cls.deploy = (ROOT / "scripts" / "deploy.py").read_text()
        cls.next_step = gp.provision.__doc__ or ""
        source = (ROOT / "server" / "genie_provision.py").read_text()
        cls.message = re.search(r'"next_step": \((.*?)\),\n', source, re.S).group(1)

    def test_the_advertised_flag_exists(self):
        for flag in re.findall(r"--[a-z-]+", self.message):
            self.assertIn(f'"{flag}"', self.deploy,
                          f"the provision response tells the operator to pass {flag}, "
                          "which scripts/deploy.py does not accept")

    def test_the_advertised_env_var_is_written_by_deploy(self):
        """Setting it by hand in app.yaml works too, but deploy must not clobber it."""
        self.assertIn('("GENIE_SPACE_ID", "genie_space_id")', self.deploy)

    def test_the_flag_feeds_the_setting(self):
        """Declaring the argument is not enough — it has to reach the settings dict, or
        it parses and is silently discarded."""
        self.assertIn("args.genie_space_id or cache.get", self.deploy)

    def test_no_dead_bundle_variable(self):
        """databricks.yml must not declare a genie_space_id variable.

        Nothing interpolates ${var.genie_space_id}, so a variable there looks like the
        place to set the id and does nothing at all.
        """
        bundle = (ROOT / "databricks.yml").read_text()
        self.assertIsNone(re.search(r"^  genie_space_id:", bundle, re.M),
                          "databricks.yml declares genie_space_id but no template "
                          "consumes it — setting it there has no effect")


if __name__ == "__main__":
    unittest.main()
