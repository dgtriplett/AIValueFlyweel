"""Genie proxy for the floating assistant ("clippy").

If GENIE_SPACE_ID is configured, proxy questions to the Databricks Genie
Conversations API. Otherwise return a graceful placeholder so the UI still
works (the space is finalized in the live-integration chunk).
"""
import asyncio

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..common import current_user, write_audit
from ..db import db
from ..limits import limiter
from ..config import GENIE_SPACE_ID, get_oauth_token, get_workspace_host

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/genie", tags=["genie"])


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None


@router.get("/status")
async def genie_status():
    """Whether Ask works, and if not, whether this app can fix it itself.

    `can_provision` is what lets the setup screen offer a button instead of a paragraph
    of instructions. It is false when a space already exists (nothing to do) or when no
    warehouse is bound (the space cannot be created without one).
    """
    from ..config import (DATABRICKS_WAREHOUSE_ID, GENIE_MIRROR_CATALOG,
                          GENIE_MIRROR_SCHEMA)

    configured = bool(GENIE_SPACE_ID)
    return {
        "configured": configured,
        "space_id": GENIE_SPACE_ID or None,
        "can_provision": not configured and bool(DATABRICKS_WAREHOUSE_ID),
        "warehouse_bound": bool(DATABRICKS_WAREHOUSE_ID),
        "mirror_target": f"{GENIE_MIRROR_CATALOG}.{GENIE_MIRROR_SCHEMA}",
        "space_url": (f"{get_workspace_host()}/genie/rooms/{GENIE_SPACE_ID}"
                      if configured else None),
    }


@router.post("/ask")
async def ask(body: AskIn):
    if not GENIE_SPACE_ID:
        return {
            "configured": False,
            "answer": (
                "Genie isn't wired to a space yet. Once a Genie space is provisioned "
                "over the portfolio tables (use cases, data assets, value, roadmap), "
                "I'll answer questions like \"which shovel-ready generation use cases "
                "unlock the most cross-LOB value?\" in natural language."
            ),
        }

    import aiohttp

    host = get_workspace_host()
    token = get_oauth_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            if body.conversation_id:
                url = f"{base}/conversations/{body.conversation_id}/messages"
                payload = {"content": body.question}
            else:
                url = f"{base}/start-conversation"
                payload = {"content": body.question}
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
            conv_id = data.get("conversation_id") or body.conversation_id
            msg_id = (data.get("message") or {}).get("id") or data.get("message_id")

            # poll for completion
            answer = None
            sql = None
            if conv_id and msg_id:
                for _ in range(30):
                    async with session.get(
                        f"{base}/conversations/{conv_id}/messages/{msg_id}"
                    ) as mresp:
                        m = await mresp.json()
                    status = m.get("status")
                    if status in ("COMPLETED", "FAILED"):
                        for att in m.get("attachments", []) or []:
                            if "text" in att:
                                answer = att["text"].get("content")
                            elif "query" in att:
                                sql = att["query"].get("query")
                        break
                    await asyncio.sleep(1.0)
            return {
                "configured": True,
                "answer": answer or "Genie processed the request but returned no text answer.",
                "sql": sql,
                "conversation_id": conv_id,
            }
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "answer": f"Genie request failed: {exc}"}

# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
class ProvisionIn(BaseModel):
    title: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=600)
    parent_path: str | None = Field(default=None, max_length=400)
    # On by default: on a fresh install the mirror has never run, so provisioning
    # without it fails on its first step. Making the caller discover that and issue a
    # second request just moves the sequencing problem into the UI.
    sync_mirror: bool = True


@router.post("/provision", dependencies=[Depends(limiter("sweep"))])
async def provision_space(body: ProvisionIn, request: Request):
    """Create the Genie space over the portfolio mirror.

    The last manual step in an install. Everything the space needs — the warehouse, the
    mirrored tables, the vocabulary Genie cannot infer — is already known to this app,
    so asking an operator to build it by hand in another product was six steps of
    avoidable work.

    Rate-limited as 'sweep': it reads information_schema through the warehouse.
    """
    from ..config import DATABRICKS_WAREHOUSE_ID
    from ..genie_provision import GenieProvisionError, provision

    actor = current_user(request)

    # Default the title from the researched company, so a provisioned space is named
    # for the customer rather than "Genie Space 3".
    title = (body.title or "").strip()
    if not title:
        company = None
        try:
            from .. import accounts
            row = await db.fetchrow(
                "SELECT company_name FROM company_profile WHERE account_id = $1",
                await accounts.current())
            company = row["company_name"] if row else None
        except Exception:  # noqa: BLE001 - a nicer title is not worth failing for
            pass
        title = (f"{company} — Portfolio (AI Value Flywheel)" if company
                 else "AI Value Flywheel — Portfolio")

    description = (body.description or "").strip() or (
        "Natural-language questions over the use-case portfolio: value, readiness, "
        "data sources and lines of business. Created by AI Value Flywheel.")

    # Refresh the mirror first. A failure here is fatal to provisioning — a space over
    # tables that do not exist is worse than no space, because it looks configured and
    # answers every question with "table not found".
    mirror = None
    if body.sync_mirror:
        from ..live import mirror_to_uc
        mirror = await mirror_to_uc()
        if not mirror.get("ok"):
            detail = mirror.get("error") or "unknown error"
            if mirror.get("needs_grant"):
                # The error already carries the exact GRANT statements to run.
                raise HTTPException(422, f"The Genie mirror could not be written: {detail}")
            raise HTTPException(
                422, f"The Genie mirror could not be written, so there is nothing for a "
                     f"space to query: {detail}")

    try:
        result = await provision(title=title, description=description,
                                 warehouse_id=DATABRICKS_WAREHOUSE_ID,
                                 parent_path=body.parent_path)
    except GenieProvisionError as exc:
        # 409 when it is refusing because one already exists — that is a conflict, not
        # a bad request, and the distinction tells the UI whether to offer a retry.
        status = 409 if "already set" in str(exc) else 422
        raise HTTPException(status, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("Genie provisioning failed (%s: %s)",
                       type(exc).__name__, exc)
        raise HTTPException(
            502, f"Could not reach the Genie API: {type(exc).__name__}. Nothing was "
                 "created.") from exc

    await write_audit("genie_space", None, "provision", actor,
                      {"space_id": result["space_id"], "title": title})
    if mirror is not None:
        result["mirror"] = mirror.get("created")
    return result
