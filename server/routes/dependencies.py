"""Dependency edges: UseCase->requires->DataAsset and UseCase->enables->UseCase."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, rows_to_list, write_audit
from ..db import db
from ..phase import recompute_and_store

router = APIRouter(prefix="/dependencies", tags=["dependencies"])


# --- requires edges (use case -> data asset) -------------------------------
class RequiresIn(BaseModel):
    use_case_id: int
    data_asset_id: int
    criticality: str = "required"
    manual: bool = False  # hand-edited via the UI override -> locks the UC's mapping


@router.get("/requires")
async def list_requires(use_case_id: int | None = None, data_asset_id: int | None = None):
    if use_case_id is not None:
        rows = await db.fetch("SELECT * FROM uc_requires_asset WHERE use_case_id=$1", use_case_id)
    elif data_asset_id is not None:
        rows = await db.fetch("SELECT * FROM uc_requires_asset WHERE data_asset_id=$1", data_asset_id)
    else:
        rows = await db.fetch("SELECT * FROM uc_requires_asset ORDER BY use_case_id")
    return rows_to_list(rows)


@router.post("/requires")
async def create_requires(body: RequiresIn, request: Request):
    if body.criticality not in ("required", "helpful"):
        raise HTTPException(422, "criticality must be 'required' or 'helpful'")
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO uc_requires_asset (use_case_id, data_asset_id, criticality, manual)
           VALUES ($1,$2,$3,$4)
           ON CONFLICT (use_case_id, data_asset_id)
           DO UPDATE SET criticality=EXCLUDED.criticality,
                         manual=(uc_requires_asset.manual OR EXCLUDED.manual) RETURNING *""",
        body.use_case_id, body.data_asset_id, body.criticality, body.manual,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    if body.manual:
        # lock the mapping so deterministic remap / agent auto-apply won't clobber it
        await db.execute("UPDATE use_cases SET requires_locked=true WHERE id=$1", body.use_case_id)
    await write_audit("uc_requires_asset", body.use_case_id, "create", actor, body.model_dump())
    return dict(row)


@router.delete("/requires")
async def delete_requires(use_case_id: int, data_asset_id: int, request: Request,
                          manual: bool = False):
    actor = current_user(request)
    res = await db.execute(
        "DELETE FROM uc_requires_asset WHERE use_case_id=$1 AND data_asset_id=$2",
        use_case_id, data_asset_id,
    )
    if manual:
        await db.execute("UPDATE use_cases SET requires_locked=true WHERE id=$1", use_case_id)
    await write_audit("uc_requires_asset", use_case_id, "delete", actor,
                      {"data_asset_id": data_asset_id, "manual": manual})
    return {"deleted": res is not None}


# --- enables edges (use case -> use case) ----------------------------------
class EnablesIn(BaseModel):
    from_use_case_id: int
    to_use_case_id: int
    detected_by_agent: bool = False
    rationale: str | None = None


@router.get("/enables")
async def list_enables(from_use_case_id: int | None = None, to_use_case_id: int | None = None):
    if from_use_case_id is not None:
        rows = await db.fetch("SELECT * FROM uc_enables_uc WHERE from_use_case_id=$1", from_use_case_id)
    elif to_use_case_id is not None:
        rows = await db.fetch("SELECT * FROM uc_enables_uc WHERE to_use_case_id=$1", to_use_case_id)
    else:
        rows = await db.fetch("SELECT * FROM uc_enables_uc ORDER BY from_use_case_id")
    return rows_to_list(rows)


async def _would_create_cycle(from_id: int, to_id: int) -> list[int] | None:
    """The prerequisite path from `to_id` back to `from_id`, if one exists.

    Adding `from -enables-> to` makes `from` a prerequisite of `to`. If `to` is already
    (transitively) a prerequisite of `from`, the new edge closes a loop: each use case
    waits for the other, neither can ever be shovel-ready, and the roadmap has a set of
    items that can never start with no visible reason why.

    compute_all_phases() already survives a cycle — it returns depth 0 at the back-edge
    rather than recursing forever — so this is not a crash guard. It is a data-integrity
    guard: the graph would be quietly meaningless instead of loudly broken, which is
    worse. Only self-edges were rejected before, and self-edges are the one cycle nobody
    accidentally creates.

    Returns the offending path (for the error message) or None.
    """
    edges = await db.fetch(
        "SELECT from_use_case_id, to_use_case_id FROM uc_enables_uc")
    downstream: dict[int, list[int]] = {}
    for edge in edges:
        downstream.setdefault(edge["from_use_case_id"], []).append(
            edge["to_use_case_id"])

    # Walk forward from `to_id`; reaching `from_id` means the new edge closes a loop.
    stack = [(to_id, [to_id])]
    seen = {to_id}
    while stack:
        node, path = stack.pop()
        if node == from_id:
            return path
        for nxt in downstream.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


# Serializes cycle-check-then-insert. The check and the INSERT are separate round trips,
# and server/db.py acquires a fresh pooled connection per call, so they cannot share a
# transaction without new plumbing. Two opposing POSTs arriving together would otherwise
# BOTH pass the check and both insert — producing exactly the cycle the guard prevents.
#
# An in-process asyncio.Lock is the right scope here, not a Postgres advisory lock: a
# SESSION-level advisory lock binds to a connection, and because each db call takes a
# different connection from the pool it would be acquired on one and released on another.
# (That was this code's first fix, and it was wrong.) A transaction-level lock would need
# the whole sequence inside one transaction, which is the plumbing being avoided.
#
# The tradeoff, stated plainly: this serializes edge creation within ONE app process. A
# Databricks App runs a single uvicorn process, so that covers the deployment as it
# exists. If this is ever scaled to multiple replicas, the guard needs to move into the
# database — either a transaction around check+insert, or a trigger.
_enables_write_lock = asyncio.Lock()


@router.post("/enables")
async def create_enables(body: EnablesIn, request: Request):
    if body.from_use_case_id == body.to_use_case_id:
        raise HTTPException(422, "A use case cannot enable itself")

    # Edge creation is a human-scale operation, so serializing it costs nothing
    # noticeable and removes the race entirely.
    async with _enables_write_lock:
        return await _create_enables_locked(body, request)


async def _create_enables_locked(body: EnablesIn, request: Request):
    cycle = await _would_create_cycle(body.from_use_case_id, body.to_use_case_id)
    if cycle:
        # Named, not just numbered: "42 → 87 → 42" is unactionable in a UI.
        #
        # `cycle` is the existing path from `to` back to `from`, so it ALREADY ends at
        # from_use_case_id. The loop is closed by the edge being added, which runs from
        # `from` back to the head of the path — so the readable form is the path followed
        # by its own first element. Appending from_use_case_id instead printed the same
        # title twice ("... → DER & EV → DER & EV"), which read like a self-edge.
        titles = {row["id"]: row["title"] for row in await db.fetch(
            "SELECT id, title FROM use_cases WHERE id = ANY($1::int[])", cycle)}
        loop = cycle + [cycle[0]]
        chain = " → ".join(titles.get(i, f"#{i}") for i in loop)
        raise HTTPException(
            422,
            f"That would create a circular dependency: {chain}. Each use case would be "
            "waiting for the other, so neither could ever be shovel-ready. Remove an "
            "edge in that chain first.")

    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO uc_enables_uc
           (from_use_case_id, to_use_case_id, detected_by_agent, rationale)
           VALUES ($1,$2,$3,$4)
           ON CONFLICT (from_use_case_id, to_use_case_id)
           DO UPDATE SET detected_by_agent=EXCLUDED.detected_by_agent,
                         rationale=EXCLUDED.rationale RETURNING *""",
        body.from_use_case_id, body.to_use_case_id, body.detected_by_agent, body.rationale,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("uc_enables_uc", body.from_use_case_id, "create", actor, body.model_dump())
    await recompute_and_store()  # phase derives from prerequisite depth
    return dict(row)


@router.delete("/enables")
async def delete_enables(from_use_case_id: int, to_use_case_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute(
        "DELETE FROM uc_enables_uc WHERE from_use_case_id=$1 AND to_use_case_id=$2",
        from_use_case_id, to_use_case_id,
    )
    await write_audit("uc_enables_uc", from_use_case_id, "delete", actor,
                      {"to_use_case_id": to_use_case_id})
    await recompute_and_store()  # phase derives from prerequisite depth
    return {"deleted": res is not None}
