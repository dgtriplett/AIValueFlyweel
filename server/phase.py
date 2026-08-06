"""Derived use-case phase from prerequisite depth.

Phase is COMPUTED, not chosen. Driver = longest upstream prerequisite chain
depth (how many layers must be built first); mapping caps at 3:
  depth 0 (no prereqs)          -> Phase 1
  depth 1 (one layer of prereqs)-> Phase 2
  depth >= 2 (deep chain)       -> Phase 3

Recompute after any dependency-edge change (create UC, add/remove enables edge,
Excel import, AI-accepted deps) and across the seed. Unifies with roadmap waves
and drives the blast-radius rings.
"""
from .db import db


def _phase_from_depth(d: int) -> int:
    return 1 if d <= 0 else 2 if d == 1 else 3


async def compute_all_phases() -> dict[int, int]:
    """Return {use_case_id: phase} for every use case, from the enables graph."""
    ucs = await db.fetch("SELECT id FROM use_cases")
    edges = await db.fetch("SELECT from_use_case_id, to_use_case_id FROM uc_enables_uc")
    # from --enables--> to  => 'from' is a prerequisite of 'to'
    upstream: dict[int, list[int]] = {}
    for e in edges:
        if e["from_use_case_id"] != e["to_use_case_id"]:
            upstream.setdefault(e["to_use_case_id"], []).append(e["from_use_case_id"])
    memo: dict[int, int] = {}

    def depth(u, stack):
        if u in memo:
            return memo[u]
        if u in stack:
            return 0
        stack.add(u)
        preds = upstream.get(u, [])
        d = 0 if not preds else 1 + max((depth(p, stack) for p in preds), default=0)
        stack.discard(u)
        memo[u] = d
        return d

    return {r["id"]: _phase_from_depth(depth(r["id"], set())) for r in ucs}


async def recompute_and_store(only_ids: set[int] | None = None) -> int:
    """Recompute phases and write any that changed. Returns count updated."""
    phases = await compute_all_phases()
    current = {r["id"]: r["phase"] for r in await db.fetch("SELECT id, phase FROM use_cases")}
    updated = 0
    for uid, ph in phases.items():
        if only_ids is not None and uid not in only_ids:
            continue
        if current.get(uid) != ph:
            await db.execute("UPDATE use_cases SET phase=$1, updated_at=now() WHERE id=$2", ph, uid)
            updated += 1
    return updated
