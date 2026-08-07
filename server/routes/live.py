"""Live Databricks integration endpoints."""
from fastapi import APIRouter, Depends, Request

from ..common import current_user
from ..lineage import system_tables_available
from ..limits import limiter
from ..live import mirror_to_uc, reconcile

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/status")
async def status():
    return {"system_tables": await system_tables_available()}


@router.post("/sync", dependencies=[Depends(limiter("sweep"))])
async def sync(request: Request, apply: bool = True):
    """Reconcile ingestion_status + UC live-status from system tables.
    Returns a diff of what changed (or would change if apply=false)."""
    _ = current_user(request)
    return await reconcile(apply=apply)


@router.post("/sync-genie", dependencies=[Depends(limiter("sweep"))])
async def sync_genie():
    """Mirror the portfolio into the Unity Catalog schema Genie reads."""
    return await mirror_to_uc()
