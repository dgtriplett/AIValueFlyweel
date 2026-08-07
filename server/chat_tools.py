"""Typed tools for the chat assistant.

DESIGN
------
Each tool is a name, a JSON-schema for its arguments, and a handler. The model picks
one, the loop runs it, and the result goes back as context. Two hard rules:

  READ tools execute immediately. They only ever SELECT.
  WRITE tools do not write. They validate, then issue a confirm token through the
  existing gate in server/confirm.py, and the card comes back for a human to
  approve. An LLM deciding to recalibrate a portfolio is exactly the situation that
  gate was built for, so the chat reuses it rather than getting its own path.

WHY TYPED TOOLS AND NOT free-form SQL or Genie
-----------------------------------------------
The portfolio is small and its questions are known: which use cases, which gaps,
what value, what's blocked. Typed tools over the existing routes give deterministic,
already-tested answers with no SQL-injection surface and no chance of the model
inventing a table. Genie remains available for open-ended questions over the mirror
where free-form querying is actually the right tool.

Handlers deliberately call the same functions the REST routes call. A second query
path would drift from the endpoints and the tests that cover them.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from .db import db


class Tool:
    """A callable the model can select.

    `writes` is the flag that matters: it decides whether the loop executes the
    handler or routes its result through the confirm gate.
    """

    def __init__(self, name: str, description: str, parameters: dict,
                 handler: Callable[[dict, str], Awaitable[dict]],
                 *, writes: bool = False) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.writes = writes

    def spec(self) -> dict:
        """OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------
async def _search_use_cases(args: dict, actor: str) -> dict:
    """Substring + LOB/readiness filtered search. Deliberately simple: the corpus
    is hundreds of rows, so scoring beats embedding for both latency and
    explainability."""
    query = (args.get("query") or "").strip().lower()
    limit = min(int(args.get("limit", 10)), 50)
    clauses = ["uc.in_portfolio = true"]
    params: list = []
    if query:
        params.append(f"%{query}%")
        clauses.append(f"(lower(uc.title) LIKE ${len(params)} "
                       f"OR lower(coalesce(uc.description,'')) LIKE ${len(params)})")
    if args.get("lob_name"):
        params.append(args["lob_name"])
        clauses.append(f"lower(l.name) = lower(${len(params)})")
    if args.get("status"):
        params.append(args["status"])
        clauses.append(f"uc.status = ${len(params)}")
    params.append(limit)

    rows = await db.fetch(f"""
        SELECT uc.id, uc.title, uc.status, uc.effort_tshirt, l.name AS lob_name
        FROM use_cases uc LEFT JOIN lobs l ON l.id = uc.lob_id
        WHERE {' AND '.join(clauses)}
        ORDER BY uc.title LIMIT ${len(params)}
    """, *params)

    from .readiness import readiness_map
    from .value_engine import compute_value_range, load_assumptions
    readiness = await readiness_map()
    assumptions = await load_assumptions()

    items = []
    for row in rows:
        detail = await db.fetchrow(
            "SELECT hypothesized_value_json FROM use_cases WHERE id=$1", row["id"])
        from .common import row_to_dict
        rng = compute_value_range(
            row_to_dict(detail).get("hypothesized_value_json") if detail else None,
            assumptions)
        state = readiness.get(row["id"]) or {}
        items.append({
            "id": row["id"], "title": row["title"], "status": row["status"],
            "lob": row["lob_name"], "effort": row["effort_tshirt"],
            "value_mm": round(rng["mid"], 2) if rng else 0.0,
            "readiness": state.get("readiness"),
            "blocked_by": [d["label"] for d in (state.get("pending_domains") or [])][:4],
        })
    # Filtering after scoring keeps the readiness/value data available for the
    # filter itself, which SQL alone cannot express (readiness is computed).
    if args.get("readiness"):
        items = [i for i in items if i["readiness"] == args["readiness"]]
    return {"count": len(items), "use_cases": items}


async def _get_use_case(args: dict, actor: str) -> dict:
    uc_id = int(args["use_case_id"])
    row = await db.fetchrow("""
        SELECT uc.*, l.name AS lob_name FROM use_cases uc
        LEFT JOIN lobs l ON l.id = uc.lob_id WHERE uc.id = $1
    """, uc_id)
    if row is None:
        return {"error": f"No use case with id {uc_id}."}
    from .common import row_to_dict
    from .readiness import readiness_for
    from .value_engine import compute_value_range, load_assumptions

    use_case = row_to_dict(row)
    assumptions = await load_assumptions()
    rng = compute_value_range(use_case.get("hypothesized_value_json"), assumptions)
    domains = await db.fetch("""
        SELECT dd.label, dd.name, urd.necessity,
               COUNT(asd.data_asset_id) FILTER (
                   WHERE da.ingestion_status IN ('curated','governed')) AS landed
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE urd.use_case_id = $1
        GROUP BY dd.id, dd.label, dd.name, urd.necessity
    """, uc_id)
    return {
        "id": use_case["id"], "title": use_case["title"],
        "description": use_case.get("description"),
        "lob": use_case.get("lob_name"), "status": use_case["status"],
        "effort": use_case.get("effort_tshirt"),
        "value_mm": round(rng["mid"], 2) if rng else 0.0,
        "value_low_mm": round(rng["low"], 2) if rng else 0.0,
        "value_high_mm": round(rng["high"], 2) if rng else 0.0,
        "readiness": await readiness_for(uc_id),
        "required_data": [
            {"need": d["label"], "necessity": d["necessity"],
             "satisfied": (d["landed"] or 0) > 0} for d in domains],
    }


async def _value_summary(args: dict, actor: str) -> dict:
    """Portfolio value, split the way the value engine actually splits it."""
    from .readiness import readiness_map
    from .value_engine import compute_value_range, load_assumptions
    from .common import rows_to_list

    assumptions = await load_assumptions()
    readiness = await readiness_map()
    rows = rows_to_list(await db.fetch("""
        SELECT uc.id, uc.status, uc.hypothesized_value_json, uc.realized_value_amount,
               l.name AS lob_name
        FROM use_cases uc LEFT JOIN lobs l ON l.id = uc.lob_id
        WHERE uc.in_portfolio = true
    """))
    total = buildable = realized = 0.0
    by_lob: dict[str, float] = {}
    by_readiness: dict[str, float] = {}
    for row in rows:
        rng = compute_value_range(row.get("hypothesized_value_json"), assumptions)
        value = rng["mid"] if rng else 0.0
        total += value
        state = (readiness.get(row["id"]) or {}).get("readiness") or "unknown"
        by_readiness[state] = by_readiness.get(state, 0.0) + value
        if state == "shovel_ready":
            buildable += value
        by_lob[row["lob_name"] or "Unassigned"] = \
            by_lob.get(row["lob_name"] or "Unassigned", 0.0) + value
        realized += float(row.get("realized_value_amount") or 0) / 1_000_000
    return {
        "total_hypothesized_mm": round(total, 2),
        "buildable_now_mm": round(buildable, 2),
        "realized_mm": round(realized, 2),
        "by_lob": {k: round(v, 2) for k, v in sorted(by_lob.items(), key=lambda x: -x[1])},
        "by_readiness": {k: round(v, 2) for k, v in by_readiness.items()},
        "note": "Values derive from the value assumptions; ask about calibration "
                "if these look wrong for this company.",
    }


async def _list_gaps(args: dict, actor: str) -> dict:
    """Unsatisfied data needs ranked by the value they block."""
    from .routes.domains import domain_gaps
    result = await domain_gaps(limit=min(int(args.get("limit", 10)), 30))
    return {
        "summary": result["summary"],
        "gaps": [{
            "need": g["domain"]["label"],
            "value_blocked_mm": g["value_blocked_mm"],
            "use_cases_blocked": g["blocked_use_case_count"],
            "no_source_at_all": g["has_no_source"],
            "why": g["rationale"],
        } for g in result["gaps"]],
    }


async def _list_domains(args: dict, actor: str) -> dict:
    from .routes.domains import list_domains
    domains = await list_domains(include_inactive=False)
    query = (args.get("query") or "").strip().lower()
    if query:
        domains = [d for d in domains
                   if query in (d.get("label") or "").lower()
                   or query in (d.get("name") or "").lower()]
    return {"count": len(domains), "domains": [{
        "name": d["name"], "label": d["label"], "category": d.get("category"),
        "satisfied": d["satisfied"], "sources": d.get("serving_asset_count"),
        "landed_sources": d.get("ready_asset_count"),
        "required_by_use_cases": d.get("required_by_count"),
    } for d in domains[:60]]}


async def _list_data_sources(args: dict, actor: str) -> dict:
    from .common import rows_to_list
    rows = rows_to_list(await db.fetch("""
        SELECT source_category, count(*) AS modules,
               count(*) FILTER (WHERE ingestion_status IN ('curated','governed')) AS landed,
               count(*) FILTER (WHERE ingestion_status = 'not_started') AS not_started
        FROM data_assets GROUP BY source_category ORDER BY source_category
    """))
    return {"count": len(rows), "source_systems": rows}


async def _next_best(args: dict, actor: str) -> dict:
    """Delegates to the existing recommender rather than reimplementing scoring."""
    from .routes.agents import recommend, RecommendIn
    result = await recommend(RecommendIn(top_n=min(int(args.get("top_n", 5)), 10)))
    return {"used_llm": result.get("used_llm"), "recommendations": [{
        "title": r["title"], "lob": r.get("lob_name"), "value_mm": r.get("value_mm"),
        "readiness": r.get("readiness"), "track": r.get("track"),
        "why": r.get("rationale"), "narrative": r.get("narrative"),
    } for r in result.get("recommendations", [])]}


async def _company_profile(args: dict, actor: str) -> dict:
    row = await db.fetchrow("SELECT * FROM company_profile WHERE id = 1")
    if row is None:
        return {"researched": False,
                "note": "No company researched yet. Research can calibrate the "
                        "value assumptions to a named utility."}
    profile = dict(row)
    calibrated = await db.fetchrow(
        "SELECT count(*) AS n FROM value_assumptions WHERE source = 'research'")
    profile["calibrated_assumptions"] = int(calibrated["n"]) if calibrated else 0
    profile["researched"] = True
    return profile


async def _list_assumptions(args: dict, actor: str) -> dict:
    """The assumptions and whether each is calibrated — the honest answer to
    "where does this number come from?"."""
    from .common import rows_to_list
    rows = rows_to_list(await db.fetch("""
        SELECT key, label, value, unit, category, source, confidence, source_note
        FROM value_assumptions ORDER BY category, key
    """))
    query = (args.get("query") or "").strip().lower()
    if query:
        rows = [r for r in rows if query in r["key"].lower()
                or query in (r.get("label") or "").lower()]
    calibrated = sum(1 for r in rows if r.get("source") == "research")
    return {
        "count": len(rows),
        "calibrated": calibrated,
        "still_default": len(rows) - calibrated,
        "assumptions": rows[:40],
    }


async def _find_data_assets(args: dict, actor: str) -> dict:
    """Look up individual data-source modules BY NAME, returning their ids.

    Exists because the write tools take a `data_asset_id` and nothing else surfaced
    one: `list_source_systems` aggregates by category, so asking to change a specific
    module left the model searching in circles until it ran out of rounds.
    """
    query = (args.get("query") or "").strip().lower()
    limit = min(int(args.get("limit", 15)), 50)
    clauses: list[str] = []
    params: list = []
    if query:
        params.append(f"%{query}%")
        clauses.append(f"(lower(module) LIKE ${len(params)} "
                       f"OR lower(coalesce(source_category,'')) LIKE ${len(params)})")
    if args.get("status"):
        params.append(args["status"])
        clauses.append(f"ingestion_status = ${len(params)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    from .common import rows_to_list
    rows = rows_to_list(await db.fetch(f"""
        SELECT da.id, da.source_category, da.module, da.ingestion_status,
               COUNT(DISTINCT asd.domain_id) AS serves_needs
        FROM data_assets da
        LEFT JOIN asset_serves_domain asd ON asd.data_asset_id = da.id
        {where}
        GROUP BY da.id
        ORDER BY da.source_category, da.module
        LIMIT ${len(params)}
    """, *params))
    return {"count": len(rows), "data_sources": rows,
            "note": "Use `id` with propose_data_source_status to change one."}


# ---------------------------------------------------------------------------
# WRITE tools — these PROPOSE. The loop routes them through the confirm gate.
# ---------------------------------------------------------------------------
async def _propose_status_change(args: dict, actor: str) -> dict:
    """Validate a status change and describe it. The loop issues the token."""
    valid = ("not_started", "scoping", "in_progress", "live", "value_realized")
    status = str(args.get("status") or "").strip()
    if status not in valid:
        return {"error": f"status must be one of {list(valid)}."}
    uc_id = int(args["use_case_id"])
    row = await db.fetchrow("SELECT id, title, status FROM use_cases WHERE id=$1", uc_id)
    if row is None:
        return {"error": f"No use case with id {uc_id}."}
    if row["status"] == status:
        return {"error": f"{row['title']!r} is already {status}."}
    return {
        "_propose": True,
        "intent": "updateUseCaseStatus",
        "payload": {"use_case_id": uc_id, "status": status},
        "before": {"status": row["status"]},
        "after": {"status": status},
        "summary": f"Set {row['title']!r} from {row['status']} to {status}",
    }


async def _propose_asset_status(args: dict, actor: str) -> dict:
    valid = ("not_started", "landed", "curated", "governed")
    status = str(args.get("ingestion_status") or "").strip()
    if status not in valid:
        return {"error": f"ingestion_status must be one of {list(valid)}."}
    asset_id = int(args["data_asset_id"])
    row = await db.fetchrow(
        "SELECT id, source_category, module, ingestion_status FROM data_assets "
        "WHERE id=$1", asset_id)
    if row is None:
        return {"error": f"No data asset with id {asset_id}."}
    label = f"{row['source_category']} · {row['module']}"
    if row["ingestion_status"] == status:
        return {"error": f"{label} is already {status}."}
    return {
        "_propose": True,
        "intent": "advanceAssetStatus",
        "payload": {"data_asset_id": asset_id, "ingestion_status": status},
        "before": {"ingestion_status": row["ingestion_status"]},
        "after": {"ingestion_status": status},
        # Naming the consequence matters: this is the write that moves readiness.
        "summary": (f"Set {label} from {row['ingestion_status']} to {status}. "
                    "This changes use-case readiness."),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
TOOLS: dict[str, Tool] = {}


def _register(tool: Tool) -> None:
    TOOLS[tool.name] = tool


_register(Tool(
    "search_use_cases",
    "Search the use-case portfolio by keyword, line of business, status, or "
    "readiness. Returns value and what is blocking each one. Use this first for "
    "any question about use cases.",
    _obj({
        "query": {"type": "string", "description": "keyword to match in title/description"},
        "lob_name": {"type": "string", "description": "e.g. Distribution, Generation"},
        "status": {"type": "string",
                   "enum": ["not_started", "scoping", "in_progress", "live", "value_realized"]},
        "readiness": {"type": "string",
                      "enum": ["shovel_ready", "awaiting_prerequisites",
                               "nearly_ready", "blocked"]},
        "limit": {"type": "integer"},
    }),
    _search_use_cases))

_register(Tool(
    "get_use_case",
    "Full detail for one use case: value range, readiness, and exactly which data "
    "needs are satisfied or missing.",
    _obj({"use_case_id": {"type": "integer"}}, ["use_case_id"]),
    _get_use_case))

_register(Tool(
    "value_summary",
    "Portfolio value: total hypothesized, how much is buildable now, realized to "
    "date, and the split by line of business and readiness.",
    _obj({}), _value_summary))

_register(Tool(
    "list_gaps",
    "Unsatisfied data needs ranked by the annual value they block. Use for "
    "'what is holding us back' or 'where should we invest in data'.",
    _obj({"limit": {"type": "integer"}}), _list_gaps))

_register(Tool(
    "list_data_needs",
    "The semantic data needs (domains) and whether each is satisfied by a landed "
    "source.",
    _obj({"query": {"type": "string"}}), _list_domains))

_register(Tool(
    "list_source_systems",
    "Source systems in the catalog with how many modules are landed.",
    _obj({}), _list_data_sources))

_register(Tool(
    "find_data_sources",
    "Find individual data-source modules by name and get their ids. Use this "
    "BEFORE propose_data_source_status, which needs a data_asset_id.",
    _obj({"query": {"type": "string", "description": "part of a module or category name"},
          "status": {"type": "string",
                     "enum": ["not_started", "landed", "curated", "governed"]},
          "limit": {"type": "integer"}}),
    _find_data_assets))

_register(Tool(
    "next_best_use_cases",
    "The recommender's ranked next-best use cases, by value, readiness, and effort.",
    _obj({"top_n": {"type": "integer"}}), _next_best))

_register(Tool(
    "company_profile",
    "Who this instance is about, and whether the value assumptions have been "
    "calibrated to them.",
    _obj({}), _company_profile))

_register(Tool(
    "list_value_assumptions",
    "The value assumptions driving every dollar figure, including whether each is "
    "calibrated to this company or still a generic default. Use this when asked "
    "where a number comes from.",
    _obj({"query": {"type": "string"}}), _list_assumptions))

_register(Tool(
    "propose_use_case_status",
    "Propose changing a use case's delivery status. Returns a confirmation card — "
    "the change is NOT applied until the user confirms it.",
    _obj({"use_case_id": {"type": "integer"},
          "status": {"type": "string",
                     "enum": ["not_started", "scoping", "in_progress", "live",
                              "value_realized"]}},
         ["use_case_id", "status"]),
    _propose_status_change, writes=True))

_register(Tool(
    "propose_data_source_status",
    "Propose changing a data source's ingestion status (this moves use-case "
    "readiness). Returns a confirmation card — NOT applied until confirmed.",
    _obj({"data_asset_id": {"type": "integer"},
          "ingestion_status": {"type": "string",
                               "enum": ["not_started", "landed", "curated", "governed"]}},
         ["data_asset_id", "ingestion_status"]),
    _propose_asset_status, writes=True))


def tool_specs() -> list[dict]:
    return [tool.spec() for tool in TOOLS.values()]


SYSTEM_PROMPT = """You are the Grid Atlas assistant, helping a Power & Utilities \
team understand and act on their data & AI portfolio.

Use the tools to answer from the ACTUAL portfolio. Never invent a use case, a value, \
or a data source — if a tool returns nothing, say so plainly.

When you report a dollar figure, remember it comes from the value assumptions. If \
those are still generic defaults (list_value_assumptions tells you), say so, because \
an uncalibrated number is directional at best.

For changes, use a propose_* tool. It returns a card the user must confirm; never \
claim a change has been made. Say what you have proposed and that it awaits their \
approval.

Be concise and concrete. Prefer specifics from the tools over general utility \
commentary — the user can get generalities anywhere. When a question is ambiguous, \
make your best interpretation, answer it, and say what you assumed."""
