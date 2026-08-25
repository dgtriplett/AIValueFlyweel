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


def confidence(
    *,
    readiness: str,
    ready_pct: float,
    required_total: int,
    required_ready: int,
    prereqs_total: int,
    prereqs_built: int,
    requirement_model: str,
    requires_locked: bool,
    pending_domains: list,
) -> dict:
    """Evidence confidence for the readiness answer.

    Readiness says "what state is this use case in?" Confidence says "how much
    should a user trust that state?" They are deliberately separate: a blocked use
    case with three well-modeled domain requirements can be a high-confidence
    blocked answer, while a shovel-ready use case with no requirements modeled is
    actually a low-confidence answer.
    """
    score = 50
    reasons: list[str] = []

    if required_total == 0:
        score = 30
        reasons.append("No required data needs are modeled, so readiness is provisional.")
    elif requires_locked:
        score = 90
        reasons.append("Data requirements were hand-curated and locked.")
    elif requirement_model == "domain":
        score = 82
        reasons.append("Readiness uses semantic data domains, allowing vendor substitution.")
    else:
        score = 68
        reasons.append("Readiness uses derived module-to-source requirements.")

    if required_total > 0 and required_ready == 0:
        score -= 14
        reasons.append("None of the required data needs are currently satisfied.")
    elif 0 < ready_pct < 1:
        score -= 6
        reasons.append("Some required data needs are still pending.")

    if pending_domains:
        score -= min(10, len(pending_domains) * 2)
        reasons.append("Unmet semantic data domains remain.")

    if prereqs_total > prereqs_built:
        score -= 4
        reasons.append("Direct prerequisite use cases are not all built.")

    if readiness == "shovel_ready" and required_total == 0:
        reasons.append("Shovel-ready because no requirements exist, not because data was proven.")

    score = max(0, min(100, int(round(score))))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"
    return {"confidence": level, "confidence_score": score, "confidence_reasons": reasons}


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


async def ready_assets(status_override: dict[int, str] | None = None) -> list[int]:
    """Asset ids that count as READY for the current account.

    One place decides this, because it is the input to every readiness answer in the
    app. Before accounts existed it was `da.ingestion_status IN (...)` inline in each
    query; per-account status made that wrong in eight places at once.

    Reads asset_status_by_account, which resolves the account's own row over the
    shared column, and falls back to the plain column on a pre-migration database so
    an un-upgraded install keeps working.

    `status_override` is applied LAST and wins: it is the caller's hypothesis
    ("suppose we landed asset 42"), and an override that lost to stored state would
    make the simulator silently report the present instead of the projection.
    """
    from . import accounts

    account_id = await accounts.current()
    statuses: dict[int, str] = {}
    if account_id is not None:
        try:
            rows = await db.fetch(
                "SELECT data_asset_id, ingestion_status FROM asset_status_by_account "
                "WHERE account_id = $1", account_id)
            statuses = {r["data_asset_id"]: r["ingestion_status"] for r in rows}
        except Exception:  # noqa: BLE001 - view absent before migration 009
            statuses = {}
    if not statuses:
        rows = await db.fetch("SELECT id, ingestion_status FROM data_assets")
        statuses = {r["id"]: r["ingestion_status"] for r in rows}

    if status_override:
        statuses.update(status_override)
    return [asset_id for asset_id, status in statuses.items()
            if status in READY_STATUSES]


async def domain_satisfaction_for(
    use_case_id: int,
    status_override: dict[int, str] | None = None,
) -> dict[int, bool]:
    """Return {domain_id: satisfied} for the REQUIRED domains of one use case.

    This is the SINGLE definition of the domain-satisfaction rule used by the
    readiness badge (see readiness_map's DOMAIN path): a required domain is
    satisfied when ANY asset serving it is curated/governed for this account.
    The detail drawer imports THIS helper instead of re-deriving the rule, so the
    per-domain "satisfied/pending" flags it renders can never drift from the
    numbers the badge shows.

    Uses the same `ready_assets` resolution (per-account status + what-if
    override) as readiness_map, and mirrors its LEFT JOIN so a required domain
    with no serving asset counts as UNSATISFIED (a real gap) rather than dropping
    out of the calculation.
    """
    ready_asset_ids = await ready_assets(status_override)
    rows = await db.fetch(
        """
        SELECT urd.domain_id,
               COALESCE(bool_or(asd.data_asset_id = ANY($2::int[])), false) AS satisfied
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = urd.domain_id
        WHERE urd.use_case_id = $1
          AND urd.necessity = 'required'
          AND COALESCE(dd.is_active, true) = true
        GROUP BY urd.domain_id
        """,
        use_case_id, ready_asset_ids,
    )
    return {r["domain_id"]: bool(r["satisfied"]) for r in rows}


async def readiness_map(
    status_override: dict[int, str] | None = None,
) -> dict[int, dict]:
    """Return {use_case_id: {readiness, ready_pct, required_total, required_ready,
    prereqs_total, prereqs_built, pending_prereqs:[{id,title}],
    requirement_model, pending_domains:[{name,label}]}}.

    Status is read PER ACCOUNT via asset_status_by_account, so two customers'
    readiness is computed from their own landed sources against the shared catalog.

    `status_override` maps data_asset_id -> hypothetical status and is what powers
    the what-if simulator: "if we landed the OMS, what turns shovel-ready?" It runs
    the real readiness logic against a projected world rather than duplicating the
    dual-path rules in a second implementation — a parallel copy would answer the
    hypothetical differently from the actual, which is worse than not having it.
    """
    # Which assets count as ready for THIS account, with any hypothetical override
    # applied. Resolved once here and passed into both path queries as an int[], so
    # the account rule and the what-if projection live in exactly one place instead
    # of being repeated in every WHERE clause.
    ready_asset_ids = await ready_assets(status_override)

    # --- MODULE path counts (CHUNK A semantics, unchanged) -----------------
    rows = await db.fetch(
        """
        SELECT uc.id AS use_case_id,
               uc.requires_locked,
               COUNT(ura.data_asset_id) FILTER (WHERE ura.criticality = 'required') AS required_total,
               COUNT(ura.data_asset_id) FILTER (
                   WHERE ura.criticality = 'required'
                     AND ura.data_asset_id = ANY($1::int[])
               ) AS required_ready
        FROM use_cases uc
        LEFT JOIN uc_requires_asset ura ON ura.use_case_id = uc.id
        GROUP BY uc.id, uc.requires_locked
        """, ready_asset_ids
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
                   COALESCE(bool_or(asd.data_asset_id = ANY($1::int[])), false) AS satisfied
            FROM uc_requires_domain urd
            JOIN data_domains dd ON dd.id = urd.domain_id
            LEFT JOIN asset_serves_domain asd ON asd.domain_id = urd.domain_id
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
        """, ready_asset_ids
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
        conf = confidence(
            readiness=label,
            ready_pct=pct,
            required_total=total,
            required_ready=ready,
            prereqs_total=p_total,
            prereqs_built=p_built,
            requirement_model=model,
            requires_locked=bool(r["requires_locked"]),
            pending_domains=pending_domains,
        )
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
            **conf,
        }
    return out


async def readiness_for(use_case_id: int) -> dict:
    m = await readiness_map()
    return m.get(use_case_id, {
        "readiness": "shovel_ready", "ready_pct": 1.0,
        "required_total": 0, "required_ready": 0,
        "prereqs_total": 0, "prereqs_built": 0, "pending_prereqs": [],
        "requirement_model": "module", "pending_domains": [],
        "confidence": "low", "confidence_score": 30,
        "confidence_reasons": [
            "No required data needs are modeled, so readiness is provisional.",
        ],
    })
