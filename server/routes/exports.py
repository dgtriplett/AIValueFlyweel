"""Shareable executive exports.

The app already has the underlying facts: portfolio value, readiness, assumptions,
data gaps, and what-if candidates. Sponsors need those facts in a portable artifact
they can forward without granting app access. These endpoints are read-only; they
do not snapshot, apply assumptions, or change the portfolio.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .. import accounts
from .. import portfolio
from ..common import rows_to_list
from ..db import db
from ..readiness import readiness_map
from ..snapshots import compute_metrics
from ..value_engine import load_assumptions, use_case_value

router = APIRouter(prefix="/exports", tags=["exports"])


def _fmt_money(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"${float(value):,.2f}M"


def _readiness_label(value: str | None) -> str:
    return (value or "unknown").replace("_", " ")


@router.get("/executive-pack")
async def executive_pack():
    """Structured executive pack for the current account."""
    account_id = await accounts.current()
    generated_at = datetime.now(timezone.utc).isoformat()

    profile = await db.fetchrow(
        "SELECT * FROM company_profile WHERE account_id = $1", account_id)
    assumptions = await load_assumptions()
    rmap = await readiness_map()
    metrics = await compute_metrics()

    condition, params = await portfolio.portfolio_condition("u")
    use_cases = rows_to_list(await db.fetch(f"""
        SELECT u.id, u.title, u.status, u.effort_tshirt, u.hypothesized_value_json,
               l.name AS lob
        FROM use_cases u
        LEFT JOIN lobs l ON l.id = u.lob_id
        WHERE {condition}
        ORDER BY u.id
    """, *params))
    for use_case in use_cases:
        rd = rmap.get(use_case["id"], {})
        use_case["readiness"] = rd.get("readiness")
        use_case["confidence"] = rd.get("confidence")
        use_case["confidence_score"] = rd.get("confidence_score")
        use_case["pending_prereqs"] = rd.get("pending_prereqs") or []
        use_case["pending_domains"] = rd.get("pending_domains") or []
        use_case["value_mm"] = round(use_case_value(use_case, assumptions), 2)

    top_value = sorted(use_cases, key=lambda u: u["value_mm"], reverse=True)[:10]
    shovel_ready = [u for u in use_cases if u.get("readiness") == "shovel_ready"]
    blocked = [u for u in use_cases if u.get("readiness") == "blocked"]
    awaiting = [u for u in use_cases
                if u.get("readiness") == "awaiting_prerequisites"]
    top_buildable = sorted(shovel_ready, key=lambda u: u["value_mm"], reverse=True)[:8]
    top_blocked = sorted(blocked + awaiting, key=lambda u: u["value_mm"],
                         reverse=True)[:8]

    gap_rows = rows_to_list(await db.fetch("""
        SELECT dd.label, dd.category, count(DISTINCT urd.use_case_id) AS use_cases
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        WHERE urd.necessity = 'required'
          AND COALESCE(dd.is_active, true)
        GROUP BY dd.id, dd.label, dd.category
        ORDER BY use_cases DESC, dd.label
        LIMIT 12
    """))

    assumption_rows = rows_to_list(await db.fetch("""
        SELECT DISTINCT ON (key) key, label, value, unit, category, source,
               confidence, source_note
        FROM value_assumptions
        WHERE $1::int IS NULL OR account_id = $1 OR account_id IS NULL
        ORDER BY key, (account_id IS NULL)
    """, account_id))
    calibrated = [a for a in assumption_rows if a.get("source") == "research"]
    generic = [a for a in assumption_rows if a.get("source") != "research"]

    try:
        from .whatif import candidates
        whatif = await candidates(limit=8)
    except Exception:  # noqa: BLE001 - export should survive optional simulator failures
        whatif = {"candidates": [], "note": "What-if candidates unavailable."}

    return {
        "generated_at": generated_at,
        "company": dict(profile) if profile else None,
        "metrics": metrics,
        "readiness_distribution": {
            "shovel_ready": metrics.get("shovel_ready"),
            "awaiting_prerequisites": metrics.get("awaiting_prereqs"),
            "nearly_ready": metrics.get("nearly_ready"),
            "blocked": metrics.get("blocked"),
        },
        "top_value_use_cases": top_value,
        "top_buildable_use_cases": top_buildable,
        "top_blocked_or_awaiting_use_cases": top_blocked,
        "data_needs": gap_rows,
        "assumptions": {
            "total": len(assumption_rows),
            "calibrated": len(calibrated),
            "generic": len(generic),
            "sample_calibrated": calibrated[:8],
            "sample_generic": generic[:8],
        },
        "whatif": whatif,
        "next_actions": _next_actions(top_buildable, top_blocked, whatif),
    }


def _next_actions(top_buildable: list[dict], top_blocked: list[dict],
                  whatif: dict) -> list[str]:
    actions: list[str] = []
    if top_buildable:
        actions.append(
            f"Start delivery planning for {top_buildable[0]['title']} "
            f"({_fmt_money(top_buildable[0]['value_mm'])}/yr).")
    if top_blocked:
        blocked = top_blocked[0]
        if blocked.get("readiness") == "awaiting_prerequisites":
            actions.append(
                f"Build prerequisites for {blocked['title']} to unlock "
                f"{_fmt_money(blocked['value_mm'])}/yr.")
        else:
            actions.append(
                f"Close data gaps for {blocked['title']} to unlock "
                f"{_fmt_money(blocked['value_mm'])}/yr.")
    candidates = whatif.get("candidates") or []
    if candidates:
        c = candidates[0]
        actions.append(
            f"Evaluate landing {c.get('module') or c.get('source')} first; "
            f"the simulator estimates {_fmt_money(c.get('value_unblocked_mm'))}/yr "
            "of newly shovel-ready value.")
    actions.append("Review generic value assumptions and calibrate them through Company research.")
    return actions[:4]


@router.get("/executive-pack.md")
async def executive_pack_markdown():
    """Markdown version for download/sharing."""
    pack = await executive_pack()
    md = _render_markdown(pack)
    headers = {
        "Content-Disposition": 'attachment; filename="grid-atlas-executive-pack.md"',
    }
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                             headers=headers)


def _render_markdown(pack: dict) -> str:
    company = pack.get("company") or {}
    name = company.get("company_name") or "Configured Utility"
    metrics = pack.get("metrics") or {}
    assumptions = pack.get("assumptions") or {}
    whatif = pack.get("whatif") or {}
    lines = [
        f"# Grid Atlas Executive Pack: {name}",
        "",
        f"Generated: {pack.get('generated_at')}",
        "",
        "## Portfolio Snapshot",
        "",
        f"- Total hypothesized value: {_fmt_money(metrics.get('total_value_mm'))}/yr",
        f"- Shovel-ready value: {_fmt_money(metrics.get('buildable_value_mm'))}/yr",
        f"- Realized value: {_fmt_money(metrics.get('realized_value_mm'))}/yr",
        f"- Use cases: {metrics.get('use_cases_total', '-')}",
        f"- Shovel-ready: {metrics.get('shovel_ready', '-')}",
        f"- Awaiting prerequisites: {metrics.get('awaiting_prereqs', '-')}",
        f"- Blocked: {metrics.get('blocked', '-')}",
        "",
        "## Top Buildable Use Cases",
        "",
        _table(
            ["Use case", "LOB", "Value/yr", "Confidence"],
            [[u["title"], u.get("lob") or "-", _fmt_money(u["value_mm"]),
              u.get("confidence") or "-"]
             for u in pack.get("top_buildable_use_cases", [])],
        ),
        "",
        "## Highest-Value Blocked Or Sequenced Use Cases",
        "",
        _table(
            ["Use case", "State", "Value/yr", "Main blocker"],
            [[u["title"], _readiness_label(u.get("readiness")),
              _fmt_money(u["value_mm"]), _blocker(u)]
             for u in pack.get("top_blocked_or_awaiting_use_cases", [])],
        ),
        "",
        "## Value Assumptions",
        "",
        f"- Assumptions tracked: {assumptions.get('total', 0)}",
        f"- Customer-calibrated: {assumptions.get('calibrated', 0)}",
        f"- Still generic: {assumptions.get('generic', 0)}",
        "",
        "## What-if Recommendations",
        "",
        _table(
            ["Source", "Module", "Use cases", "Value/yr", "Value per $M"],
            [[c.get("source") or "-", c.get("module") or "-",
              c.get("use_cases_unblocked", 0),
              _fmt_money(c.get("value_unblocked_mm")),
              c.get("value_per_cost") or "-"]
             for c in (whatif.get("candidates") or [])[:8]],
        ),
        "",
        "## Recommended Next Actions",
        "",
        *[f"- {action}" for action in pack.get("next_actions", [])],
        "",
    ]
    return "\n".join(lines)


def _blocker(use_case: dict) -> str:
    prereqs = use_case.get("pending_prereqs") or []
    if prereqs:
        return ", ".join(p.get("title") or f"#{p.get('id')}" for p in prereqs[:3])
    domains = use_case.get("pending_domains") or []
    if domains:
        return ", ".join(d.get("label") or d.get("name") or "data gap"
                         for d in domains[:3])
    return "-"


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])
