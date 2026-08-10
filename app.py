"""AI Value Flywheel - FastAPI entry point.

Serves the REST API under /api/* and the built React SPA from frontend/dist.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server import limits, logging_setup
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
    branding,
    knowledge,
    proposals,
    accounts as accounts_routes,
    whatif,
)

# Install handlers before anything logs. JSON in Databricks Apps, text locally;
# LOG_LEVEL turns detail up on a deployed app without a code change.
logging_setup.configure()
logger = logging.getLogger("grid_atlas.app")

BASE_DIR = Path(__file__).parent
MIGRATIONS_DIR = BASE_DIR / "server" / "migrations"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
# Operator console: a dependency-free page for the discovery/agent workflows.
# Separate from the SPA because the SPA's TypeScript source isn't in this repo
# (see README) — this way the new screens ship without touching the built bundle.
CONSOLE_DIR = BASE_DIR / "frontend" / "console"


async def check_schema() -> None:
    """Report schema state at startup. Never applies migrations.

    The app authenticates as a service principal holding DML but NOT DDL, so it
    cannot apply migrations — and should not. Replaying SQL on every boot is how a
    non-idempotent migration silently destroys customer data on a restart nobody was
    watching. server/migrator.py owns the ledger; scripts/migrate.py applies, run as
    the database owner.

    A pending migration is a state to report, not a crash: the portfolio still works
    for every table that does exist.
    """
    from server.migrator import startup_check

    pool = await db.get_pool()
    if pool is None:
        logger.info("Lakebase not configured — running in demo mode")
        return
    try:
        await startup_check(db, MIGRATIONS_DIR)
    except Exception as exc:  # noqa: BLE001 - never block startup on a status check
        logger.warning("schema check failed (%s) — continuing", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_schema()
    refresh_task = asyncio.create_task(token_refresh_loop())
    yield
    refresh_task.cancel()
    await db.close()


app = FastAPI(title="AI Value Flywheel", version="0.1.0", lifespan=lifespan)

# Correlates every log line emitted while handling a request, and emits one line per
# request with status + duration. Added before the routers so it wraps all of them.
logging_setup.install_middleware(app)

# Resolves which customer's data this request is about, into a ContextVar the value
# engine and readiness read. Added AFTER the logging middleware so account-resolution
# warnings carry a request id.
from server import accounts as _accounts  # noqa: E402
_accounts.install_middleware(app)

# --- API routers -----------------------------------------------------------
for module in (lobs, data_assets, use_cases, dependencies, values, roadmap,
               comments, funding_requests, impact, value_assumptions, genie, agents,
               analytics, live, onboarding, joint_funding, source_recommendations,
               domains, ingestion, generate, setup, taxonomy, research,
               flow, inventory, chat, branding, knowledge, proposals,
               accounts_routes, whatif):
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
        # So a "why did I get a 429?" report can be answered without a redeploy.
        "rate_limits": limits.snapshot(),
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
        return {"message": "AI Value Flywheel API. Frontend build not found.",
                "console": "/console", "docs": "/docs"}
