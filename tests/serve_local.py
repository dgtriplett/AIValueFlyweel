"""Run the app locally with stubbed drivers, for UI work without a workspace.

    python3 tests/serve_local.py [--port 8000]

Serves the real FastAPI app — real routers, real handlers, real static files — but
with `asyncpg`/`openai`/`aiohttp` stubbed and Lakebase absent, so it needs no
database, no Databricks workspace, and no network. Read-only endpoints answer from
a small in-memory fixture; anything requiring the warehouse returns the same
degraded response a misconfigured install would.

The point is to exercise the console's rendering and error paths — including the
"nothing is configured yet" state a customer sees first, which is otherwise the
hardest state to reach deliberately.

This is a development tool. It is not imported by the app and has no effect on a
deployed instance.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import stubs  # noqa: E402,F401  (must precede any server.* import)

# No Lakebase, no discovery catalog: the fresh-install state.
os.environ.pop("PGHOST", None)
os.environ.pop("ATLAS_CATALOG", None)
os.environ.setdefault("SERVING_ENDPOINT", "databricks-claude-sonnet-4-5")

from fakedb import FakeDB, Row  # noqa: E402

from server import db as db_module  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: enough rows for every console view to render something real.
# ---------------------------------------------------------------------------
LOBS = [Row(id=1, name="Distribution", description="Distribution operations"),
        Row(id=2, name="Generation", description="Generation fleet"),
        Row(id=3, name="Customer", description="Customer operations")]

DOMAINS = [
    Row(id=1, name="outage_records", label="Outage & Interruption Records",
        description="Interruption events with cause and restoration times.",
        category="grid", example_attributes="outage_id, feeder_id, cause_code",
        is_active=True, origin="catalog", is_user_edited=False,
        created_at=None, updated_at=None,
        serving_asset_count=3, ready_asset_count=2, ready_assets=2,
        use_case_count=12, required_by_count=9),
    Row(id=2, name="interval_meter_usage", label="Interval Meter Usage",
        description="Interval consumption reads from AMI meters.",
        category="customer", example_attributes="meter_id, interval_start, kwh",
        is_active=True, origin="catalog", is_user_edited=False,
        created_at=None, updated_at=None,
        serving_asset_count=4, ready_asset_count=0, ready_assets=0,
        use_case_count=18, required_by_count=15),
    Row(id=3, name="market_prices", label="Market & Locational Prices",
        description="Day-ahead and real-time LMPs by settlement point.",
        category="market", example_attributes="settlement_point, lmp",
        is_active=True, origin="catalog", is_user_edited=False,
        created_at=None, updated_at=None,
        serving_asset_count=4, ready_asset_count=0, ready_assets=0,
        use_case_count=7, required_by_count=6),
]

DATA_ASSETS = [
    Row(id=1, source_category="ADMS (Distribution)", vendor=None,
        source_system="ADMS (Distribution)", module="OMS (Outage Management)",
        description="Outage events and restoration.", sub_vertical="cross",
        ingestion_status="curated", ingest_effort="L", ingest_cost_low=None,
        ingest_cost_high=None, uc_catalog=None, uc_schema=None, owning_lob_id=1,
        origin="catalog", auto_captured=False, auto_note=None, created_by="seed",
        created_at=None, updated_at=None, discovered_table_count=0,
        discovery_confidence=None, last_discovered_at=None),
    Row(id=2, source_category="Meter/AMI/MDM", vendor=None,
        source_system="Meter/AMI/MDM", module="Interval Usage Collection",
        description="AMI interval reads.", sub_vertical="cross",
        ingestion_status="not_started", ingest_effort="L", ingest_cost_low=None,
        ingest_cost_high=None, uc_catalog=None, uc_schema=None, owning_lob_id=3,
        origin="catalog", auto_captured=False, auto_note=None, created_by="seed",
        created_at=None, updated_at=None, discovered_table_count=0,
        discovery_confidence=None, last_discovered_at=None),
]

ALIASES = [
    Row(id=1, raw="OSIsoft PI Historian", raw_normalized="osisoft pi historian",
        canonical="Data Historian", mapped_by="normalized", confidence="medium",
        notes=None, is_user_edited=False, mapped_at=None, updated_at=None),
    Row(id=2, raw="Acme Widget Tracker", raw_normalized="acme widget tracker",
        canonical="Other", mapped_by="fallback_other", confidence="low",
        notes=None, is_user_edited=False, mapped_at=None, updated_at=None),
]

TAXONOMY = [
    Row(data_asset_id=1, dimension="criticality", value="T1 - Mission critical",
        source="ai", confidence=0.9, ai_reasoning="Feeds restoration.",
        effective_from=None, created_by="seed",
        source_category="ADMS (Distribution)", module="OMS (Outage Management)",
        ingestion_status="curated"),
    Row(data_asset_id=1, dimension="integration_pattern", value="Real-time streaming",
        source="ai", confidence=0.8, ai_reasoning="SCADA-adjacent.",
        effective_from=None, created_by="seed",
        source_category="ADMS (Distribution)", module="OMS (Outage Management)",
        ingestion_status="curated"),
]


def build_fake_db() -> FakeDB:
    # has_pool=True so the app behaves as though Lakebase is reachable; the
    # discovery/warehouse paths still degrade, which is the state worth seeing.
    fake = FakeDB(has_pool=True)
    fake.on("FROM lobs", LOBS)
    fake.on("SELECT id, name FROM lobs", LOBS)
    fake.on("FROM data_assets", DATA_ASSETS)
    fake.on("SELECT * FROM data_assets ORDER BY id", DATA_ASSETS)
    fake.on("FROM data_domains dd", DOMAINS)
    fake.on("SELECT id, name FROM data_domains",
            [Row(id=d["id"], name=d["name"]) for d in DOMAINS])
    fake.on("FROM data_asset_aliases", ALIASES)
    fake.on("FROM asset_taxonomy at", TAXONOMY)
    fake.on("SELECT count(*) AS n FROM data_assets", [Row(n=len(DATA_ASSETS))])
    fake.on("dimension, count(DISTINCT data_asset_id)",
            [Row(dimension="criticality", n=1), Row(dimension="integration_pattern", n=1)])
    fake.on("HAVING count(DISTINCT dimension)", [Row(n=0)])
    fake.on("FROM ingestion_runs", [])
    fake.on("FROM uc_generation_previews", [])
    fake.on("SELECT title FROM use_cases", [Row(title="Outage Prediction")])
    fake.on("FROM value_assumptions", [Row(key="omBudgetMM", label="O&M budget",
                                           value=500, unit="USD_M", category="cost")])
    fake.on("information_schema.tables", [Row(n=5)])
    fake.on("(SELECT count(*) FROM use_cases)",
            [Row(use_cases=240, data_assets=146, domains=63, assumptions=34)])
    fake.on("SELECT 1 AS ok", [Row(ok=1)])
    return fake


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    fake = build_fake_db()

    # Point every module that captured `db` at import time to the fake. Each
    # router does `from ..db import db`, which binds the object rather than the
    # module, so each binding has to be replaced individually.
    import importlib

    db_module.db = fake
    for name in ("common", "readiness", "value_engine", "confirm", "live"):
        try:
            module = importlib.import_module(f"server.{name}")
            if hasattr(module, "db"):
                module.db = fake
        except ImportError:
            pass
    for name in ("lobs", "data_assets", "use_cases", "dependencies", "values",
                 "roadmap", "comments", "funding_requests", "impact",
                 "value_assumptions", "genie", "agents", "analytics", "live",
                 "onboarding", "joint_funding", "source_recommendations",
                 "domains", "ingestion", "generate", "setup", "taxonomy", "demo"):
        try:
            module = importlib.import_module(f"server.routes.{name}")
            if hasattr(module, "db"):
                module.db = fake
        except ImportError:
            pass

    import uvicorn

    from app import app

    print("\n  Grid Atlas (local, stubbed drivers)")
    print(f"  Console:   http://{args.host}:{args.port}/console")
    print(f"  API docs:  http://{args.host}:{args.port}/docs")
    print(f"  Portfolio: http://{args.host}:{args.port}/  (built SPA)\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
