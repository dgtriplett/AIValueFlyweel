"""Value flow (Sankey) + glossary.

    GET /api/flow/sankey     sources -> domains -> use cases -> LOBs, $-weighted
    GET /api/flow/glossary   business terms with the data behind them
    ... plus glossary CRUD

WHY THESE LIVE TOGETHER
-----------------------
Both are projections of the same join — asset → domain → use case → LOB — read at
different altitudes. The Sankey is the narrative view (where does value flow, where
does it stop), the glossary is the lookup view (what does this term mean, which
systems hold it). Sharing the aggregation keeps them consistent: a term's sources
and a Sankey link can never disagree about the same underlying data.

WHY A SANKEY IN ADDITION TO THE COVERAGE MATRIX
-----------------------------------------------
They answer different questions and neither replaces the other:

  Coverage matrix   precise, per-cell: "is THIS need met for THIS line of business?"
                    Good for working through gaps systematically.
  Sankey            narrative, whole-system: "our value concentrates in these three
                    domains, and it stops here." Good for a conversation with an
                    executive who will not read a 63-row grid.

The matrix is a grid of states; the Sankey is weighted flow. Dropping either would
lose a real use, so both exist — and both read from this module's aggregation so
their numbers agree.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/flow", tags=["flow"])

READY = ("curated", "governed")


# ---------------------------------------------------------------------------
# Sankey
# ---------------------------------------------------------------------------
@router.get("/sankey")
async def sankey(
    lob_id: int | None = None,
    top_use_cases: int = Query(25, ge=5, le=200),
    include_gaps: bool = Query(True, description="Include unsatisfied domains"),
):
    """Four-column value flow: data source -> data domain -> use case -> LOB.

    Link width is the annual $ value carried. A use case's value is split evenly
    across the domains it requires, and each domain's share is split across the
    sources that serve it — so a source feeding several high-value use cases reads
    as a thick ribbon, which is the intuition the diagram exists to convey.

    `top_use_cases` caps the middle column by value: past ~25 nodes a Sankey stops
    being readable, and the tail contributes little width anyway.

    Node colour encodes state, so the gaps are visible rather than implied:
      source   grey when not landed
      domain   red when nothing landed serves it (a real gap)
      use case by readiness
    """
    from ..readiness import readiness_map
    from ..value_engine import compute_value_range, load_assumptions

    assumptions = await load_assumptions()
    readiness = await readiness_map()

    lob_filter = "AND uc.lob_id = $1" if lob_id is not None else ""
    args = [lob_id] if lob_id is not None else []

    # One row per (use case, required domain). The LEFT JOIN on assets matters:
    # a domain with no serving asset must still appear, because that is exactly the
    # gap the diagram should show.
    rows = await db.fetch(f"""
        SELECT uc.id AS uc_id, uc.title AS uc_title, uc.status,
               uc.hypothesized_value_json,
               l.id AS lob_id, l.name AS lob_name,
               dd.id AS domain_id, dd.name AS domain_name, dd.label AS domain_label,
               dd.category AS domain_category,
               da.id AS asset_id, da.source_category, da.module,
               da.ingestion_status
        FROM uc_requires_domain urd
        JOIN use_cases uc ON uc.id = urd.use_case_id
        JOIN data_domains dd ON dd.id = urd.domain_id
        LEFT JOIN lobs l ON l.id = uc.lob_id
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE urd.necessity = 'required'
          AND uc.in_portfolio = true
          AND COALESCE(dd.is_active, true) = true
          {lob_filter}
    """, *args)

    if not rows:
        return {"nodes": [], "links": [], "summary": {
            "use_cases": 0, "domains": 0, "sources": 0, "total_value_mm": 0.0,
            "blocked_value_mm": 0.0},
            "note": "No portfolio use case declares a required data domain yet."}

    # --- value per use case, and its domain set ----------------------------
    uc_value: dict[int, float] = {}
    uc_meta: dict[int, dict] = {}
    uc_domains: dict[int, set] = {}
    domain_assets: dict[int, dict] = {}
    domain_meta: dict[int, dict] = {}

    for row in rows:
        uc_id = row["uc_id"]
        if uc_id not in uc_value:
            rng = compute_value_range(row_to_dict(row).get("hypothesized_value_json"),
                                      assumptions)
            uc_value[uc_id] = rng["mid"] if rng else 0.0
            uc_meta[uc_id] = {
                "title": row["uc_title"], "status": row["status"],
                "lob_id": row["lob_id"], "lob_name": row["lob_name"] or "Unassigned",
                "readiness": (readiness.get(uc_id) or {}).get("readiness"),
            }
        uc_domains.setdefault(uc_id, set()).add(row["domain_id"])
        domain_meta.setdefault(row["domain_id"], {
            "name": row["domain_name"], "label": row["domain_label"],
            "category": row["domain_category"]})
        if row["asset_id"] is not None:
            domain_assets.setdefault(row["domain_id"], {})[row["asset_id"]] = {
                "label": f"{row['source_category'] or ''} · {row['module']}".strip(" ·"),
                "category": row["source_category"],
                "landed": row["ingestion_status"] in READY,
            }

    # Cap the middle column by value; the tail is thin and unreadable.
    ranked = sorted(uc_value, key=lambda i: -uc_value[i])[:top_use_cases]
    kept = set(ranked)

    # --- accumulate link weights ------------------------------------------
    # dict keys are (source_node, target_node) so parallel edges merge.
    links: dict[tuple, float] = {}
    used_domains: set[int] = set()
    used_assets: set[int] = set()
    used_lobs: dict[int | None, str] = {}
    blocked_value = 0.0

    for uc_id in kept:
        value = uc_value[uc_id]
        domains = uc_domains[uc_id]
        if not domains:
            continue
        # Even split: the model has no basis for weighting one requirement over
        # another, and inventing weights would be false precision.
        per_domain = value / len(domains)
        meta = uc_meta[uc_id]

        for domain_id in domains:
            assets = domain_assets.get(domain_id, {})
            landed = {aid: a for aid, a in assets.items() if a["landed"]}
            satisfied = bool(landed)
            if not satisfied:
                blocked_value += per_domain
                if not include_gaps:
                    continue
            used_domains.add(domain_id)

            # source -> domain. Landed sources carry the flow; when none are
            # landed, the unlanded ones are shown so the gap has an origin.
            contributors = landed or assets
            if contributors:
                per_asset = per_domain / len(contributors)
                for asset_id in contributors:
                    used_assets.add(asset_id)
                    links[(f"src:{asset_id}", f"dom:{domain_id}")] = \
                        links.get((f"src:{asset_id}", f"dom:{domain_id}"), 0.0) + per_asset

            # domain -> use case
            links[(f"dom:{domain_id}", f"uc:{uc_id}")] = \
                links.get((f"dom:{domain_id}", f"uc:{uc_id}"), 0.0) + per_domain

        # use case -> LOB
        used_lobs[meta["lob_id"]] = meta["lob_name"]
        links[(f"uc:{uc_id}", f"lob:{meta['lob_id']}")] = \
            links.get((f"uc:{uc_id}", f"lob:{meta['lob_id']}"), 0.0) + value

    # --- nodes -------------------------------------------------------------
    nodes = []
    for asset_id in sorted(used_assets):
        for assets in domain_assets.values():
            if asset_id in assets:
                asset = assets[asset_id]
                nodes.append({
                    "id": f"src:{asset_id}", "label": asset["label"], "column": 0,
                    "kind": "source",
                    "state": "landed" if asset["landed"] else "not_landed",
                })
                break
    for domain_id in sorted(used_domains):
        meta = domain_meta[domain_id]
        assets = domain_assets.get(domain_id, {})
        satisfied = any(a["landed"] for a in assets.values())
        nodes.append({
            "id": f"dom:{domain_id}", "label": meta["label"], "column": 1,
            "kind": "domain", "category": meta["category"],
            "state": "satisfied" if satisfied else "gap",
        })
    for uc_id in ranked:
        meta = uc_meta[uc_id]
        nodes.append({
            "id": f"uc:{uc_id}", "label": meta["title"], "column": 2,
            "kind": "use_case", "state": meta["readiness"] or "unknown",
            "value_mm": round(uc_value[uc_id], 2), "status": meta["status"],
        })
    for lob_id_key, name in used_lobs.items():
        nodes.append({
            "id": f"lob:{lob_id_key}", "label": name, "column": 3, "kind": "lob",
            "state": "lob",
        })

    node_ids = {n["id"] for n in nodes}
    link_list = [
        {"source": s, "target": t, "value": round(v, 3)}
        for (s, t), v in sorted(links.items(), key=lambda kv: -kv[1])
        # A link to a node we dropped (e.g. a capped use case) would render as a
        # dangling edge, so drop it too rather than emit an invalid graph.
        if s in node_ids and t in node_ids and v > 0
    ]

    total = sum(uc_value[i] for i in kept)
    return {
        "nodes": nodes,
        "links": link_list,
        "summary": {
            "use_cases": len(kept),
            "use_cases_total": len(uc_value),
            "domains": len(used_domains),
            "sources": len(used_assets),
            "lobs": len(used_lobs),
            "total_value_mm": round(total, 2),
            # The number the diagram is really about: value that cannot flow.
            "blocked_value_mm": round(blocked_value, 2),
            "blocked_pct": round(100 * blocked_value / total, 1) if total else 0.0,
        },
        "legend": {
            "columns": ["Data source", "Data need", "Use case", "Line of business"],
            "link_width": "annual $M of hypothesized value carried",
            "gap": "a red data-need node has no landed source — value stops there",
        },
    }


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------
class GlossaryTermIn(BaseModel):
    term: str = Field(..., min_length=1, max_length=200)
    definition: str | None = None
    domain_id: int | None = None
    lob_id: int | None = None
    synonyms: list[str] = []
    source_systems: list[str] = []
    owner: str | None = None


@router.get("/glossary")
async def list_glossary(search: str | None = None, include_derived: bool = True):
    """Business terms with the systems behind them.

    Curated terms come from `glossary_terms`. When `include_derived`, the data
    domains are also projected as terms — a domain already IS a business term with
    a definition and known systems of record, so re-typing 63 of them by hand would
    be busywork. Derived entries are marked so a user can tell which they can edit.
    """
    curated = rows_to_list(await db.fetch("""
        SELECT gt.*, dd.label AS domain_label, dd.name AS domain_name,
               l.name AS lob_name
        FROM glossary_terms gt
        LEFT JOIN data_domains dd ON dd.id = gt.domain_id
        LEFT JOIN lobs l ON l.id = gt.lob_id
        ORDER BY gt.term
    """))
    for term in curated:
        term["origin_kind"] = "curated"

    derived: list[dict] = []
    if include_derived:
        # Exclude domains already covered by a curated term, so the same concept
        # never appears twice with two definitions.
        claimed = {t["domain_id"] for t in curated if t.get("domain_id")}
        rows = await db.fetch("""
            SELECT dd.id, dd.name, dd.label, dd.description, dd.category,
                   dd.example_attributes,
                   COALESCE(
                       array_agg(DISTINCT COALESCE(da.source_category, da.source_system))
                       FILTER (WHERE da.id IS NOT NULL), '{}'::text[]
                   ) AS source_systems,
                   COUNT(DISTINCT asd.data_asset_id) FILTER (
                       WHERE da.ingestion_status IN ('curated','governed')
                   ) AS landed_sources,
                   COUNT(DISTINCT urd.use_case_id) AS use_case_count
            FROM data_domains dd
            LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
            LEFT JOIN data_assets da ON da.id = asd.data_asset_id
            LEFT JOIN uc_requires_domain urd ON urd.domain_id = dd.id
            WHERE COALESCE(dd.is_active, true) = true
            GROUP BY dd.id
            ORDER BY dd.label
        """)
        for raw in rows:
            # Read via a plain dict so a row missing an aggregate column degrades to
            # a default instead of raising. asyncpg Records index-error on an absent
            # key, which turned one query change into a 500 on the whole page.
            row = dict(raw)
            if row.get("id") in claimed:
                continue
            derived.append({
                "id": None, "term": row.get("label"),
                "definition": row.get("description"),
                "domain_id": row.get("id"), "domain_name": row.get("name"),
                "domain_label": row.get("label"), "lob_name": None,
                "synonyms": [], "source_systems": list(row.get("source_systems") or []),
                "owner": None, "origin": "derived", "origin_kind": "derived",
                "example_attributes": row.get("example_attributes"),
                "category": row.get("category"),
                "landed_sources": int(row.get("landed_sources") or 0),
                "use_case_count": int(row.get("use_case_count") or 0),
                "is_user_edited": False,
            })

    terms = curated + derived
    if search:
        needle = search.lower()
        terms = [t for t in terms
                 if needle in (t.get("term") or "").lower()
                 or needle in (t.get("definition") or "").lower()
                 or any(needle in s.lower() for s in (t.get("synonyms") or []))]
    terms.sort(key=lambda t: (t.get("term") or "").lower())
    return {
        "terms": terms,
        "summary": {"total": len(terms), "curated": len(curated),
                    "derived": len(derived)},
    }


@router.post("/glossary")
async def create_term(body: GlossaryTermIn, request: Request):
    """Add a curated term. Attaching a domain_id supersedes that domain's derived
    entry, so the same concept never shows twice."""
    actor = current_user(request)
    existing = await db.fetchrow(
        "SELECT id FROM glossary_terms WHERE lower(term) = lower($1)", body.term)
    if existing:
        raise HTTPException(409, f"A term named {body.term!r} already exists.")
    row = await db.fetchrow(
        """INSERT INTO glossary_terms
           (term, definition, domain_id, lob_id, synonyms, source_systems, owner,
            origin, is_user_edited)
           VALUES ($1,$2,$3,$4,$5,$6,$7,'manual',true) RETURNING *""",
        body.term, body.definition, body.domain_id, body.lob_id,
        body.synonyms, body.source_systems, body.owner)
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("glossary_term", row["id"], "create", actor, body.model_dump())
    return dict(row)


@router.put("/glossary/{term_id}")
async def update_term(term_id: int, body: GlossaryTermIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        """UPDATE glossary_terms SET term=$1, definition=$2, domain_id=$3, lob_id=$4,
           synonyms=$5, source_systems=$6, owner=$7, is_user_edited=true,
           updated_at=now() WHERE id=$8 RETURNING *""",
        body.term, body.definition, body.domain_id, body.lob_id,
        body.synonyms, body.source_systems, body.owner, term_id)
    if row is None:
        raise HTTPException(404, "Term not found")
    await write_audit("glossary_term", term_id, "update", actor, body.model_dump())
    return dict(row)


@router.delete("/glossary/{term_id}")
async def delete_term(term_id: int, request: Request):
    actor = current_user(request)
    result = await db.execute("DELETE FROM glossary_terms WHERE id = $1", term_id)
    await write_audit("glossary_term", term_id, "delete", actor)
    return {"deleted": result is not None}
