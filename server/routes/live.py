"""Live Databricks integration endpoints."""
from fastapi import APIRouter, Depends, Request

from ..common import current_user, write_audit
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
    actor = current_user(request)
    result = await reconcile(apply=apply)

    # A sync that actually CHANGED something moved readiness, and therefore buildable
    # value. Only snapshot when apply=true and something changed: a dry run and a
    # no-op sync are not events, and charting them would fill the trend with
    # duplicate points that imply activity where there was none.
    if apply and (result.get("asset_changes") or result.get("uc_changes")):
        from .. import snapshots as snap
        changed = len(result.get("asset_changes") or []) + \
            len(result.get("uc_changes") or [])
        await snap.capture_quietly(
            snap.REASON_SOURCE, actor=actor,
            detail=f"live sync advanced {changed} record(s) from system tables")
        # A live sync that changed records is a sensitive, source-of-truth
        # mutation from external system tables — record who ran it and what moved.
        await write_audit("live_sync", None, "sync", actor, {
            "asset_changes": len(result.get("asset_changes") or []),
            "uc_changes": len(result.get("uc_changes") or []),
        })
    return result


@router.post("/sync-genie", dependencies=[Depends(limiter("sweep"))])
async def sync_genie():
    """Mirror the portfolio into the Unity Catalog schema Genie reads."""
    return await mirror_to_uc()
