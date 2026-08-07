"""Chat assistant — natural language over the portfolio, with gated writes.

    POST /api/chat                 send a turn, get an answer (+ maybe a card)
    GET  /api/chat/conversations    list
    GET  /api/chat/{id}             replay one
    DELETE /api/chat/{id}           delete one

THE LOOP
--------
    user turn -> model -> [tool calls -> results -> model]* -> answer

Read tools execute inline. A write tool never writes: its handler returns a
`_propose` descriptor, and this module turns that into a confirm token, so a
chat-driven change lands in exactly the same human-approval gate as one proposed by
the generation agent. That is deliberate — the chat is the least predictable caller
in the app, so it gets no privileged path.

MAX_TOOL_ROUNDS bounds the loop. A model that keeps calling tools without answering
would otherwise burn tokens indefinitely; hitting the bound returns what it has
along with a note, rather than an error, because a partial answer is usually still
useful.
"""
from __future__ import annotations

import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import chat_tools as ct
from .. import confirm as cf
from ..common import current_user, rows_to_list
from ..config import SERVING_ENDPOINT
from ..db import db
from ..limits import CHAT_QUERY_BUDGET, BudgetExceeded, limiter, query_budget

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Enough for lookup -> detail -> propose, plus slack for a wrong first guess.
# Five proved too tight: a write needs find -> confirm-shape -> propose, and the
# model spent its budget searching before it could propose anything.
MAX_TOOL_ROUNDS = 7
# How much history to replay. Long enough to hold a conversation, short enough to
# keep the prompt affordable.
HISTORY_TURNS = 12


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None


async def _load_history(conversation_id: str) -> list[dict]:
    """Rebuild the model-facing message list from stored turns.

    Tool calls and results are replayed too, so a follow-up like "and the second
    one?" still has the list it refers to.
    """
    rows = await db.fetch("""
        SELECT role, content, tool_name, tool_args_json, tool_result_json
        FROM chat_messages WHERE conversation_id = $1
        ORDER BY created_at DESC, id DESC LIMIT $2
    """, conversation_id, HISTORY_TURNS * 3)
    messages: list[dict] = []
    for row in reversed(list(rows)):
        if row["role"] == "tool":
            # Rendered as a plain assistant note rather than a real tool message:
            # reconstructing exact tool_call_id pairing across restarts is fragile,
            # and the content is what actually matters for continuity.
            messages.append({
                "role": "assistant",
                "content": f"[used {row['tool_name']}: "
                           f"{json.dumps(row['tool_result_json'])[:800]}]",
            })
        elif row["content"]:
            messages.append({"role": row["role"], "content": row["content"]})
    return messages


async def _save(conversation_id: str, role: str, *, content: str | None = None,
                tool_name: str | None = None, args: dict | None = None,
                result: dict | None = None, token: str | None = None) -> None:
    await db.execute("""
        INSERT INTO chat_messages
          (conversation_id, role, content, tool_name, tool_args_json,
           tool_result_json, confirm_token)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)
    """, conversation_id, role, content, tool_name,
        json.dumps(args) if args is not None else None,
        json.dumps(result) if result is not None else None, token)
    await db.execute(
        "UPDATE chat_conversations SET updated_at = now() WHERE id = $1",
        conversation_id)


@router.post("", dependencies=[Depends(limiter("chat"))])
async def chat(body: ChatIn, request: Request):
    """One conversational turn.

    A thin wrapper so the query budget has a single place to be established and a
    single place to be translated into an HTTP response. The model chooses how many
    tools to call, so the database load of a turn has no natural ceiling —
    MAX_TOOL_ROUNDS bounds the model calls, not the queries underneath them.
    """
    with query_budget(CHAT_QUERY_BUDGET, "This conversation turn") as budget:
        try:
            result = await _chat_turn(body, request)
        except BudgetExceeded as exc:
            # 429, not 500: the request was too expensive, not malformed. The
            # turn's user message is already saved, so the conversation stays
            # coherent when they retry with something narrower.
            logger.warning("chat turn exceeded its query budget after %d queries",
                           budget.used)
            raise HTTPException(429, str(exc)) from exc
        result["queries"] = budget.used
        return result


async def _chat_turn(body: ChatIn, request: Request):
    actor = current_user(request)

    conversation_id = body.conversation_id
    if conversation_id:
        exists = await db.fetchrow(
            "SELECT id FROM chat_conversations WHERE id=$1", conversation_id)
        if exists is None:
            raise HTTPException(404, "Conversation not found")
    else:
        conversation_id = f"conv_{secrets.token_hex(8)}"
        await db.execute(
            "INSERT INTO chat_conversations (id, title, actor) VALUES ($1,$2,$3)",
            conversation_id, body.message[:80], actor)

    await _save(conversation_id, "user", content=body.message)

    history = await _load_history(conversation_id)
    messages = [{"role": "system", "content": ct.SYSTEM_PROMPT}, *history]

    from ..llm import get_llm_client
    from .agents import MIN_OUTPUT_TOKENS, flatten_content

    tools = ct.tool_specs()
    used_tools: list[dict] = []
    confirm_card: dict | None = None
    answer = ""
    note: str | None = None

    try:
        client = get_llm_client()
        for round_index in range(MAX_TOOL_ROUNDS):
            response = await client.chat.completions.create(
                model=SERVING_ENDPOINT,
                messages=messages,
                tools=tools,
                max_tokens=MIN_OUTPUT_TOKENS,
            )
            choice = response.choices[0]
            calls = getattr(choice.message, "tool_calls", None) or []

            if not calls:
                answer = flatten_content(choice.message.content)
                break

            # Record the model's turn so the next round sees its own decision.
            messages.append({
                "role": "assistant",
                "content": flatten_content(choice.message.content) or None,
                "tool_calls": [{
                    "id": call.id, "type": "function",
                    "function": {"name": call.function.name,
                                 "arguments": call.function.arguments},
                } for call in calls],
            })

            for call in calls:
                name = call.function.name
                tool = ct.TOOLS.get(name)
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if tool is None:
                    result = {"error": f"No tool named {name!r}."}
                else:
                    try:
                        result = await tool.handler(args, actor)
                    except BudgetExceeded:
                        # MUST propagate. Reporting this to the model as a tool
                        # error would let it keep calling tools, so the budget
                        # would bound nothing — it is a limit on the request, not
                        # a failure the model can work around.
                        raise
                    except Exception as exc:  # noqa: BLE001
                        # Surface the failure to the model so it can recover or
                        # explain, rather than failing the whole turn.
                        result = {"error": f"{type(exc).__name__}: {exc}"}

                # A write tool's proposal becomes a confirm token here — the tool
                # itself never writes.
                if result.get("_propose"):
                    try:
                        confirm_card = await cf.issue_token(
                            result["intent"], result["payload"], actor=actor,
                            before=result.get("before"), after=result.get("after"),
                            summary=result.get("summary"))
                        result = {
                            "proposed": True, "summary": result.get("summary"),
                            "awaiting_confirmation": True,
                            "note": "Not applied. The user must confirm.",
                        }
                    except cf.ConfirmError as exc:
                        result = {"error": exc.reason}

                used_tools.append({"tool": name, "args": args})
                await _save(conversation_id, "tool", tool_name=name, args=args,
                            result=result,
                            token=confirm_card["token"] if confirm_card else None)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result)[:4000]})
        else:
            # Loop exhausted without a final answer.
            note = (f"Stopped after {MAX_TOOL_ROUNDS} rounds of tool use. "
                    "Try a narrower question.")
    except BudgetExceeded:
        # Also propagate past the outer handler, or it becomes a 502 "assistant
        # unavailable" — which points support at the serving endpoint for what is
        # actually this request exceeding its own database budget.
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        raise HTTPException(
            502,
            "The assistant is unavailable. "
            + ("The serving endpoint rejected the request — the app's service "
               "principal may lack CAN_QUERY on it. "
               if "PERMISSION" in message.upper() else "")
            + f"({type(exc).__name__})")

    if not answer and not confirm_card:
        answer = ("I could not find an answer to that in the portfolio. "
                  "Try asking about use cases, data gaps, or portfolio value.")

    await _save(conversation_id, "assistant", content=answer,
                token=confirm_card["token"] if confirm_card else None)

    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "tools_used": used_tools,
        "confirm": confirm_card,
        "note": note,
        "model": SERVING_ENDPOINT,
    }


@router.get("/conversations")
async def list_conversations(limit: int = 20):
    return rows_to_list(await db.fetch("""
        SELECT c.id, c.title, c.actor, c.created_at, c.updated_at,
               (SELECT count(*) FROM chat_messages m
                WHERE m.conversation_id = c.id AND m.role <> 'tool') AS turns
        FROM chat_conversations c ORDER BY c.updated_at DESC LIMIT $1
    """, limit))


@router.get("/tools/list")
async def list_tools():
    """What the assistant can do, and which actions need confirmation.

    Exposed so the UI can set expectations up front rather than leaving users to
    discover capabilities by trial and error.
    """
    return {"tools": [{
        "name": tool.name, "description": tool.description,
        "writes": tool.writes,
    } for tool in ct.TOOLS.values()],
        "read_only_count": sum(1 for t in ct.TOOLS.values() if not t.writes),
        "write_count": sum(1 for t in ct.TOOLS.values() if t.writes),
        "note": "Write tools only ever propose; every change needs confirmation.",
    }


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str):
    conversation = await db.fetchrow(
        "SELECT * FROM chat_conversations WHERE id=$1", conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    messages = rows_to_list(await db.fetch("""
        SELECT role, content, tool_name, tool_result_json, confirm_token, created_at
        FROM chat_messages WHERE conversation_id=$1 ORDER BY created_at, id
    """, conversation_id))
    return {**dict(conversation), "messages": messages}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str):
    result = await db.execute(
        "DELETE FROM chat_conversations WHERE id=$1", conversation_id)
    return {"deleted": result is not None}
