"""Create the Genie space over the portfolio mirror, instead of asking for one.

WHY THIS EXISTS
---------------
Genie was the one capability an install could not finish on its own. Everything else —
Lakebase, the warehouse, the UC schemas, the grants, the seed — is handled by
scripts/deploy.py. For Genie the docs said: go to the workspace UI, create a space,
add the mirrored tables, paste these instructions, copy the id back into app.yaml,
redeploy. Six manual steps, in a different product, and until they are done the "Ask
your portfolio in natural language" feature is a placeholder.

The mirror tables already exist (see live.mirror_to_uc). So does the warehouse. The
space is the only missing piece and it is fully described by data this app already has.

WHAT IT CREATES
---------------
A space over the mirrored tables, with instructions that tell Genie the things it
cannot infer from the schema — that value is in $M per year, that "shovel-ready" means
data AND prerequisites, that a blocked use case is a data gap rather than a cancelled
project, that the two tables cannot be joined. Without those, Genie answers confidently
and wrongly.

WHY THE PAYLOAD IS BUILT BY HAND
--------------------------------
The installed SDK has no Genie space client, and the REST contract had to be established
by probing the live API one rejection at a time. Recorded here so nobody repeats it:

  * POST /api/2.0/genie/spaces requires `title`, `warehouse_id` and `serialized_space`,
    the last being a JSON *string*, not an object.
  * `serialized_space` is `{version: 2, data_sources, instructions, benchmarks?}`.
  * `text_instructions[].content` is an ARRAY OF STRINGS, not a string. Passing a string
    gives "Expected an array for content but found ...".
  * Every `text_instructions[]` entry needs an `id`: a lowercase 32-hex uuid with no
    hyphens. Omitting it gives "must be provided and non-empty".
  * `text_instructions` may contain AT MOST ONE item. One entry per rule is the obvious
    structure and it is rejected; the rules are one document, split into lines.
  * Three things must be SORTED, each enforced by its own error: `data_sources.tables`
    by identifier, every table's `column_configs` by column_name, and `text_instructions`
    by id. None of these hold by accident — MIRROR_TABLES is ordered for readability and
    information_schema returns columns by ordinal position.
  * `benchmarks` is OPTIONAL, and that decided the design. Each benchmark question needs
    both an id and at least one `answer` — "benchmark_question must have at least one
    answer" — where an answer is `{format: "SQL", content: [<sql>]}`. So seeding starter
    questions means shipping hand-written SQL that is asserted to be the right answer.
    See STARTER_QUESTIONS below for why that is not done.

The whole payload this module produces was validated against the live API before
shipping: created, read back, verified field by field, deleted.

WHY IT NEVER OVERWRITES
-----------------------
If GENIE_SPACE_ID is already set, provisioning is refused. A space accumulates
instructions and benchmark questions that someone tuned by hand; replacing it silently
would throw that away, and it is not recoverable from anything this app stores.
"""
from __future__ import annotations

import json
import logging
import uuid

from .config import (GENIE_MIRROR_CATALOG, GENIE_MIRROR_SCHEMA, GENIE_SPACE_ID,
                     get_oauth_token, get_workspace_host)

logger = logging.getLogger(__name__)

# The tables live.mirror_to_uc() materializes — exactly two. There is no `lobs` table:
# the LOB name is denormalized into use_cases.domain, which is why the instructions
# below have to explain what `domain` means. Kept in sync with that function, because a
# space pointing at a table the mirror does not create yields a Genie that answers every
# question with "I could not find that table".
MIRROR_TABLES = ("use_cases", "data_assets")

# Columns worth entity-matching, per table: the ones a user names in a question
# ("outage prediction", "Distribution", "governed"). Entity matching on a numeric column
# is noise, so only the text columns of the mirror DDL are listed.
_MATCHABLE = {
    "use_cases": ("title", "domain", "status", "readiness", "category",
                  "sub_vertical", "effort"),
    "data_assets": ("source_category", "vendor", "module", "ingestion_status",
                    "owning_domain", "origin"),
}

# What Genie cannot infer from the schema. Each of these was a wrong answer waiting to
# happen: the units are not in the column names, and the readiness vocabulary is this
# product's own.
INSTRUCTIONS = [
    "All value figures are in MILLIONS OF DOLLARS PER YEAR at full run-rate. A "
    "hypothesized_value_mm of 125.4 means $125.4M per year, not $125.40.",

    "'readiness' has exactly four values and they are not a simple scale. "
    "'shovel_ready' means every required data source is landed AND every prerequisite "
    "use case is already built. 'awaiting_prerequisites' means the DATA is complete but "
    "an upstream use case has not been built — it is a sequencing problem, not a data "
    "gap. 'nearly_ready' means at least half the required data is landed. 'blocked' "
    "means less than half is landed, which IS a data gap.",

    "A 'blocked' use case is not cancelled or rejected. It is a use case whose data is "
    "not yet available. Never describe blocked use cases as failed or abandoned.",

    "data_assets.ingestion_status is a progression: not_started, landed, curated, "
    "governed. Only 'curated' and 'governed' count as usable for a use case — 'landed' "
    "means the data has arrived but is not yet trustworthy enough to build on.",

    "When asked what to do next, prefer use cases with readiness = 'shovel_ready' "
    "ordered by hypothesized_value_mm descending. Those are the ones that can start "
    "immediately.",

    "use_cases.domain is the line of business that owns the use case (Distribution, "
    "Transmission, Customer, Generation, and so on), not a data domain. It is the LOB "
    "name denormalized into the row, which is why there is no separate LOB table.",

    "Sum hypothesized_value_mm only across use_cases, never across data_assets — a data "
    "source has no value of its own, it enables use cases that do. Summing both "
    "double-counts.",

    # Without this, Genie invents a join. Both tables have an `id`, so a plausible-looking
    # `use_cases.id = data_assets.id` query returns rows and a completely fictional answer.
    "use_cases and data_assets CANNOT be joined. There is no shared key — the `id` column "
    "in each table is its own primary key and the two are unrelated. Which sources a use "
    "case requires is not present in these tables. If asked to connect a specific use case "
    "to specific data sources, say that the mapping is not available here and point the "
    "user at the use-case detail page in AI Value Flywheel, which shows it. Never join on "
    "id, and never guess the relationship from a name.",
]

# Starter questions, shipped as an INSTRUCTION rather than as benchmarks.
#
# Benchmarks were the original plan and the API rejected the idea on inspection: a
# benchmark question requires at least one `answer`, and that answer is SQL asserted to
# be correct. Genie evaluates itself against it. Writing that SQL here would mean
# shipping six hand-authored queries as ground truth for a schema whose contents vary by
# install — and a benchmark whose "correct" answer is subtly wrong is worse than no
# benchmark, because it trains and scores the space against a mistake, silently.
#
# As an instruction they still do the useful half of the job: they show a new user what
# to ask, and they give the model concrete examples of the vocabulary in use. Nothing
# claims they are correctly answered.
STARTER_QUESTIONS = [
    "Which use cases are shovel-ready, ordered by annual value?",
    "What is the total annual value of the portfolio in millions?",
    "How many data sources are governed versus not started?",
    "Which use cases are blocked, and which line of business do they belong to?",
    "What is the highest-value use case that is still blocked?",
    "How many use cases are live or realizing value?",
]


class GenieProvisionError(RuntimeError):
    """Provisioning failed. The message is written to be shown to an operator."""


def _mirror_configured() -> bool:
    return bool(GENIE_MIRROR_CATALOG and GENIE_MIRROR_SCHEMA)


def _instruction_id() -> str:
    """A lowercase 32-hex id with no hyphens, which is what the API demands.

    Random per call rather than derived from the text: these ids are the space's own
    identity for an instruction, and a stable hash would make two provisioned spaces
    share ids for the same content — with no benefit, since nothing here ever updates an
    instruction in place.
    """
    return uuid.uuid4().hex


def build_serialized_space(table_columns: dict[str, list[str]]) -> str:
    """The `serialized_space` payload the create API requires.

    Every detail of this shape was established by probing the live API; see the module
    docstring. The two that are easy to get wrong and hard to diagnose: `content` is an
    array of strings, and each instruction needs a 32-hex `id`.
    """
    tables = []
    # Sorted by identifier, because the API requires it: "Invalid export proto:
    # data_sources.tables must be sorted by identifier". MIRROR_TABLES is ordered for
    # human readability (the important table first), which is the opposite of what this
    # needs, so the sort is applied here rather than by reordering that constant.
    for table in sorted(MIRROR_TABLES):
        columns = table_columns.get(table) or []
        matchable = _MATCHABLE.get(table, ())
        tables.append({
            "identifier": f"{GENIE_MIRROR_CATALOG}.{GENIE_MIRROR_SCHEMA}.{table}",
            # Also sorted, by the same rule: "column_configs must be sorted by
            # column_name". `columns` arrives in ordinal_position order from
            # information_schema, which is deliberate everywhere else and wrong here.
            "column_configs": [
                {"column_name": column,
                 "enable_format_assistance": True,
                 "enable_entity_matching": True}
                for column in sorted(c for c in columns if c in matchable)
            ],
        })

    # ONE text instruction, not one per rule: "text_instructions must contain at most
    # one item". A space's general instructions are a single document — which matches
    # what the API returns for a hand-built space (exactly one entry, whose `content` is
    # the document split into lines). So INSTRUCTIONS is a list for authoring and
    # testing convenience, and is joined into that one document here.
    content = ["AI Value Flywheel portfolio. Read these rules before answering.\n", "\n"]
    for text in INSTRUCTIONS:
        content.append(f"- {text}\n")
    content.append("\n")
    content.append("Typical questions this space is meant to answer:\n")
    content.extend(f"- {question}\n" for question in STARTER_QUESTIONS)

    # `benchmarks` is omitted entirely rather than sent empty: a benchmark requires an
    # asserted-correct SQL answer, and there is none to give honestly.
    return json.dumps({
        "version": 2,
        "data_sources": {"tables": tables},
        "instructions": {"text_instructions": [
            {"id": _instruction_id(), "content": content}]},
    })


async def _mirror_columns() -> dict[str, list[str]]:
    """Real column names per mirrored table, from the warehouse.

    Read rather than hardcoded: the mirror's column list has changed twice, and a space
    configured for a column that no longer exists is rejected at create time with an
    error that does not say which column.
    """
    from .enrichment import _ident
    from .lineage import run_sql

    # Same quoting as the mirror itself: backtick-quoted identifier, single-quoted
    # literal. A catalog named with a hyphen is legal in UC and would otherwise make
    # information_schema unreadable.
    catalog = _ident(GENIE_MIRROR_CATALOG)
    schema_literal = GENIE_MIRROR_SCHEMA.replace("'", "''")

    out: dict[str, list[str]] = {}
    for table in MIRROR_TABLES:
        fq = f"{GENIE_MIRROR_CATALOG}.{GENIE_MIRROR_SCHEMA}.{table}"
        result = await run_sql(
            f"SELECT column_name FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema_literal}' "
            f"AND table_name = '{table}' ORDER BY ordinal_position")
        if not result.get("ok"):
            raise GenieProvisionError(
                f"Could not read the columns of {fq}: {result.get('error')}. "
                "Run the Genie mirror first (POST /api/live/sync-genie) — the space "
                "has to point at tables that already exist.")
        out[table] = [row[0] for row in (result.get("rows") or [])]
        if not out[table]:
            raise GenieProvisionError(
                f"{fq} has no columns, which means the mirror has not run. "
                "POST /api/live/sync-genie first.")
    return out


async def provision(*, title: str, description: str, warehouse_id: str,
                    parent_path: str | None = None) -> dict:
    """Create the space and return its id. Never overwrites an existing one."""
    import aiohttp

    if GENIE_SPACE_ID:
        raise GenieProvisionError(
            f"GENIE_SPACE_ID is already set to {GENIE_SPACE_ID}. Refusing to create "
            "a second space: the existing one may carry instructions and benchmark "
            "questions that were tuned by hand, and this app does not store them. "
            "Clear GENIE_SPACE_ID in app.yaml first if you really want a new one.")
    if not _mirror_configured():
        raise GenieProvisionError(
            "GENIE_MIRROR_CATALOG and GENIE_MIRROR_SCHEMA must be set — the space is "
            "created over the mirrored portfolio tables.")
    if not warehouse_id:
        raise GenieProvisionError(
            "A SQL warehouse is required. Bind the sql-warehouse app resource.")

    try:
        columns = await _mirror_columns()
    except ValueError as exc:   # _ident rejects a name it cannot safely quote
        raise GenieProvisionError(
            f"The Genie mirror target is not a usable identifier: {exc}") from exc
    payload = {
        "title": title,
        "description": description,
        "warehouse_id": warehouse_id,
        "serialized_space": build_serialized_space(columns),
    }
    if parent_path:
        payload["parent_path"] = parent_path

    host = get_workspace_host()
    token = get_oauth_token()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{host}/api/2.0/genie/spaces",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            body = await response.text()
            if response.status >= 400:
                # The API's own message names the offending field, which is far more
                # useful than a generic failure — surface it verbatim.
                raise GenieProvisionError(
                    f"Genie refused to create the space (HTTP {response.status}): "
                    f"{body[:400]}")
            try:
                created = json.loads(body)
            except ValueError as exc:
                raise GenieProvisionError(
                    f"Genie returned a non-JSON response: {body[:200]}") from exc

    space_id = created.get("space_id")
    if not space_id:
        raise GenieProvisionError(
            f"Genie accepted the request but returned no space_id: {body[:200]}")

    logger.info("created Genie space %s over %s.%s", space_id,
                GENIE_MIRROR_CATALOG, GENIE_MIRROR_SCHEMA)
    return {
        "space_id": space_id,
        "title": created.get("title"),
        "tables": [f"{GENIE_MIRROR_CATALOG}.{GENIE_MIRROR_SCHEMA}.{t}"
                   for t in MIRROR_TABLES],
        # Rules, not instruction records: the API allows only one text_instruction, so
        # all of these are joined into a single document. Reporting "1 instruction"
        # would be technically true and useless to someone checking their space was
        # seeded properly.
        "rules": len(INSTRUCTIONS),
        "starter_questions": len(STARTER_QUESTIONS),
        "url": f"{host}/genie/rooms/{space_id}",
        # The one step this cannot do for itself: an app cannot rewrite its own
        # app.yaml and redeploy, so say exactly what to set.
        "next_step": (
            "Set GENIE_SPACE_ID in app.yaml to this space_id and redeploy "
            "(or pass --genie-space-id to scripts/deploy.py). Until then the Ask "
            "feature will still report Genie as unconfigured."),
    }
