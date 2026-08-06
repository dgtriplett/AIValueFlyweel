"""Genie proxy for the floating assistant ("clippy").

If GENIE_SPACE_ID is configured, proxy questions to the Databricks Genie
Conversations API. Otherwise return a graceful placeholder so the UI still
works (the space is finalized in the live-integration chunk).
"""
import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import GENIE_SPACE_ID, get_oauth_token, get_workspace_host

router = APIRouter(prefix="/genie", tags=["genie"])


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None


@router.get("/status")
async def genie_status():
    return {"configured": bool(GENIE_SPACE_ID), "space_id": GENIE_SPACE_ID or None}


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
