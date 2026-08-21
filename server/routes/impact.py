"""Blast-radius / impact analysis for the flywheel view.

Given a focal node (data asset or use case), traverse the dependency graph
(uc_requires_asset + uc_enables_uc) in concentric rings and summarize the
downstream impact, grouped by LOB, with hypothesized value and how many use
cases would become shovel-ready.
"""
from fastapi import APIRouter, HTTPException

from ..db import db
from ..readiness import READY_STATUSES
from ..value_engine import compute_value, load_assumptions

router = APIRouter(prefix="/impact", tags=["impact"])

_NODE_KINDS = ("asset", "uc", "lob")


async def _load_graph():
    ucs = await db.fetch("SELECT * FROM use_cases ORDER BY id")
    assets = await db.fetch("SELECT * FROM data_assets ORDER BY id")
    requires = await db.fetch("SELECT * FROM uc_requires_asset")
    enables = await db.fetch("SELECT * FROM uc_enables_uc")
    return ucs, assets, requires, enables


def _asset_key(i):
    return f"asset-{i}"


def _uc_key(i):
    return f"uc-{i}"


def _split_node_id(node_id: str) -> tuple[str, int] | None:
    """Split 'asset-12' / 'uc-3' / 'lob-7' into ('asset', 12); None if malformed.

    Every caller used to do `int(nid.split("-")[1])` inline, which raises IndexError
    on a bare 'bogus' and ValueError on 'asset-abc'. Neither is an HTTPException, so
    both surfaced as a 500 on a request the client got wrong — see `blast_radius`,
    which turns the None into the 422 the typed `{id:int}` routes already return.
    """
    kind, _, raw = node_id.partition("-")
    if kind not in _NODE_KINDS or not raw.isdigit():
        return None
    return kind, int(raw)


@router.get("/top-asset")
async def top_asset():
    """The data asset whose ingestion unlocks the most use cases (default focus)."""
    rows = await db.fetch(
        """SELECT da.id, da.source_system, da.module,
                  COUNT(ura.use_case_id) AS uc_count
           FROM data_assets da
           LEFT JOIN uc_requires_asset ura ON ura.data_asset_id = da.id
           GROUP BY da.id, da.source_system, da.module
           ORDER BY uc_count DESC, da.id
           LIMIT 1"""
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "node_id": _asset_key(r["id"]),
        "type": "asset",
        "id": r["id"],
        "label": f"{r['source_system']} · {r['module']}",
        "downstream_uc_count": int(r["uc_count"] or 0),
    }


@router.get("/{node_id}")
async def blast_radius(node_id: str, max_rings: int = 4):
    """Concentric impact rings from a focal node.

    node_id: 'asset-<id>' or 'uc-<id>' or 'lob-<id>'.
    Returns rings (list of node ids per depth), node metadata, and a summary.
    """
    # Validated before the graph load so a typo costs nothing and, more to the
    # point, cannot reach the `int(...)` calls below as a 500.
    parsed = _split_node_id(node_id)
    if parsed is None:
        raise HTTPException(
            422, f"node_id must be 'asset-<id>', 'uc-<id>' or 'lob-<id>', not {node_id!r}")
    kind, focal_id = parsed

    ucs, assets, requires, enables = await _load_graph()
    uc_by_id = {u["id"]: u for u in ucs}
    asset_by_id = {a["id"]: a for a in assets}

    # adjacency: focal traversal follows "what does X unlock downstream"
    #   asset -> use cases that require it
    #   use case -> use cases it enables
    downstream: dict[str, set[str]] = {}

    def add(a, b):
        downstream.setdefault(a, set()).add(b)

    for r in requires:
        add(_asset_key(r["data_asset_id"]), _uc_key(r["use_case_id"]))
    for e in enables:
        add(_uc_key(e["from_use_case_id"]), _uc_key(e["to_use_case_id"]))

    # seed set
    seeds: list[str] = []
    if kind == "lob":
        lob_id = focal_id
        # seed with all assets owned by the LOB + all UCs owned by the LOB
        seeds = [_asset_key(a["id"]) for a in assets if a["owning_lob_id"] == lob_id]
        seeds += [_uc_key(u["id"]) for u in ucs if u["lob_id"] == lob_id]
    else:
        seeds = [node_id]

    # BFS rings
    rings: list[list[str]] = []
    seen: set[str] = set(seeds)
    frontier = set(seeds)
    rings.append(sorted(frontier))
    for _ in range(max_rings):
        nxt: set[str] = set()
        for n in frontier:
            for m in downstream.get(n, ()):  # noqa
                if m not in seen:
                    nxt.add(m)
        if not nxt:
            break
        seen |= nxt
        rings.append(sorted(nxt))
        frontier = nxt

    # node metadata
    def node_meta(nid: str) -> dict | None:
        # Unknown/unparseable ids are "no metadata", not an error: callers already
        # drop the None, and the ring ids are built by _asset_key/_uc_key so this
        # only bites if the graph ever grows a node kind this function predates.
        split = _split_node_id(nid)
        if split is None:
            return None
        nkind, nid_int = split
        if nkind == "asset":
            a = asset_by_id.get(nid_int)
            if not a:
                return None
            return {
                "node_id": nid, "type": "asset", "id": a["id"],
                "label": a["module"], "sublabel": a["source_system"],
                "lob_id": a["owning_lob_id"], "ingestion_status": a["ingestion_status"],
            }
        u = uc_by_id.get(nid_int)
        if not u:
            return None
        return {
            "node_id": nid, "type": "usecase", "id": u["id"],
            "label": u["title"], "sublabel": u["sub_vertical"],
            "lob_id": u["lob_id"], "stage": u["stage"],
        }

    nodes = {}
    for ring in rings:
        for nid in ring:
            m = node_meta(nid)
            if m:
                nodes[nid] = m

    # downstream use cases (exclude the seeds themselves if they are UCs? keep all UCs beyond ring 0)
    seed_set = set(seeds)
    downstream_ucs = [
        n for nid, n in nodes.items() if n["type"] == "usecase" and nid not in seed_set
    ]

    # summary
    lob_ids = {n["lob_id"] for n in downstream_ucs if n["lob_id"] is not None}

    # hypothesized value computed from the current global assumptions (parameterized model)
    assumptions = await load_assumptions()
    total_hyp_value = 0.0
    for n in downstream_ucs:
        v = compute_value(uc_by_id[n["id"]]["hypothesized_value_json"], assumptions)
        if v:
            total_hyp_value += v
    total_hyp_value = round(total_hyp_value, 2)

    # readiness flips: if the focal node is an asset that is not yet ready,
    # count downstream UCs that would become shovel-ready once it is ready.
    becomes_ready = 0
    focal_asset_ids = set()
    for s in seeds:
        split = _split_node_id(s)
        if split is not None and split[0] == "asset":
            focal_asset_ids.add(split[1])
    if focal_asset_ids:
        # for each downstream UC, recompute readiness assuming focal assets are governed
        # get required assets per downstream UC
        req_map: dict[int, list[tuple[int, str]]] = {}
        for r in requires:
            if r["criticality"] == "required":
                req_map.setdefault(r["use_case_id"], []).append(
                    (r["data_asset_id"], asset_by_id[r["data_asset_id"]]["ingestion_status"])
                )
        # prerequisite-built map: a UC's direct prereqs (upstream enablers) must be
        # live/value_realized for it to become TRUE shovel-ready.
        prereqs_built: dict[int, bool] = {}
        prereq_lists: dict[int, list[int]] = {}
        for e in enables:
            prereq_lists.setdefault(e["to_use_case_id"], []).append(e["from_use_case_id"])
        for uc_id, ups in prereq_lists.items():
            prereqs_built[uc_id] = all(
                (uc_by_id.get(p) or {}).get("status") in ("live", "value_realized") for p in ups)
        for n in downstream_ucs:
            reqs = req_map.get(n["id"], [])
            if not reqs:
                continue
            now_ready = all(
                (st in READY_STATUSES) for (_, st) in reqs
            )
            hypo_ready = all(
                (aid in focal_asset_ids) or (st in READY_STATUSES) for (aid, st) in reqs
            )
            prereqs_ok = prereqs_built.get(n["id"], True)
            # becomes TRUE shovel-ready only if data completes AND prereqs are built
            if hypo_ready and not now_ready and prereqs_ok:
                becomes_ready += 1

    focal = nodes.get(node_id) if kind != "lob" else {
        "node_id": node_id, "type": "lob", "id": focal_id, "label": f"LOB {node_id}",
    }

    return {
        "focal": focal,
        "rings": rings,
        "nodes": list(nodes.values()),
        "summary": {
            "downstream_uc_count": len(downstream_ucs),
            "lob_count": len(lob_ids),
            "lob_ids": sorted(lob_ids),
            "hypothesized_value": total_hyp_value,
            "becomes_shovel_ready": becomes_ready,
        },
    }
