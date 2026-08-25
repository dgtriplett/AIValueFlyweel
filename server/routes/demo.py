"""Demo Mode — ISOLATED, trivially-removable feature (see DEMO_MODE.md).

The ENTIRE feature is gated behind ONE env flag: ``DEMO_MODE``. When it is off
(the shipped default) every endpoint here returns 404 and the frontend toggle
does not render, so this module is a complete no-op — safe to leave in the tree
and a 5-minute deletion before Marketplace.

What it does: flips the LIVE Lakebase between two states on demand for a demo:
  - POST /api/demo/load  -> populate the showcase dataset (seed_demo scenario)
  - POST /api/demo/reset -> pristine day-1 (seed_clean scenario)
  - GET  /api/demo/status -> {enabled, db_connected, mode}

It REUSES the exact same seed data + phase logic as ``scripts/seed_lib.py``
(``load_data`` / ``compute_phases`` / ``TABLES_FK_ORDER`` and the identical
portfolio-selection rules), but writes through the app's existing asyncpg pool
using DML ONLY — DELETE + INSERT inside a single transaction. It never runs DDL
and never TRUNCATEs, because the app service principal holds DML privileges but
not table ownership. A failure rolls the whole transaction back, so the DB is
never left half-populated.

ALL demo logic lives in this one file plus
``frontend/src/components/DemoModeToggle.tsx``.
"""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .. import accounts as acct
from ..common import write_audit
from ..db import db

# --- The single feature flag -----------------------------------------------
DEMO_MODE_ENABLED = os.environ.get("DEMO_MODE", "").strip().lower() in (
    "1", "true", "yes", "on")

# Reuse the shared seed data + PURE helpers from scripts/seed_lib.py. scripts/
# is synced into the deployed app; seed_lib lazy-imports psycopg2 so importing
# it here (asyncpg-only runtime) is safe.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

router = APIRouter(prefix="/demo", tags=["demo"])


def _seed_lib():
    """Lazy import so a missing scripts/ dir can never crash app boot."""
    import seed_lib  # noqa: E402
    return seed_lib


def _dec(v):
    """asyncpg maps NUMERIC to Decimal; convert JSON numbers safely."""
    return None if v is None else Decimal(str(v))


async def _apply(conn, data, clean: bool) -> None:
    """Port of seed_lib.load() onto asyncpg, DML-only. Runs inside a caller
    transaction. Reproduces the clean day-1 / full-demo scenarios exactly."""
    sl = _seed_lib()

    # Reset via DELETE (DML) in FK-safe child->parent order. No TRUNCATE/DDL.
    for table in sl.TABLES_FK_ORDER:
        await conn.execute(f"DELETE FROM {table}")

    # 1) LOBs
    lob_id: dict = {}
    for name, desc in data["lobs"]:
        lob_id[name] = await conn.fetchval(
            "INSERT INTO lobs (name, description) VALUES ($1,$2) RETURNING id", name, desc)

    # 2) data assets (clean forces not_started)
    asset_id_by_index: dict = {}
    for idx, a in enumerate(data["data_assets"]):
        status = "not_started" if clean else a["ingestion_status"]
        aid = await conn.fetchval(
            """INSERT INTO data_assets
               (source_category, vendor, source_system, module, description, sub_vertical,
                ingestion_status, ingest_effort, ingest_cost_low, ingest_cost_high,
                uc_catalog, uc_schema, owning_lob_id, origin, created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING id""",
            a.get("source_category"), None, a["source_system"], a["module"],
            a["description"], a.get("sub_vertical") or "cross", status,
            a.get("ingest_effort"), _dec(a.get("ingest_cost_low")), _dec(a.get("ingest_cost_high")),
            a["uc_catalog"], a["uc_schema"], lob_id.get(a["owning_lob"]),
            a.get("origin", "catalog"), "seed")
        asset_id_by_index[idx] = aid
        for lob in a["benefiting_lobs"]:
            if lob in lob_id:
                await conn.execute(
                    "INSERT INTO data_asset_lobs (data_asset_id, lob_id) VALUES ($1,$2) "
                    "ON CONFLICT DO NOTHING", aid, lob_id[lob])

    # phases (computed from enables edges — identical to seed_lib)
    uc_parent_ids = [u["parent_id"] for u in data["use_cases"]]
    phase_by_parent = sl.compute_phases(data["enables"], uc_parent_ids)

    # Demo portfolio selection — identical rules to seed_lib.load(clean=False).
    demo_portfolio_parents: set = set()
    demo_custom_parents: set = set()
    if not clean:
        def _hyp_mid(u):
            comps = (u.get("hypothesized_value_json") or {}).get("components") or []
            return sum((c.get("multiplier") or 0) for c in comps)
        in_flight = [u for u in data["use_cases"] if u["status"] != "not_started"]
        in_flight.sort(key=_hyp_mid, reverse=True)
        chosen = in_flight[:35]
        demo_portfolio_parents = {u["parent_id"] for u in chosen}
        demo_custom_parents = {u["parent_id"] for u in chosen[:5]}

    # 3) use cases
    uc_id_by_parent: dict = {}
    for u in data["use_cases"]:
        in_portfolio = (not clean) and (u["parent_id"] in demo_portfolio_parents)
        origin = "custom" if u["parent_id"] in demo_custom_parents else "catalog"
        status = u["status"] if in_portfolio else "not_started"
        phase = phase_by_parent.get(u["parent_id"], 1)
        realized_amt = u["realized_value_amount"] if in_portfolio else None
        realized_json = (json.dumps(u["realized_value_json"])
                         if (in_portfolio and u.get("realized_value_json")) else None)
        uc_id_by_parent[u["parent_id"]] = await conn.fetchval(
            """INSERT INTO use_cases
               (title, description, lob_id, sub_vertical, stage, phase, status, category,
                effort_tshirt, priority_score, risk_tags, compliance_tags,
                hypothesized_value_json, realized_value_amount, realized_value_json,
                status_source, created_by, origin, in_portfolio)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
               RETURNING id""",
            u["title"], u["description"], lob_id.get(u["lob"]), u["sub_vertical"],
            u["stage"], phase, status, None, u["effort_tshirt"],
            _dec(u["priority_score"]), u["risk_tags"], u["compliance_tags"],
            json.dumps(u["hypothesized_value_json"]), _dec(realized_amt), realized_json,
            "manual", "seed", origin, in_portfolio)

    # 4) requires edges
    for (parent_id, aidx, crit) in data["requires"]:
        if parent_id in uc_id_by_parent and aidx in asset_id_by_index:
            await conn.execute(
                "INSERT INTO uc_requires_asset (use_case_id, data_asset_id, criticality) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                uc_id_by_parent[parent_id], asset_id_by_index[aidx], crit)

    # 5) enables edges
    for (frm, to) in data["enables"]:
        if frm in uc_id_by_parent and to in uc_id_by_parent and frm != to:
            await conn.execute(
                "INSERT INTO uc_enables_uc (from_use_case_id, to_use_case_id, detected_by_agent, rationale) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                uc_id_by_parent[frm], uc_id_by_parent[to], False,
                "Prerequisite: completing the upstream use case lands data that accelerates this one.")

    # 6) value assumptions (both modes)
    for a in data["value_assumptions"]:
        await conn.execute(
            "INSERT INTO value_assumptions (key, label, value, unit, category) "
            "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            a["key"], a["label"], _dec(a["value"]), a["unit"], a["category"])

    # 7) benchmark library (reference; if present)
    for b in data.get("benchmarks", []):
        await conn.execute(
            "INSERT INTO benchmark_library (use_case_pattern, sub_vertical, metric_type, low, mid, high, unit, notes) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            b.get("use_case_pattern"), b.get("sub_vertical"), b.get("metric_type"),
            _dec(b.get("low")), _dec(b.get("mid")), _dec(b.get("high")), b.get("unit"), b.get("notes"))

    if clean:
        return

    # ---- DEMO-ONLY in-flight state (portfolio use cases only) ----
    for u in data["use_cases"]:
        if u["parent_id"] not in demo_portfolio_parents:
            continue
        ucid = uc_id_by_parent[u["parent_id"]]
        if u["value_mid_mm"]:
            await conn.execute(
                "INSERT INTO value_records (use_case_id, kind, metric_type, amount, unit, fiscal_period, confidence, created_by) "
                "VALUES ($1,'hypothesized','annual value (mid)',$2,'USD_M','FY27','med','seed')",
                ucid, _dec(u["value_mid_mm"]))
        if u["realized_value_amount"]:
            await conn.execute(
                "INSERT INTO value_records (use_case_id, kind, metric_type, amount, unit, fiscal_period, confidence, created_by) "
                "VALUES ($1,'realized','realized annual value',$2,'USD','FY26','high','seed')",
                ucid, _dec(u["realized_value_amount"]))
        ph = phase_by_parent.get(u["parent_id"], 1)
        horizon = {1: "now", 2: "next", 3: "later"}.get(ph, "later")
        await conn.execute(
            "INSERT INTO roadmap_items (use_case_id, horizon, wave, notes) VALUES ($1,$2,$3,$4)",
            ucid, horizon, ph, f"Phase {ph} · {u.get('time_to_value') or ''}")

    n_fund = 0
    for idx, a in enumerate(data["data_assets"]):
        if len(a["benefiting_lobs"]) >= 2 and n_fund < 3:
            req_lob = a["benefiting_lobs"][0]
            co = [lob_id[lob] for lob in a["benefiting_lobs"][1:] if lob in lob_id]
            await conn.execute(
                "INSERT INTO funding_requests (data_asset_id, requesting_lob_id, co_funding_lobs, combined_value, status) "
                "VALUES ($1,$2,$3,$4,'proposed')",
                asset_id_by_index[idx], lob_id[req_lob], co, _dec(4_000_000 + n_fund * 1_500_000))
            n_fund += 1

    live_ucs = [uc_id_by_parent[u["parent_id"]] for u in data["use_cases"]
                if u["parent_id"] in demo_portfolio_parents
                and u["status"] in ("live", "value_realized", "in_progress")][:5]
    demo_jobs = [("job", "181028058559804", "CDC Fleet Intel - Live Simulator"),
                 ("pipeline", "388888035762457", "CDC Fleet Intel - Generate Synthetic Data")]
    for i, ucid in enumerate(live_ucs[:2]):
        at, dbx_id, an = demo_jobs[i % len(demo_jobs)]
        await conn.execute(
            "INSERT INTO linked_databricks_assets (use_case_id, asset_type, asset_id, asset_name) "
            "VALUES ($1,$2,$3,$4)", ucid, at, dbx_id, an)


# --- helpers ---------------------------------------------------------------
def _require_enabled() -> None:
    if not DEMO_MODE_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


async def _current_mode() -> str:
    row = await db.fetchrow("SELECT count(*) AS n FROM use_cases WHERE in_portfolio = true")
    n = (row["n"] if row else 0) or 0
    return "demo" if n > 0 else "clean"


async def _counts() -> dict:
    out = {}
    for t in ("use_cases", "value_records", "roadmap_items", "funding_requests"):
        row = await db.fetchrow(f"SELECT count(*) AS n FROM {t}")
        out[t] = int(row["n"]) if row else 0
    return out


async def _run(clean: bool) -> dict:
    pool = await db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Lakebase not reachable")
    sl = _seed_lib()
    try:
        data = sl.load_data()
    except SystemExit:
        raise HTTPException(status_code=500, detail="seed_data.json not found in deployment")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _apply(conn, data, clean=clean)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface a clean message, never hang
        action = "reset" if clean else "load"
        raise HTTPException(status_code=500, detail=f"Demo {action} failed: {exc}")
    return {"ok": True, "clean": clean, "mode": await _current_mode(), "counts": await _counts()}


# --- endpoints -------------------------------------------------------------
@router.get("/status")
async def demo_status():
    _require_enabled()
    if not os.environ.get("PGHOST") or db.is_demo_mode:
        return {"enabled": True, "db_connected": False, "mode": "unknown"}
    try:
        return {"enabled": True, "db_connected": True, "mode": await _current_mode()}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "db_connected": False, "mode": "unknown", "error": str(exc)}


@router.post("/load")
async def demo_load(request: Request):
    actor = acct.require_admin(request, "Loading demo data")
    _require_enabled()
    result = await _run(clean=False)
    # A demo load rewrites the entire live dataset; it is a sensitive, admin-only
    # operation and must be attributable in the audit log.
    await write_audit("demo", None, "load", actor,
                      {"clean": False, "counts": result.get("counts")})
    return result


@router.post("/reset")
async def demo_reset(request: Request):
    actor = acct.require_admin(request, "Resetting demo data")
    _require_enabled()
    result = await _run(clean=True)
    # A demo reset wipes+reseeds the entire live dataset; it is a sensitive,
    # admin-only operation and must be attributable in the audit log.
    await write_audit("demo", None, "reset", actor,
                      {"clean": True, "counts": result.get("counts")})
    return result
