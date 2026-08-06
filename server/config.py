"""Dual-mode authentication and environment configuration for Grid Atlas.

Detects whether the app is running inside Databricks Apps (service principal
credentials auto-injected) or locally (uses a Databricks CLI profile).
"""
import os
from functools import lru_cache

# Databricks Apps set DATABRICKS_APP_NAME in the runtime environment.
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))

# --- Customer-configurable settings (set per your own workspace) -----------
# Local CLI profile for seeding / local dev. No workspace-specific default so
# nothing FE-specific is baked in; set DATABRICKS_PROFILE for local use.
DATABRICKS_PROFILE = os.environ.get("DATABRICKS_PROFILE") or os.environ.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT"

# Foundation Model endpoint for the AI agents (any chat model your workspace has).
SERVING_ENDPOINT = os.environ.get("SERVING_ENDPOINT", "databricks-claude-sonnet-4-5")

# Genie space over the portfolio mirror (set after creating the space).
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")

# SQL warehouse used for system-table / live-integration queries (customer picks).
# Bound as an app resource; the resource injects this env var.
DATABRICKS_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")

# Unity Catalog target for the Genie mirror (customer's own catalog/schema).
GENIE_MIRROR_CATALOG = os.environ.get("GENIE_MIRROR_CATALOG", "main")
GENIE_MIRROR_SCHEMA = os.environ.get("GENIE_MIRROR_SCHEMA", "grid_atlas")

# --- Discovery layer (Unity Catalog) ---------------------------------------
# Where the ingestion pipeline writes workspace-metadata inventory and the
# ai_query() enrichment staging table. Distinct from the Genie mirror (which is
# a read-only projection of the portfolio for NL Q&A): this is working storage
# for discovery, and the app SP needs CREATE TABLE / MODIFY / SELECT on it.
ATLAS_CATALOG = os.environ.get("ATLAS_CATALOG", "")
ATLAS_SCHEMA = os.environ.get("ATLAS_SCHEMA", "grid_atlas_discovery")


def discovery_configured() -> bool:
    """True when the discovery layer has a catalog to write into."""
    return bool(ATLAS_CATALOG and ATLAS_SCHEMA)


def atlas_fqn(table: str) -> str:
    """Fully-qualified, backtick-quoted name for a discovery-layer table."""
    return f"`{ATLAS_CATALOG}`.`{ATLAS_SCHEMA}`.`{table}`"


@lru_cache(maxsize=1)
def get_workspace_client():
    """Return an authenticated WorkspaceClient (lazily imported)."""
    from databricks.sdk import WorkspaceClient

    if IS_DATABRICKS_APP:
        return WorkspaceClient()
    return WorkspaceClient(profile=DATABRICKS_PROFILE)


def get_oauth_token() -> str:
    """Return an OAuth bearer token for Lakebase / Foundation Model auth.

    Works both in Databricks Apps (service principal) and locally (CLI/U2M).
    For OAuth/U2M auth, ``config.token`` is often ``None`` so we fall back to
    ``config.authenticate()`` which returns an ``Authorization`` header.
    """
    w = get_workspace_client()
    if getattr(w.config, "token", None):
        return w.config.token
    headers = w.config.authenticate()
    if headers and "Authorization" in headers:
        return headers["Authorization"].replace("Bearer ", "")
    raise RuntimeError("Unable to obtain OAuth token from WorkspaceClient")


def get_workspace_host() -> str:
    """Return the workspace host URL, always with an https:// scheme."""
    if IS_DATABRICKS_APP:
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            host = f"https://{host}"
        return host
    return get_workspace_client().config.host
