"""AI Value Flywheel - FastAPI entry point.

Serves the REST API under /api/* and the built React SPA from frontend/dist.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server import limits, logging_setup
from server.config import AI_QUERY_ENDPOINT, IS_DATABRICKS_APP, SERVING_ENDPOINT, GENIE_SPACE_ID
from server.db import DatabaseUnavailable, db, token_refresh_loop
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
    exports,
    accounts as accounts_routes,
    whatif,
    snapshots as snapshot_routes,
    quality,
    sync_packages,
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


def app_env() -> str:
    value = (os.environ.get("APP_ENV") or "DEV").strip().upper()
    return value if value in {"DEV", "PROD"} else value[:12]


def env_pill_html() -> str:
    env = app_env()
    is_prod = env == "PROD"
    bg = "#123c2f" if is_prod else "#3b2c06"
    border = "#00a972" if is_prod else "#ffab00"
    color = "#9ed6c4" if is_prod else "#ffdb96"
    return (
        '<div id="app-env-pill" aria-label="Application environment" '
        f'style="position:fixed;right:14px;bottom:14px;z-index:2147483647;'
        f'padding:5px 10px;border-radius:999px;border:1px solid {border};'
        f'background:{bg};color:{color};font:700 11px DM Mono,ui-monospace,monospace;'
        'letter-spacing:.08em;box-shadow:0 8px 24px rgba(0,0,0,.35);'
        'pointer-events:none;">'
        f'{env}</div>'
    )


def _contained_path(root: Path, requested: str) -> Path | None:
    """Resolve `requested` under `root`, or None if it lands outside.

    Decides containment WITHOUT asking whether the target exists — deliberately, so
    the escape verdict cannot become a filesystem oracle. `escapes_root` and
    `safe_static_path` both build on this so there is exactly one containment rule.

    Containment is decided on the RESOLVED paths, and the root is resolved too, so a
    symlink pointing out of the tree cannot smuggle a path past the comparison —
    collapsing `..` alone is not enough. `strict=False` because a path that does not
    exist must still be classifiable rather than raising.
    """
    if not requested:
        return None
    # A NUL byte truncates the path in some C-level filesystem calls, so a name
    # containing one must never reach the OS.
    if "\x00" in requested:
        return None
    try:
        root_resolved = root.resolve(strict=False)
        candidate = (root_resolved / requested).resolve(strict=False)
    except (OSError, ValueError):
        # An overlong or otherwise unrepresentable path resolves to nothing useful.
        return None
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    return candidate


def escapes_root(root: Path, requested: str) -> bool:
    """Whether `requested` tries to leave `root` — a traversal attempt.

    Existence is never consulted, so `/..%2fapp.py` (a real file) and
    `/..%2fnope.txt` (not a file) are both simply "escaped". The caller can turn this
    into a flat 404 without revealing which paths exist outside the tree.

    The empty path is not an escape — it means "/" and belongs to the SPA.
    """
    if not requested:
        return False
    return _contained_path(root, requested) is None


def safe_static_path(root: Path, requested: str) -> Path | None:
    """The file `requested` names inside `root`, or None if it escapes or is absent.

    WHY THIS EXISTS
    ---------------
    The SPA catch-all captures `{full_path:path}`, which happily contains `..`.
    Joining that onto a directory and serving the result is an unauthenticated
    arbitrary file read: `GET /..%2f..%2fapp.py` returned this file's source, and
    the same trick reaches anything the app process can read. This route is the
    app's front door, matched before any auth runs, so the check has to live here.

    Returns None for anything that is not a real contained file — escape, directory,
    missing file, empty path — so a caller can never serve a path it did not verify.
    """
    candidate = _contained_path(root, requested)
    if candidate is None:
        return None
    try:
        if not candidate.is_file():
            return None
    except OSError:
        return None
    return candidate


def spa_index_response() -> HTMLResponse:
    html = (FRONTEND_DIST / "index.html").read_text()
    pill = env_pill_html()
    html = html.replace("</body>", f"  {pill}\n  </body>")
    return HTMLResponse(html)


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


@app.exception_handler(DatabaseUnavailable)
async def database_unavailable_handler(request, exc: DatabaseUnavailable):
    """A configured-but-unreachable Lakebase is a 503, not a 500 and not a 200.

    The query helpers raise rather than returning an empty result, because an empty
    result reads as "no data" and silently unscopes the request. That raise has to
    land somewhere useful: without this handler it is an opaque 500 with a stack
    trace, and the actual cause — the database is down — is only in the log.

    503 is the honest status: the request was fine, the dependency is not, and a
    retry may succeed.
    """
    logger.error("refusing %s: %s", request.url.path, exc)
    return JSONResponse(
        {"error": "The database is temporarily unavailable, so this request cannot "
                  "be served. No data was read or written.",
         "detail": str(exc)},
        status_code=503)

# --- API routers -----------------------------------------------------------
for module in (lobs, data_assets, use_cases, dependencies, values, roadmap,
               comments, funding_requests, impact, value_assumptions, genie, agents,
               analytics, live, onboarding, joint_funding, source_recommendations,
               domains, ingestion, generate, setup, taxonomy, research,
               flow, inventory, chat, branding, knowledge, proposals,
               exports, accounts_routes, whatif, snapshot_routes, quality):
    app.include_router(module.router, prefix="/api")

app.include_router(sync_packages.router, prefix="/api")

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
    """Liveness plus an honest database verdict.

    Reports unhealthy — with a 503 — when Lakebase is CONFIGURED but unreachable.
    Previously this said "healthy" through a full outage, because a failed pool fell
    into the same demo-mode branch as an unconfigured one: reads returned empty, the
    UI showed "no data yet" over a populated database, and the probe agreed
    everything was fine. An outage that reports healthy is one nobody gets paged for.

    A genuinely unconfigured Lakebase (no PGHOST) stays healthy: empty reads are the
    right answer for a local checkout or a demo, and failing the probe there would
    make a working install look broken.
    """
    pool = await db.get_pool()
    connected = False
    counts: dict = {}
    query_error: str | None = None
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                connected = True
                for table in ("lobs", "data_assets", "use_cases"):
                    counts[table] = await conn.fetchval(f"SELECT count(*) FROM {table}")
        except Exception as exc:  # noqa: BLE001
            query_error = str(exc)
            counts = {"error": query_error}

    # Configured-but-unreachable, or connected but unable to query: both mean the
    # data this app exists to show is not being shown.
    degraded = db.is_degraded or (pool is not None and not connected) \
        or query_error is not None
    payload = {
        "status": "unhealthy" if degraded else "healthy",
        "app": "grid-atlas",
        "app_env": app_env(),
        "environment": "databricks" if IS_DATABRICKS_APP else "local",
        "db_connected": connected,
        "demo_mode": db.is_demo_mode,
        "serving_endpoint": SERVING_ENDPOINT,
        "ai_query_endpoint": AI_QUERY_ENDPOINT,
        "genie_space_configured": bool(GENIE_SPACE_ID),
        "counts": counts,
        # So a "why did I get a 429?" report can be answered without a redeploy.
        "rate_limits": limits.snapshot(),
    }
    if degraded:
        # Named plainly so the operator sees the cause in the probe output rather
        # than having to go and read the app log.
        payload["error"] = db.last_error or query_error or "database unreachable"
        payload["detail"] = (
            "Lakebase is configured (PGHOST is set) but unreachable, so reads are "
            "empty and writes return 503. This is NOT demo mode.")
        return JSONResponse(payload, status_code=503)
    return payload


@app.get("/api/runtime")
async def runtime():
    return {
        "app": "grid-atlas",
        "app_env": app_env(),
        "databricks_app": bool(IS_DATABRICKS_APP),
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
        if escapes_root(FRONTEND_DIST, full_path):
            # An explicit 404 for a path that tried to leave the directory. Serving
            # the SPA with a 200 here (the previous behaviour) meant a probe got the
            # same success status as a real page, so nothing in the response said
            # "that was rejected" — bad for the caller and worse for anyone reading
            # access logs looking for traversal attempts.
            #
            # This is NOT a filesystem oracle: `escapes_root` decides on the path's
            # SHAPE after resolution and never asks whether the target exists, so
            # /..%2fapp.py and /..%2fno-such-file both 404 identically. What leaks is
            # "you tried to escape", which the caller already knows.
            return JSONResponse({"error": "Not found"}, status_code=404)
        candidate = safe_static_path(FRONTEND_DIST, full_path)
        if candidate is not None:
            return FileResponse(str(candidate))
        # Inside the root but not a file: a client-side route like /portfolio. The
        # SPA renders it.
        return spa_index_response()
else:
    @app.get("/")
    async def root():
        return {"message": "AI Value Flywheel API. Frontend build not found.",
                "console": "/console", "docs": "/docs"}
