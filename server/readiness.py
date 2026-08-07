"""Readiness scoring for use cases.

A use case is "shovel_ready" only when BOTH conditions hold:
  1. data_ready    : all its REQUIRED data needs are satisfied by curated/
                     governed sources (see DUAL PATH below).
  2. prereqs_built : all its DIRECT prerequisite use cases (upstream in
                     uc_enables_uc where to_use_case_id = this) are live or
                     value_realized. No prerequisites => trivially true.

Classification (4 states):
  - shovel_ready           : data_ready AND prereqs_built.
  - awaiting_prerequisites : data_ready AND NOT prereqs_built — the data is there
                             but N upstream use cases must be built first. NOT
                             shovel-ready, and NOT a data "blocked" state.
  - nearly_ready           : data partially there (>= 50% of required governed).
  - blocked                : < 50% of required data governed (a true data gap).

DUAL PATH — how "required data" is resolved
-------------------------------------------
Two requirement models coexist (see server/migrations/002_domains.sql):

  MODULE path (CHUNK A) : use_case --requires--> data_asset (a specific module).
                          Satisfied when that exact asset is curated/governed.

  DOMAIN path (CHUNK B) : use_case --requires--> data_domain <--serves-- asset.
                          A required domain is satisfied when ANY asset mapped
                          to it is curated/governed. This is what stops vendor
                          substitution (Maximo instead of SAP PM) from showing a
                          false gap.

Resolution is PER USE CASE, not global, and precedence is:

  1. requires_locked = true  -> MODULE path. A human hand-curated this use case's
     module requirements, so their precision wins. This matters because domains
     trade precision for robustness: `process_historian_timeseries` is served by
     the turbine, boiler, and feedwater modules alike, so for a use case that
     genuinely needs *turbine* tags the domain rule is too permissive. Locking
     the module requirements is the escape hatch.
  2. has required domains     -> DOMAIN path (the vendor-substitution win).
  3. otherwise                -> MODULE path.

That keeps the 693 seeded module edges, the deterministic remap in
scripts/derive_requires.py, and the requires_locked override all working
untouched, and makes a fresh install with an unpopulated domain layer behave
identically to CHUNK A.

`requirement_model` in the output says which path a use case used, so the UI can
explain *why* something is blocked without the caller having to guess.
"""
import json

from .db import db

READY_STATUSES = ("curated", "governed")
BUILT_STATUSES = ("live", "value_realized")


def _status_list(statuses: tuple[str, ...]) -> str:
    """Render a status tuple as a SQL IN list: "'live','value_realized'".

    Two route modules had the BUILT list typed out by hand in SQL while importing
    READY_STATUSES as a constant from here — so changing BUILT_STATUSES would have
    left those queries answering the old question, and the disagreement would
    surface as a use case reading "blocked" on one screen and "shovel-ready" on
    another. This makes the constant the only definition.

    Safe to interpolate: the values come from a module constant, never from a
    request. The assertion makes that a guarantee rather than a convention, since
    the result goes straight into query text.

    NOTE: only the BUILT list is threaded through today. The `'curated','governed'`
    literals in the domain queries were left alone deliberately — several of those
    SQL strings also contain `'{}'::text[]`, and converting them to f-strings breaks
    that literal. Mechanically rewriting eight query strings to remove a duplicated
    two-element tuple is a worse trade than leaving it; the redundancy test below
    covers the risk instead.
    """
    for status in statuses:
        assert status.replace("_", "").isalnum(), f"unsafe status literal: {status!r}"
    return ",".join(f"'{status}'" for status in statuses)


BUILT_SQL_LIST = _status_list(BUILT_STATUSES)
READY_SQL_LIST = _status_list(READY_STATUSES)


def classify(ready: int, total: int, prereqs_total: int, prereqs_built: int) -> tuple[str, float]:
    """Return (label, data_ready_pct)."""
    data_pct = 1.0 if total == 0 else ready / total
    data_ready = data_pct >= 1.0
    prereqs_ok = prereqs_built >= prereqs_total  # no prereqs => 0>=0 => True
    if data_ready and prereqs_ok:
        return "shovel_ready", data_pct
    if data_ready and not prereqs_ok:
        return "awaiting_prerequisites", data_pct
    if data_pct >= 0.5:
        return "nearly_ready", data_pct
    return "blocked", data_pct


def _as_list(value) -> list:
    """jsonb_agg arrives as a parsed list when asyncpg has a jsonb codec
    registered, and as a JSON string when it doesn't. Accept both."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


async def readiness_map() -> dict[int, dict]:
    """Return {use_case_id: {readiness, ready_pct, required_total, required_ready,
    prereqs_total, prereqs_built, pending_prereqs:[{id,title}],
    requirement_model, pending_domains:[{name,label}]}}."""
    # --- MODULE path counts (CHUNK A semantics, unchanged) -----------------
    rows = await db.fetch(
        """
        SELECT uc.id AS use_case_id,
               uc.requires_locked,
               COUNT(ura.data_asset_id) FILTER (WHERE ura.criticality = 'required') AS required_total,
               COUNT(ura.data_asset_id) FILTER (
                   WHERE ura.criticality = 'required'
                     AND da.ingestion_status IN ('curated','governed')
               ) AS required_ready
        FROM use_cases uc
        LEFT JOIN uc_requires_asset ura ON ura.use_case_id = uc.id
        LEFT JOIN data_assets da ON da.id = ura.data_asset_id
        GROUP BY uc.id, uc.requires_locked
        """
    )

    # --- DOMAIN path counts (CHUNK B) --------------------------------------
    # A required domain is satisfied when ANY serving asset is curated/governed.
    # bool_or over the serving assets gives per-domain satisfaction; the outer
    # aggregate counts satisfied vs total required domains per use case.
    # The LEFT JOINs matter: a domain with no serving asset at all must still
    # count toward the denominator as UNSATISFIED (that is a real gap), not
    # silently drop out of the calculation.
    domain_rows = await db.fetch(
        """
        WITH domain_satisfaction AS (
            SELECT urd.use_case_id,
                   urd.domain_id,
                   dd.name  AS domain_name,
                   dd.label AS domain_label,
                   COALESCE(bool_or(da.ingestion_status IN ('curated','governed')), false) AS satisfied
            FROM uc_requires_domain urd
            JOIN data_domains dd ON dd.id = urd.domain_id
            LEFT JOIN asset_serves_domain asd ON asd.domain_id = urd.domain_id
            LEFT JOIN data_assets da ON da.id = asd.data_asset_id
            WHERE urd.necessity = 'required'
              AND COALESCE(dd.is_active, true) = true
            GROUP BY urd.use_case_id, urd.domain_id, dd.name, dd.label
        )
        SELECT use_case_id,
               COUNT(*)                          AS required_total,
               COUNT(*) FILTER (WHERE satisfied) AS required_ready,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object('name', domain_name, 'label', domain_label)
                       ORDER BY domain_label
                   ) FILTER (WHERE NOT satisfied),
                   '[]'::jsonb
               )                                 AS pending_domains
        FROM domain_satisfaction
        GROUP BY use_case_id
        """
    )
    by_domain = {r["use_case_id"]: r for r in domain_rows}

    # Direct prerequisites: edges from_use_case_id --enables--> to_use_case_id.
    # For a target UC, its prerequisites are the "from" nodes; they are "built"
    # when their status is live/value_realized.
    prereq_rows = await db.fetch(
        """
        SELECT e.to_use_case_id AS uc_id,
               up.id AS prereq_id, up.title AS prereq_title,
               (up.status IN ('live','value_realized')) AS built
        FROM uc_enables_uc e
        JOIN use_cases up ON up.id = e.from_use_case_id
        """
    )
    prereqs: dict[int, list] = {}
    for r in prereq_rows:
        prereqs.setdefault(r["uc_id"], []).append(
            {"id": r["prereq_id"], "title": r["prereq_title"], "built": r["built"]})

    out: dict[int, dict] = {}
    for r in rows:
        uc_id = r["use_case_id"]
        # Per-use-case path selection (precedence documented in the module
        # docstring): a hand-curated module requirement set wins over the more
        # permissive domain rule; otherwise use domains when declared.
        drow = by_domain.get(uc_id)
        if not r["requires_locked"] and drow and int(drow["required_total"] or 0) > 0:
            total = int(drow["required_total"])
            ready = int(drow["required_ready"] or 0)
            model = "domain"
            pending_domains = _as_list(drow["pending_domains"])
        else:
            total = int(r["required_total"] or 0)
            ready = int(r["required_ready"] or 0)
            model = "module"
            pending_domains = []

        plist = prereqs.get(uc_id, [])
        p_total = len(plist)
        p_built = sum(1 for p in plist if p["built"])
        pending = [{"id": p["id"], "title": p["title"]} for p in plist if not p["built"]]
        label, pct = classify(ready, total, p_total, p_built)
        out[uc_id] = {
            "readiness": label,
            "ready_pct": round(pct, 4),
            "required_total": total,
            "required_ready": ready,
            "prereqs_total": p_total,
            "prereqs_built": p_built,
            "pending_prereqs": pending,
            "requirement_model": model,
            "pending_domains": pending_domains,
        }
    return out


async def readiness_for(use_case_id: int) -> dict:
    m = await readiness_map()
    return m.get(use_case_id, {
        "readiness": "shovel_ready", "ready_pct": 1.0,
        "required_total": 0, "required_ready": 0,
        "prereqs_total": 0, "prereqs_built": 0, "pending_prereqs": [],
        "requirement_model": "module", "pending_domains": [],
    })
