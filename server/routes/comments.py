"""Comments CRUD (with @mentions)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..common import current_user, row_to_dict, rows_to_list, write_audit
from ..db import db

router = APIRouter(prefix="/comments", tags=["comments"])


class CommentIn(BaseModel):
    entity_type: str
    entity_id: int
    body: str
    mentions: list[str] = []


@router.get("")
async def list_comments(entity_type: str | None = None, entity_id: int | None = None):
    if entity_type is not None and entity_id is not None:
        rows = await db.fetch(
            "SELECT * FROM comments WHERE entity_type=$1 AND entity_id=$2 ORDER BY created_at",
            entity_type, entity_id,
        )
    else:
        rows = await db.fetch("SELECT * FROM comments ORDER BY created_at DESC LIMIT 200")
    return rows_to_list(rows)


@router.post("")
async def create_comment(body: CommentIn, request: Request):
    actor = current_user(request)
    row = await db.fetchrow(
        """INSERT INTO comments (entity_type, entity_id, body, author, mentions)
           VALUES ($1,$2,$3,$4,$5) RETURNING *""",
        body.entity_type, body.entity_id, body.body, actor, body.mentions,
    )
    if row is None:
        raise HTTPException(503, "Database unavailable")
    await write_audit("comment", row["id"], "create", actor,
                      {"entity_type": body.entity_type, "entity_id": body.entity_id})
    return row_to_dict(row)


@router.delete("/{comment_id}")
async def delete_comment(comment_id: int, request: Request):
    actor = current_user(request)
    res = await db.execute("DELETE FROM comments WHERE id=$1", comment_id)
    await write_audit("comment", comment_id, "delete", actor)
    return {"deleted": res is not None}
