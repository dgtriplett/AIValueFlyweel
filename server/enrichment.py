"""AI enrichment of discovered inventory via the staged `ai_query()` pattern.

WHY STAGED, AND NOT ONE STATEMENT
---------------------------------
The obvious implementation is a single MERGE whose source CTE calls `ai_query()`
and parses the JSON response into several columns:

    raw     : ai_query(...)                              AS resp
    parsed  : from_json(resp.result, 'a STRING, b STRING, c STRING') AS c
    MERGE   : SET t.a = src.c.a, t.b = src.c.b, t.c = src.c.c

That is 3-4x more expensive than it looks. Each reference to `c.<field>` lets the
optimizer push the `from_json` projection — and the `ai_query` beneath it — down
separately, so the model is invoked ONCE PER OUTPUT FIELD.

So we split it in two:

  Stage 1  evaluate `ai_query()` exactly once per candidate row and persist the
           RAW response to a staging Delta table.
  Stage 2  MERGE from that table, parsing the stored text. No LLM calls at all.

Beyond cost this buys: visible progress (`SELECT count(*)` on staging climbs),
resumability (a failed MERGE doesn't re-pay for inference), and a cheap re-parse
if the JSON shape needs adjusting.

Everything here builds SQL strings for the Statement Execution API. That API has
no bind parameters, so values are interpolated — `sql_str()` is the single
escaping choke point and every interpolated value must pass through it. Identifiers
come from validated config (never user input) and are backtick-quoted.
"""
from __future__ import annotations

import json

from .config import AI_QUERY_ENDPOINT, ATLAS_CATALOG, ATLAS_SCHEMA, atlas_fqn

# Staging table for stage 1. Deliberately NOT dropped after a run so stage 2 can
# be replayed without paying for inference again.
STAGING_TABLE = "table_enrichment_staging"

# Cap on how much column context we feed per table. Column names are the single
# strongest classification signal, but a 400-column fact table would blow the
# prompt budget for no extra accuracy.
_MAX_COLUMN_CHARS = 600
_MAX_COMMENT_CHARS = 300


def sql_str(value) -> str:
    """Render a Python value as a SQL literal, escaping quotes.

    The Statement Execution API takes a complete statement with no parameter
    binding, so this is the ONLY sanctioned way to put a value into generated
    SQL. Doubling single quotes is the correct escape for Spark SQL string
    literals; backslashes are not escape characters there by default.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    """Backtick-quote an identifier, rejecting anything that could break out.

    Identifiers here come from validated configuration rather than request bodies,
    so this is defence in depth: a backtick in a catalog name would otherwise let
    the name terminate its own quoting.
    """
    text = str(name or "")
    if not text or "`" in text:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f"`{text}`"


def staging_fqn() -> str:
    return atlas_fqn(STAGING_TABLE)


def discovery_schema_fqn() -> str:
    return f"{_ident(ATLAS_CATALOG)}.{_ident(ATLAS_SCHEMA)}"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
# Strict JSON schema so the model returns parseable output instead of prose with
# a code fence. Supported by ai_query's responseFormat on FMAPI endpoints.
_TABLE_RESPONSE_SCHEMA = json.dumps({
    "type": "json_schema",
    "json_schema": {
        "name": "table_enrichment",
        "schema": {
            "type": "object",
            "properties": {
                "business_name": {"type": "string"},
                "ai_definition": {"type": "string"},
                "source_system": {"type": "string"},
            },
            "required": ["business_name", "ai_definition", "source_system"],
        },
        "strict": True,
    },
})


def build_table_prompt_expr(company_name: str, canonicals: list[str]) -> str:
    """SQL expression producing the per-row prompt.

    `canonicals` is supplied as a closed vocabulary so `source_system` comes back
    already near-canonical — which shrinks the long tail the normalization
    cascade has to resolve later. It is a hint, not a guarantee: the model can
    still answer something else, which is why normalization runs regardless.
    """
    vocabulary = ", ".join(canonicals[:60]) if canonicals else "no predefined list"
    preamble = (
        f"You are a data catalog expert for {company_name}, a Power & Utilities "
        "organization. Given a table's identity and column list, return JSON with: "
        "business_name (a concise human-readable name), ai_definition (1-2 "
        "sentences on what business data this table holds), and source_system "
        "(the operational system this data originates from). "
        f"For source_system prefer one of: {vocabulary}. "
        "Use 'Internal' if it is a derived/analytical table with no external source."
    )
    # CONCAT over columns so Spark builds the prompt per row; COALESCE keeps a
    # NULL column from nulling the entire concatenation.
    return (
        "CONCAT("
        f"{sql_str(preamble)}, "
        "' catalog=', t.catalog_name, "
        "' schema=', t.schema_name, "
        "' table=', t.table_name, "
        "' type=', COALESCE(t.table_type, ''), "
        "' format=', COALESCE(t.data_format, ''), "
        f"' comment=', COALESCE(LEFT(t.comment, {_MAX_COMMENT_CHARS}), ''), "
        f"' columns=', COALESCE(LEFT(t.column_summary, {_MAX_COLUMN_CHARS}), ''), "
        "' schema_context=', COALESCE(LEFT(s.ai_definition, 200), '')"
        ")"
    )


# ---------------------------------------------------------------------------
# Stage 1 — evaluate ai_query once per candidate, persist raw responses
# ---------------------------------------------------------------------------
def build_staging_sql(
    *,
    company_name: str,
    canonicals: list[str],
    endpoint: str | None = None,
    max_rows: int | None = None,
    only_schema: str | None = None,
) -> str:
    """CREATE OR REPLACE the staging table with one raw LLM response per row.

    Candidate selection excludes rows a human has edited and rows already
    enriched, so a re-run only pays for genuinely new tables.
    """
    endpoint = endpoint or AI_QUERY_ENDPOINT
    tables = atlas_fqn("discovered_tables")
    schemas = atlas_fqn("discovered_schemas")
    staging = staging_fqn()

    filters = [
        "t.is_present = true",
        "COALESCE(t.is_user_edited, false) = false",
        "(t.ai_definition IS NULL OR t.ai_definition = '' "
        " OR t.source_system_raw IS NULL OR t.source_system_raw = '')",
    ]
    if only_schema:
        filters.append(f"t.schema_name = {sql_str(only_schema)}")
    limit = f"LIMIT {int(max_rows)}" if max_rows else ""

    return f"""
        CREATE OR REPLACE TABLE {staging}
        USING DELTA
        COMMENT 'AI Value Flywheel AI enrichment staging: one raw ai_query() response per candidate table. Built by server/enrichment.py. Safe to drop; rebuilding re-pays for inference.'
        AS
        WITH candidates AS (
            SELECT t.workspace_id, t.catalog_name, t.schema_name, t.table_name,
                   t.table_type, t.data_format, t.comment, t.column_summary
            FROM {tables} t
            WHERE {' AND '.join(filters)}
            {limit}
        ),
        -- One schema row per (workspace, catalog, schema). Deduped BEFORE the
        -- join: duplicate schema rows would fan out the candidate set and
        -- multiply inference cost by the duplication factor.
        schema_ctx AS (
            SELECT workspace_id, catalog_name, schema_name, ai_definition
            FROM (
                SELECT workspace_id, catalog_name, schema_name, ai_definition,
                       ROW_NUMBER() OVER (
                           PARTITION BY workspace_id, catalog_name, schema_name
                           ORDER BY LENGTH(COALESCE(ai_definition, '')) DESC
                       ) AS rn
                FROM {schemas}
            )
            WHERE rn = 1
        )
        SELECT
            t.workspace_id, t.catalog_name, t.schema_name, t.table_name,
            -- failOnError => false wraps the output in
            -- STRUCT<result STRING, errorMessage STRING> so one bad row cannot
            -- abort a multi-thousand-row run.
            ai_query(
                {sql_str(endpoint)},
                {build_table_prompt_expr(company_name, canonicals)},
                responseFormat => {sql_str(_TABLE_RESPONSE_SCHEMA)},
                modelParameters => named_struct('max_tokens', 512, 'temperature', 0.0),
                failOnError => false
            ) AS resp
        FROM candidates t
        LEFT JOIN schema_ctx s
            ON  s.workspace_id  = t.workspace_id
            AND s.catalog_name  = t.catalog_name
            AND s.schema_name   = t.schema_name
    """


def build_staging_stats_sql() -> str:
    """Row/success/error counts for the staging table — drives the progress UI."""
    return f"""
        SELECT
            COUNT(*)                                                        AS rows,
            SUM(CASE WHEN resp.result IS NOT NULL AND resp.result <> ''
                     THEN 1 ELSE 0 END)                                     AS rows_ok,
            SUM(CASE WHEN resp.errorMessage IS NOT NULL AND resp.errorMessage <> ''
                     THEN 1 ELSE 0 END)                                     AS rows_error
        FROM {staging_fqn()}
    """


# ---------------------------------------------------------------------------
# Stage 2 — MERGE parsed responses back. No LLM calls.
# ---------------------------------------------------------------------------
_JSON_SCHEMA_DDL = "business_name STRING, ai_definition STRING, source_system STRING"


def build_merge_sql() -> str:
    """MERGE the parsed staging rows into discovered_tables.

    Three defences, each for a failure seen in practice:
      - strip markdown fences: a model may still wrap JSON in ``` despite a
        strict responseFormat.
      - drop rows whose definition parsed empty, so a failed generation doesn't
        overwrite a good earlier value with NULL.
      - de-dupe on the merge key: Delta raises
        DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE if the source has
        two rows for one target row, which staging can if it was built when the
        inventory still held duplicates.
    """
    tables = atlas_fqn("discovered_tables")
    staging = staging_fqn()
    return f"""
        MERGE INTO {tables} AS target
        USING (
            WITH cleaned AS (
                SELECT workspace_id, catalog_name, schema_name, table_name,
                       REGEXP_REPLACE(
                           REGEXP_REPLACE(resp.result, '^```[a-zA-Z]*\\\\n?', ''),
                           '\\\\n?```$', ''
                       ) AS payload
                FROM {staging}
                WHERE resp.result IS NOT NULL AND resp.result <> ''
            ),
            parsed AS (
                SELECT workspace_id, catalog_name, schema_name, table_name,
                       from_json(TRIM(payload), '{_JSON_SCHEMA_DDL}') AS c
                FROM cleaned
            ),
            valid AS (
                SELECT * FROM parsed
                WHERE c.ai_definition IS NOT NULL AND c.ai_definition <> ''
            )
            SELECT workspace_id, catalog_name, schema_name, table_name, c
            FROM valid
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY workspace_id, catalog_name, schema_name, table_name
                ORDER BY LENGTH(COALESCE(c.ai_definition, '')) DESC
            ) = 1
        ) AS src
        ON  target.workspace_id = src.workspace_id
        AND target.catalog_name = src.catalog_name
        AND target.schema_name  = src.schema_name
        AND target.table_name   = src.table_name
        WHEN MATCHED AND COALESCE(target.is_user_edited, false) = false THEN UPDATE SET
            target.business_name    = src.c.business_name,
            target.ai_definition    = src.c.ai_definition,
            target.source_system_raw = src.c.source_system
    """


def build_prune_staging_sql() -> str:
    """Delete staging rows whose table is gone from the inventory.

    Keeps a `--skip-staging` replay cheap. Trade-off: a key later re-added has to
    pay for inference again, which is why pruning is opt-in.
    """
    return f"""
        DELETE FROM {staging_fqn()} AS s
        WHERE NOT EXISTS (
            SELECT 1 FROM {atlas_fqn('discovered_tables')} t
            WHERE t.workspace_id = s.workspace_id
              AND t.catalog_name = s.catalog_name
              AND t.schema_name  = s.schema_name
              AND t.table_name   = s.table_name
        )
    """


# ---------------------------------------------------------------------------
# Schema-level enrichment (far fewer rows; single-stage is fine)
# ---------------------------------------------------------------------------
_SCHEMA_RESPONSE_SCHEMA = json.dumps({
    "type": "json_schema",
    "json_schema": {
        "name": "schema_enrichment",
        "schema": {
            "type": "object",
            "properties": {
                "business_name": {"type": "string"},
                "ai_definition": {"type": "string"},
            },
            "required": ["business_name", "ai_definition"],
        },
        "strict": True,
    },
})


def build_schema_enrichment_sql(company_name: str, endpoint: str | None = None,
                                max_rows: int | None = None) -> str:
    """Enrich discovered_schemas in one statement.

    Schemas number in the hundreds where tables number in the tens of thousands,
    so the staged split isn't worth its complexity here — but we still write to a
    single struct column and parse once, so the projection can't be pushed down
    into repeated inference.
    """
    endpoint = endpoint or AI_QUERY_ENDPOINT
    schemas = atlas_fqn("discovered_schemas")
    limit = f"LIMIT {int(max_rows)}" if max_rows else ""
    preamble = (
        f"You are a data catalog expert for {company_name}, a Power & Utilities "
        "organization. Given a Unity Catalog schema and a sample of its table "
        "names, return JSON with business_name (concise human-readable name) and "
        "ai_definition (1-2 sentences on what this schema contains and which "
        "business function it serves)."
    )
    return f"""
        MERGE INTO {schemas} AS target
        USING (
            WITH candidates AS (
                SELECT s.workspace_id, s.catalog_name, s.schema_name,
                       s.comment, s.table_count,
                       (SELECT CONCAT_WS(', ', COLLECT_LIST(t.table_name))
                        FROM (SELECT table_name FROM {atlas_fqn('discovered_tables')} it
                              WHERE it.workspace_id = s.workspace_id
                                AND it.catalog_name = s.catalog_name
                                AND it.schema_name  = s.schema_name
                              LIMIT 40) t
                       ) AS sample_tables
                FROM {schemas} s
                WHERE s.is_present = true
                  AND COALESCE(s.is_user_edited, false) = false
                  AND (s.ai_definition IS NULL OR s.ai_definition = '')
                {limit}
            ),
            enriched AS (
                SELECT workspace_id, catalog_name, schema_name,
                       ai_query(
                           {sql_str(endpoint)},
                           CONCAT({sql_str(preamble)},
                                  ' catalog=', catalog_name,
                                  ' schema=', schema_name,
                                  ' comment=', COALESCE(LEFT(comment, 200), ''),
                                  ' table_count=', CAST(table_count AS STRING),
                                  ' tables=', COALESCE(LEFT(sample_tables, 800), '')),
                           responseFormat => {sql_str(_SCHEMA_RESPONSE_SCHEMA)},
                           modelParameters => named_struct('max_tokens', 400, 'temperature', 0.0),
                           failOnError => false
                       ) AS resp
                FROM candidates
            )
            SELECT workspace_id, catalog_name, schema_name,
                   from_json(TRIM(REGEXP_REPLACE(
                       REGEXP_REPLACE(resp.result, '^```[a-zA-Z]*\\\\n?', ''),
                       '\\\\n?```$', '')), 'business_name STRING, ai_definition STRING') AS c
            FROM enriched
            WHERE resp.result IS NOT NULL AND resp.result <> ''
        ) AS src
        ON  target.workspace_id = src.workspace_id
        AND target.catalog_name = src.catalog_name
        AND target.schema_name  = src.schema_name
        WHEN MATCHED AND COALESCE(target.is_user_edited, false) = false
                     AND src.c.ai_definition IS NOT NULL
                     AND src.c.ai_definition <> '' THEN UPDATE SET
            target.business_name = src.c.business_name,
            target.ai_definition = src.c.ai_definition
    """
