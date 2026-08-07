"""Grid Atlas - FastAPI entry point.

Serves the REST API under /api/* and the built React SPA from frontend/dist.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
    domains,
    ingestion,
    generate,
    setup,
    taxonomy,
    research,
    flow,
    inventory,
    chat,
)

BASE_DIR = Path(__file__).parent
MIGRATIONS_DIR = BASE_DIR / "server" / "migrations"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
# Operator console: a dependency-free page for the discovery/agent workflows.
# Separate from the SPA because the SPA's TypeScript source isn't in this repo
# (see README) — this way the new screens ship without touching the built bundle.
CONSOLE_DIR = BASE_DIR / "frontend" / "console"


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
        # Every migration, in lexical order. 002+ ALTER tables that 001 creates,
        # so the numeric prefix ordering is load-bearing; applying only 001 would
        # leave the domain, discovery, and agent tables missing.
        sql = "\n".join(p.read_text() for p in sorted(MIGRATIONS_DIR.glob("*.sql")))
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
               analytics, live, onboarding, joint_funding, source_recommendations,
               domains, ingestion, generate, setup, taxonomy, research,
               flow, inventory, chat):
    app.include_router(module.router, prefix="/api")

# Secondary routers whose paths don't sit under their module's own prefix:
#   domains.uc_router  — a use case's required domains belong under /use-cases
#   generate.confirm_router — /confirm is shared by every agent-proposed write
app.include_router(domains.uc_router, prefix="/api")
app.include_router(generate.confirm_router, prefix="/api")

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


# --- Operator console ------------------------------------------------------
# Mounted BEFORE the SPA's catch-all, which matches every path and would
# otherwise return index.html for /console.
if CONSOLE_DIR.exists():
    # `/console` (no trailing slash) does NOT hit the mount below — it falls
    # through to the SPA catch-all, which serves index.html and silently shows the
    # portfolio instead. Redirecting makes both spellings work, so a typed URL or
    # a stale bookmark still lands on the console.
    @app.get("/console", include_in_schema=False)
    async def console_redirect():
        return RedirectResponse(url="/console/", status_code=308)

    app.mount("/console", StaticFiles(directory=str(CONSOLE_DIR), html=True),
              name="console")


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
        return {"message": "Grid Atlas API. Frontend build not found.",
                "console": "/console", "docs": "/docs"}
