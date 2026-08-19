"""Portfolio snapshots — the trajectory, not just today's number.

WHY THIS MODULE IS SEPARATE FROM ITS ROUTE
------------------------------------------
`capture()` is called from three places: the manual button, the confirm executors that
apply research or land a source, and the seed. Putting it in the route module would
mean those callers importing a route to write a row, which is how a route module ends
up being imported by everything and becoming impossible to change.

WHY A SNAPSHOT IS DENORMALIZED
------------------------------
It stores computed figures, not foreign keys. A snapshot must stay readable after a use
case is renamed, a source is re-scoped, or an assumption is recalibrated — if it were a
view over current state, recalibrating an assumption would retroactively change every
historical point and the trend would be a straight line by construction. This is a
ledger of what was true when it was captured.

WHY FAILURES ARE SWALLOWED
--------------------------
`capture_quietly()` never raises. It is called from the tail of a confirm executor that
has already applied a real change, and failing there would surface as "your change did
not work" when in fact it did — the only thing lost is a point on a chart. The failure
is logged at warning so a missing trend is diagnosable.
"""
from __future__ import annotations

import json
import logging

from .db import db
from . import portfolio
from .readiness import READY_STATUSES, readiness_map
from .value_engine import compute_realized, compute_value_range, load_assumptions

logger = logging.getLogger(__name__)

# Reasons used by the callers today. Not a database constraint (see the migration),
# but named here so callers share spellings instead of inventing 'source-landed' and
# 'source_landed' independently.
REASON_MANUAL = "manual"
REASON_ASSUMPTIONS = "assumptions_applied"
REASON_RESEARCH = "research_applied"
REASON_SOURCE = "source_landed"
REASON_STATUS = "status_changed"
REASON_SEED = "seed"


async def compute_metrics() -> dict:
    """The portfolio's headline figures right now, for the current account.

    Reads through readiness_map(), so the numbers are the same ones the UI shows. A
    separate aggregation here would drift from the portfolio view, and a trend that
    disagrees with the current screen is worse than no trend.
    """
    assumptions = await load_assumptions()
    readiness = await readiness_map()

    membership_expr, params = await portfolio.select_membership_expression("uc")
    rows = await db.fetch(f"""
        SELECT id, status, in_portfolio, hypothesized_value_json, realized_value_json,
               realized_override_enabled, realized_override_amount,
               realized_override_note, {membership_expr} AS account_in_portfolio
        FROM use_cases uc
    """, *params)

    total = buildable = realized = 0.0
    live = 0
    portfolio_count = 0
    for row in rows:
        use_case = dict(row)
        if not use_case.get("account_in_portfolio"):
            continue
        portfolio_count += 1
        value_range = compute_value_range(
            use_case.get("hypothesized_value_json"), assumptions)
        mid = (value_range or {}).get("mid") or 0.0
        total += mid
        # "Buildable" is the number that matters in a funding conversation: value you
        # could start capturing now, not the theoretical ceiling.
        if readiness.get(use_case["id"], {}).get("readiness") == "shovel_ready":
            buildable += mid
        realized_info = compute_realized(use_case, assumptions)
        realized += realized_info.get("value") or 0.0
        if use_case.get("status") in ("live", "value_realized"):
            live += 1

    distribution: dict[str, int] = {}
    for entry in readiness.values():
        label = entry.get("readiness") or "unknown"
        distribution[label] = distribution.get(label, 0) + 1

    # Source and domain position, per account.
    sources = await db.fetchrow("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE ingestion_status = ANY($1::text[])) AS ready
        FROM asset_status_by_account
        WHERE account_id = (SELECT COALESCE($2::int,
                                            (SELECT id FROM accounts WHERE is_default)))
    """, list(READY_STATUSES), await _account_id())

    domains = await db.fetchrow("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE satisfied) AS satisfied
        FROM (
            SELECT dd.id,
                   COALESCE(bool_or(asd.data_asset_id = ANY($1::int[])), false)
                       AS satisfied
            FROM data_domains dd
            LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
            WHERE COALESCE(dd.is_active, true)
            GROUP BY dd.id
        ) AS per_domain
    """, await _ready_asset_ids())

    return {
        "total_value_mm": round(total, 2),
        "buildable_value_mm": round(buildable, 2),
        "realized_value_mm": round(realized, 2),
        "shovel_ready": distribution.get("shovel_ready", 0),
        "nearly_ready": distribution.get("nearly_ready", 0),
        "awaiting_prereqs": distribution.get("awaiting_prerequisites", 0),
        "blocked": distribution.get("blocked", 0),
        "sources_total": (sources or {}).get("total") if sources else None,
        "sources_ready": (sources or {}).get("ready") if sources else None,
        "domains_total": (domains or {}).get("total") if domains else None,
        "domains_satisfied": (domains or {}).get("satisfied") if domains else None,
        "use_cases_total": portfolio_count,
        "use_cases_live": live,
    }


async def _account_id() -> int | None:
    from . import accounts
    return await accounts.current()


async def _ready_asset_ids() -> list[int]:
    from .readiness import ready_assets
    return await ready_assets()


async def capture(reason: str = REASON_MANUAL, *, detail: str | None = None,
                  actor: str | None = None) -> dict:
    """Compute and store a snapshot. Returns the stored row.

    Raises on failure — the manual endpoint wants to report a problem. Automatic
    callers use capture_quietly().
    """
    metrics = await compute_metrics()
    account_id = await _account_id()

    row = await db.fetchrow("""
        INSERT INTO value_snapshots (
            account_id, reason, detail, captured_by,
            total_value_mm, buildable_value_mm, realized_value_mm,
            shovel_ready, nearly_ready, awaiting_prereqs, blocked,
            sources_total, sources_ready, domains_total, domains_satisfied,
            use_cases_total, use_cases_live, metrics_json)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
        -- Collides with the one-per-minute index when several changes are confirmed
        -- together. Updating rather than erroring keeps the LAST state of that minute,
        -- which is the one a reader wants.
        ON CONFLICT (account_id, captured_minute, reason) DO UPDATE SET
            detail = EXCLUDED.detail,
            total_value_mm = EXCLUDED.total_value_mm,
            buildable_value_mm = EXCLUDED.buildable_value_mm,
            realized_value_mm = EXCLUDED.realized_value_mm,
            shovel_ready = EXCLUDED.shovel_ready,
            nearly_ready = EXCLUDED.nearly_ready,
            awaiting_prereqs = EXCLUDED.awaiting_prereqs,
            blocked = EXCLUDED.blocked,
            sources_total = EXCLUDED.sources_total,
            sources_ready = EXCLUDED.sources_ready,
            domains_total = EXCLUDED.domains_total,
            domains_satisfied = EXCLUDED.domains_satisfied,
            use_cases_total = EXCLUDED.use_cases_total,
            use_cases_live = EXCLUDED.use_cases_live,
            metrics_json = EXCLUDED.metrics_json
        RETURNING id, captured_at, reason
    """, account_id, reason, detail, actor,
        metrics["total_value_mm"], metrics["buildable_value_mm"],
        metrics["realized_value_mm"], metrics["shovel_ready"],
        metrics["nearly_ready"], metrics["awaiting_prereqs"], metrics["blocked"],
        metrics["sources_total"], metrics["sources_ready"],
        metrics["domains_total"], metrics["domains_satisfied"],
        metrics["use_cases_total"], metrics["use_cases_live"],
        json.dumps(metrics))

    logger.info("snapshot captured (%s): buildable $%sM, %s shovel-ready",
                reason, metrics["buildable_value_mm"], metrics["shovel_ready"])
    return {**metrics, "id": (row or {}).get("id") if row else None,
            "captured_at": (row or {}).get("captured_at") if row else None,
            "reason": reason}


async def capture_quietly(reason: str, *, detail: str | None = None,
                          actor: str | None = None) -> None:
    """Capture without ever raising.

    Called from the tail of a confirm executor that has ALREADY applied a real change.
    Raising here would report "your change failed" when it succeeded; the only thing
    lost is a point on a chart, which is worth strictly less than a correct response.
    """
    try:
        await capture(reason, detail=detail, actor=actor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot skipped (%s: %s) — the change itself was applied",
                       type(exc).__name__, exc)


def delta(newer: dict, older: dict) -> dict:
    """Signed change between two snapshots, for the headline "since" line."""
    fields = ("total_value_mm", "buildable_value_mm", "realized_value_mm",
              "shovel_ready", "nearly_ready", "awaiting_prereqs", "blocked",
              "sources_ready", "domains_satisfied", "use_cases_live")
    out: dict[str, float | int | None] = {}
    for field in fields:
        before, after = older.get(field), newer.get(field)
        if before is None or after is None:
            out[field] = None
            continue
        change = float(after) - float(before)
        # Integers stay integers so the UI does not render "+3.0 use cases".
        out[field] = round(change, 2) if isinstance(before, float) or \
            isinstance(after, float) else int(change)
    return out
