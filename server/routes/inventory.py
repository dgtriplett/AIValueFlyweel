"""Classification rules + BI/AI artifact inventory.

    GET/POST/PUT/DELETE /api/rules        naming-convention rules
    POST   /api/rules/test               dry-run against real discovered rows
    POST   /api/rules/seed               load the common conventions
    GET    /api/artifacts                what has been built on the platform
    POST   /api/artifacts/sync           discover artifacts from system tables
    PATCH  /api/artifacts/{id}           attribute one to a use case
    GET    /api/artifacts/unattributed   work the portfolio doesn't know about

WHY ARTIFACTS ARE SEPARATE FROM linked_databricks_assets
--------------------------------------------------------
`linked_databricks_assets` answers "which job proves this use case is live?" — a
deliberate, per-use-case link a human makes. This table is the whole estate swept
from system tables, most of which is unattributed.

The unattributed part is the point. A dashboard nobody claimed is either shadow work
the portfolio should know about, or something abandoned that is still costing money.
Neither is visible from the portfolio side.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import rules as rl
from ..common import current_user, rows_to_list, write_audit
from ..db import db
from ..lineage import run_sql, system_tables_available

router = APIRouter(tags=["inventory"])

# Rows per INSERT during an artifact sweep. Keeps each statement well inside any
# parameter limit while turning 500 round-trips into a handful.
_ARTIFACT_BATCH = 100


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
class RuleIn(BaseModel):
    dimension: str
    field: str
    match_type: str
    pattern: str
    value: str | None = None
    case_sensitive: bool = False
    priority: int = 100
    is_active: bool = True
    notes: str | None = None


class RuleTestIn(BaseModel):
    # Test against real discovered rows by default — a rule that works on invented
    # samples and fails on the actual estate is the failure mode worth avoiding.
    limit: int = Field(default=50, ge=1, le=500)
    samples: list[dict] = []


@router.get("/rules")
async def list_rules(dimension: str | None = None, include_inactive: bool = True):
    if dimension is not None and dimension not in rl.DIMENSIONS:
        raise HTTPException(422, f"dimension must be one of {list(rl.DIMENSIONS)}")
    clauses = []
    args: list = []
    if dimension:
        args.append(dimension)
        clauses.append(f"dimension = ${len(args)}")
    if not include_inactive:
        clauses.append("is_active = true")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.fetch(
        f"SELECT * FROM classification_rules {where} ORDER BY dimension, priority, id",
        *args)
    return {
        "rules": rows_to_list(rows),
        "vocabulary": {"dimensions": list(rl.DIMENSIONS), "fields": list(rl.FIELDS),
                       "match_types": list(rl.MATCH_TYPES)},
    }


@router.post("/rules")
async def create_rule(body: RuleIn, request: Request):
    try:
        rule = rl.validate_rule(body.model_dump())
    except rl.RuleError as exc:
        raise HTTPException(422, str(exc))
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO classification_rules
           (dimension, field, match_type, pattern, value, case_sensitive,
            priority, is_active, notes, origin, created_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'manual',$10) RETURNING *""",
        rule["dimension"], rule["field"], rule["match_type"], rule["pattern"],
        rule["value"], rule["case_sensitive"], rule["priority"],
        rule["is_active"], rule["notes"], actor)
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("classification_rule", row["id"], "create", actor, rule)
    return dict(row)


# NOTE: /rules/seed and /rules/test are declared BEFORE /rules/{rule_id}.
# FastAPI matches in declaration order, so a literal segment after a
# parameterized one is captured as the parameter and never reached.
@router.post("/rules/seed")
async def seed_rules(request: Request):
    """Load the conventions common to most workspaces. Idempotent."""
    actor = current_user(request)
    created = 0
    for rule in rl.SEED_RULES:
        existing = await db.fetchrow(
            "SELECT id FROM classification_rules WHERE dimension=$1 AND field=$2 "
            "AND match_type=$3 AND lower(pattern)=lower($4)",
            rule["dimension"], rule["field"], rule["match_type"], rule["pattern"])
        if existing:
            continue
        await db.execute(
            """INSERT INTO classification_rules
               (dimension, field, match_type, pattern, value, priority, notes,
                origin, created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'seed',$8)""",
            rule["dimension"], rule["field"], rule["match_type"], rule["pattern"],
            rule.get("value"), rule["priority"], rule.get("notes"), actor)
        created += 1
    await write_audit("classification_rule", None, "seed", actor, {"created": created})
    return {"created": created, "total_seeds": len(rl.SEED_RULES)}


@router.post("/rules/test")
async def test_rules(body: RuleTestIn):
    """Dry-run the active rules against real discovered rows.

    Uses the actual inventory rather than synthetic names, because a rule that
    works on invented samples and fails on the estate is exactly the problem this
    endpoint exists to catch. Falls back to caller-supplied samples when no
    inventory has been uploaded yet.
    """
    rows = await db.fetch(
        "SELECT * FROM classification_rules WHERE is_active = true "
        "ORDER BY dimension, priority, id")
    rules = rows_to_list(rows)
    if not rules:
        raise HTTPException(422, "No active rules to test. Add one, or POST "
                                "/api/rules/seed for the common conventions.")

    samples = body.samples
    source = "supplied"
    if not samples:
        from ..config import discovery_configured, atlas_fqn
        if discovery_configured():
            result = await run_sql(f"""
                SELECT workspace_id, catalog_name, schema_name, table_name, owner,
                       comment
                FROM {atlas_fqn('discovered_tables')}
                WHERE is_present = true
                LIMIT {int(body.limit)}
            """, timeout_s=40)
            if result["ok"]:
                columns = ["workspace_id", "catalog_name", "schema_name",
                           "table_name", "owner", "comment"]
                samples = [dict(zip(columns, row)) for row in result["rows"]]
                source = "discovered_tables"
    if not samples:
        raise HTTPException(
            422,
            "No rows to test against. Upload an inventory under Get started, or "
            "pass `samples` explicitly.")

    results = rl.test_rules(rules, samples)
    return {
        "sample_source": source,
        "rules_applied": len(rules),
        "results": results[:100],
        "summary": rl.summarize(results),
    }


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, request: Request):
    try:
        rule = rl.validate_rule(body.model_dump())
    except rl.RuleError as exc:
        raise HTTPException(422, str(exc))
    actor = current_user(request)
    row = await db.fetchrow(
        """UPDATE classification_rules SET dimension=$1, field=$2, match_type=$3,
           pattern=$4, value=$5, case_sensitive=$6, priority=$7, is_active=$8,
           notes=$9, updated_at=now() WHERE id=$10 RETURNING *""",
        rule["dimension"], rule["field"], rule["match_type"], rule["pattern"],
        rule["value"], rule["case_sensitive"], rule["priority"],
        rule["is_active"], rule["notes"], rule_id)
    if row is None:
        raise HTTPException(404, "Rule not found")
    await write_audit("classification_rule", rule_id, "update", actor, rule)
    return dict(row)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, request: Request):
    actor = current_user(request)
    result = await db.execute("DELETE FROM classification_rules WHERE id=$1", rule_id)
    await write_audit("classification_rule", rule_id, "delete", actor)
    return {"deleted": result is not None}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
class ArtifactPatchIn(BaseModel):
    use_case_id: int | None = None
    lob_id: int | None = None
    description: str | None = None


@router.get("/artifacts")
async def list_artifacts(
    artifact_type: str | None = None,
    unattributed_only: bool = False,
    limit: int = Query(200, ge=1, le=2000),
):
    clauses = ["a.is_present = true"]
    args: list = []
    if artifact_type:
        args.append(artifact_type)
        clauses.append(f"a.artifact_type = ${len(args)}")
    if unattributed_only:
        clauses.append("a.use_case_id IS NULL")
    args.append(limit)
    rows = await db.fetch(f"""
        SELECT a.*, uc.title AS use_case_title, l.name AS lob_name
        FROM artifacts a
        LEFT JOIN use_cases uc ON uc.id = a.use_case_id
        LEFT JOIN lobs l ON l.id = a.lob_id
        WHERE {' AND '.join(clauses)}
        ORDER BY a.artifact_type, a.name
        LIMIT ${len(args)}
    """, *args)

    stats = await db.fetchrow("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE use_case_id IS NULL) AS unattributed,
               count(DISTINCT artifact_type) AS types
        FROM artifacts WHERE is_present = true
    """)
    by_type = await db.fetch("""
        SELECT artifact_type, count(*) AS n,
               count(*) FILTER (WHERE use_case_id IS NULL) AS unattributed
        FROM artifacts WHERE is_present = true
        GROUP BY artifact_type ORDER BY n DESC
    """)
    return {
        "artifacts": rows_to_list(rows),
        "summary": dict(stats) if stats else {},
        "by_type": rows_to_list(by_type),
    }


@router.get("/artifacts/unattributed")
async def unattributed(limit: int = Query(50, ge=1, le=500)):
    """Artifacts no use case claims — the reason this inventory exists.

    Each is either shadow work the portfolio should know about, or something
    abandoned that still costs money. Recently-active ones are listed first because
    those are the ones that matter.
    """
    rows = await db.fetch("""
        SELECT * FROM artifacts
        WHERE is_present = true AND use_case_id IS NULL
        ORDER BY (last_run IS NULL), last_run DESC NULLS LAST,
                 run_count_30d DESC NULLS LAST
        LIMIT $1
    """, limit)
    items = rows_to_list(rows)
    active = [a for a in items if (a.get("run_count_30d") or 0) > 0]
    return {
        "artifacts": items,
        "summary": {
            "total": len(items),
            # Split matters: active-but-unclaimed is shadow work; inactive-and-
            # unclaimed is probably abandoned. Different conversations.
            "active_unclaimed": len(active),
            "inactive_unclaimed": len(items) - len(active),
        },
        "interpretation": "Active but unclaimed artifacts are usually work the "
                          "portfolio doesn't know about. Inactive ones are often "
                          "abandoned and still consuming budget.",
    }


@router.post("/artifacts/sync")
async def sync_artifacts(request: Request):
    """Discover artifacts from system tables. Read-only against Databricks.

    Each source is independent and a failure in one is reported rather than fatal,
    because system-table availability varies by workspace and partial coverage is
    much better than none.
    """
    actor = current_user(request)
    available = await system_tables_available()
    found: dict[str, int] = {}
    notes: list[str] = []

    async def ingest(kind: str, sql: str, mapper) -> None:
        result = await run_sql(sql, timeout_s=60)
        if not result["ok"]:
            notes.append(f"{kind}: {result.get('error') or 'unavailable'}")
            return

        records = []
        for raw in result["rows"]:
            record = mapper(raw)
            if record.get("name"):
                records.append(record)
        if not records:
            found[kind] = 0
            return

        # Batched rather than one statement per row. Each sweep can return 500
        # artifacts, and the pool holds 10 connections — 500 sequential round-trips
        # per source starved concurrent requests and made a repeated sync a
        # cheap way to tie up the app. UNNEST turns each batch into one statement.
        written = 0
        for start in range(0, len(records), _ARTIFACT_BATCH):
            batch = records[start:start + _ARTIFACT_BATCH]
            await db.execute("""
                INSERT INTO artifacts
                  (artifact_type, workspace_id, artifact_id, name, owner,
                   uc_catalog, uc_schema, last_run, run_count_30d, status)
                SELECT $1, w, a, n, o, c, s, lr, rc, st
                FROM unnest($2::text[], $3::text[], $4::text[], $5::text[],
                            $6::text[], $7::text[], $8::timestamptz[],
                            $9::int[], $10::text[])
                     AS t(w, a, n, o, c, s, lr, rc, st)
                ON CONFLICT (artifact_type, workspace_id, artifact_id) DO UPDATE SET
                  name=EXCLUDED.name, owner=EXCLUDED.owner,
                  last_run=EXCLUDED.last_run, run_count_30d=EXCLUDED.run_count_30d,
                  status=EXCLUDED.status, is_present=true, last_seen_at=now()
            """, kind,
                [r.get("workspace_id") for r in batch],
                [r.get("artifact_id") for r in batch],
                [r["name"] for r in batch],
                [r.get("owner") for r in batch],
                [r.get("uc_catalog") for r in batch],
                [r.get("uc_schema") for r in batch],
                [r.get("last_run") for r in batch],
                [r.get("run_count_30d") for r in batch],
                [r.get("status") for r in batch])
            written += len(batch)
        found[kind] = written

    if available.get("lakeflow_jobs"):
        # Data-relative window: "recent" means relative to the newest data the
        # historian holds, so a demo workspace with stale data still shows activity.
        await ingest("job", """
            WITH recent AS (
                SELECT job_id, count(*) AS runs, max(period_start_time) AS last_run
                FROM system.lakeflow.job_run_timeline
                WHERE period_start_time > (
                    SELECT max(period_start_time) - INTERVAL 30 DAYS
                    FROM system.lakeflow.job_run_timeline)
                GROUP BY job_id
            )
            SELECT j.job_id, j.name, j.creator_id, r.runs, r.last_run
            FROM system.lakeflow.jobs j
            LEFT JOIN recent r ON r.job_id = j.job_id
            WHERE j.name IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY j.job_id
                                       ORDER BY j.change_time DESC) = 1
            LIMIT 500
        """, lambda row: {
            "artifact_id": str(row[0]), "name": row[1], "owner": row[2],
            "run_count_30d": int(row[3]) if row[3] is not None else 0,
            "last_run": row[4],
        })
    else:
        notes.append("system.lakeflow.jobs not readable — jobs skipped.")

    if available.get("serving"):
        await ingest("serving_endpoint", """
            SELECT served_entity_name, count(*) AS calls, max(request_time) AS last_call
            FROM system.serving.endpoint_usage
            WHERE request_time > (SELECT max(request_time) - INTERVAL 30 DAYS
                                  FROM system.serving.endpoint_usage)
            GROUP BY served_entity_name
            LIMIT 200
        """, lambda row: {
            "artifact_id": str(row[0]), "name": row[0],
            "run_count_30d": int(row[1]) if row[1] is not None else 0,
            "last_run": row[2],
        })
    else:
        notes.append("system.serving.endpoint_usage not readable — endpoints skipped.")

    # Models come from the UC information schema rather than system tables.
    result = await run_sql("""
        SELECT catalog_name, schema_name, model_name, created_by, last_altered
        FROM system.information_schema.models
        LIMIT 500
    """, timeout_s=60)
    if result["ok"]:
        # Same batching as `ingest` above, for the same reason.
        models = [r for r in result["rows"] if r and r[2]]
        written = 0
        for start in range(0, len(models), _ARTIFACT_BATCH):
            batch = models[start:start + _ARTIFACT_BATCH]
            await db.execute("""
                INSERT INTO artifacts
                  (artifact_type, workspace_id, artifact_id, name, owner,
                   uc_catalog, uc_schema, last_modified)
                SELECT 'model', NULL, a, n, o, c, s, lm
                FROM unnest($1::text[], $2::text[], $3::text[], $4::text[],
                            $5::text[], $6::timestamptz[]) AS t(a, n, o, c, s, lm)
                ON CONFLICT (artifact_type, workspace_id, artifact_id) DO UPDATE SET
                  name=EXCLUDED.name, owner=EXCLUDED.owner,
                  last_modified=EXCLUDED.last_modified, is_present=true,
                  last_seen_at=now()
            """,
                [f"{r[0]}.{r[1]}.{r[2]}" for r in batch],
                [r[2] for r in batch], [r[3] for r in batch],
                [r[0] for r in batch], [r[1] for r in batch],
                [r[4] for r in batch])
            written += len(batch)
        found["model"] = written
    else:
        notes.append("system.information_schema.models not readable — models skipped.")

    total = sum(found.values())
    await write_audit("artifacts", None, "sync", actor,
                      {"found": found, "notes": len(notes)})
    return {
        "ok": True, "found": found, "total": total, "notes": notes,
        "available": available,
        "hint": ("Nothing was found. Grant the app's service principal SELECT on "
                 "the system schemas — Get started step 1 shows the GRANTs."
                 if total == 0 else
                 "Review /api/artifacts/unattributed for work the portfolio "
                 "doesn't know about."),
    }


@router.patch("/artifacts/{artifact_id}")
async def patch_artifact(artifact_id: int, body: ArtifactPatchIn, request: Request):
    """Attribute an artifact to a use case. Pins it against future syncs."""
    actor = current_user(request)
    if body.use_case_id is not None:
        exists = await db.fetchrow("SELECT id FROM use_cases WHERE id=$1",
                                   body.use_case_id)
        if exists is None:
            raise HTTPException(404, "Use case not found")
    row = await db.fetchrow("""
        UPDATE artifacts SET use_case_id=$1, lob_id=$2,
               description=COALESCE($3, description),
               mapped_by='manual', is_user_edited=true, last_seen_at=now()
        WHERE id=$4 RETURNING *
    """, body.use_case_id, body.lob_id, body.description, artifact_id)
    if row is None:
        raise HTTPException(404, "Artifact not found")
    await write_audit("artifact", artifact_id, "attribute", actor, body.model_dump())
    return dict(row)
