"""Grid Atlas - FastAPI entry point.

Serves the REST API under /api/* and the built React SPA from frontend/dist.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.config import IS_DATABRICKS_APP, SERVING_ENDPOINT, GENIE_SPACE_ID
from server.db import db, token_refresh_loop
from server.routes import (
    lobs,
    data_assets,
    use_cases,
    dependencies,
    values,
    roadmap,
    comments,
    funding_requests,
    impact,
    value_assumptions,
    genie,
    agents,
    analytics,
    live,
    onboarding,
    joint_funding,
    source_recommendations,
)

BASE_DIR = Path(__file__).parent
MIGRATION = BASE_DIR / "server" / "migrations" / "001_init.sql"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


async def run_migrations() -> None:
    """Best-effort schema check on startup.

    Schema creation/migration is OWNED by the install step (databricks.yml setup
    + scripts/seed_clean.py), run as the Lakebase owner (the deploying principal).
    The running app authenticates as its own service principal, which holds DML
    but is NOT the table owner, so it cannot run DDL (CREATE/ALTER). That is
    expected and fine: we attempt the idempotent migration opportunistically and,
    if we lack ownership, log a single benign line and continue — the SP already
    has every privilege it needs to serve the app.
    """
    pool = await db.get_pool()
    if pool is None:
        print("[startup] Lakebase not configured - running in demo mode")
        return
    try:
        sql = MIGRATION.read_text()
        async with pool.acquire() as conn:
            await conn.execute(sql)
        print("[startup] schema up to date")
    except Exception as exc:  # noqa: BLE001
        # DDL failures at startup are EXPECTED and non-fatal: the app SP holds DML
        # but not table ownership, so it can't run CREATE/ALTER. Schema is owned by
        # the install/seed step (run as the DB owner). Detect the ownership case
        # (asyncpg InsufficientPrivilegeError -> SQLSTATE 42501, "must be owner")
        # and log a single benign line; keep serving either way.
        sqlstate = getattr(exc, "sqlstate", None)
        msg = str(exc).lower()
        if sqlstate == "42501" or "must be owner" in msg or "permission denied" in msg:
            print("[startup] schema managed by install step (app SP lacks DDL "
                  "privilege, which is expected) - continuing")
        else:
            print(f"[startup] schema check skipped ({type(exc).__name__}: {exc}) - continuing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    refresh_task = asyncio.create_task(token_refresh_loop())
    yield
    refresh_task.cancel()
    await db.close()


app = FastAPI(title="Grid Atlas", version="0.1.0", lifespan=lifespan)

# --- API routers -----------------------------------------------------------
for module in (lobs, data_assets, use_cases, dependencies, values, roadmap,
               comments, funding_requests, impact, value_assumptions, genie, agents,
               analytics, live, onboarding, joint_funding, source_recommendations):
    app.include_router(module.router, prefix="/api")

# --- Demo Mode (ISOLATED feature; gated behind DEMO_MODE env; see DEMO_MODE.md) ---
# To remove before Marketplace: delete this block + server/routes/demo.py.
from server.routes import demo as _demo  # noqa: E402
app.include_router(_demo.router, prefix="/api")


@app.get("/api/health")
async def health():
    pool = await db.get_pool()
    connected = False
    counts: dict = {}
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                connected = True
                for table in ("lobs", "data_assets", "use_cases"):
                    counts[table] = await conn.fetchval(f"SELECT count(*) FROM {table}")
        except Exception as exc:  # noqa: BLE001
            counts = {"error": str(exc)}
    return {
        "status": "healthy",
        "app": "grid-atlas",
        "environment": "databricks" if IS_DATABRICKS_APP else "local",
        "db_connected": connected,
        "demo_mode": db.is_demo_mode,
        "serving_endpoint": SERVING_ENDPOINT,
        "genie_space_configured": bool(GENIE_SPACE_ID),
        "counts": counts,
    }


# --- Static SPA ------------------------------------------------------------
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    async def root():
        return {"message": "Grid Atlas API. Frontend build not found.", "docs": "/docs"}
