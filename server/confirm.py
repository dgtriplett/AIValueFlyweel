"""Propose/confirm token registry for agent-initiated writes.

LIFECYCLE
---------
  1. An agent endpoint validates its inputs, reads the current entity state, and
     calls `issue_token(intent, payload, before, after)`.
  2. The response carries {token, intent, expires_at, before, after}; the UI
     renders a diff card with Confirm / Cancel.
  3. On Confirm the UI POSTs /api/confirm/{token}. `consume_token` atomically
     marks it used and returns the stored payload, which is dispatched to the
     matching executor.
  4. Cancel writes nothing. The token simply expires.

WHY THE PAYLOAD IS STORED SERVER-SIDE
-------------------------------------
The alternative — have the UI send the payload back on confirm — means the only
thing binding "what the user saw" to "what gets written" is client state. Anything
that can mutate the page (an extension, an XSS, a stale tab) could then substitute
different values behind an approval the user already gave. Storing the payload at
issue time reduces the client's authority to a single bit: yes or no to the thing
it was shown.

WHY TOKENS ARE SINGLE-USE AND SHORT-LIVED
-----------------------------------------
Single-use makes a replayed confirm a no-op rather than a duplicate write (double
click, retried request, back button). The 10-minute TTL bounds the window in which
a leaked token is useful while still leaving time to read the card and ask a
follow-up question.

Tokens live in Lakebase rather than memory because Databricks Apps can run more
than one worker and can restart between the propose and the confirm; an in-memory
registry would drop the token in both cases. Expired rows are rejected at consume
time and retained for audit rather than eagerly deleted.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from .db import db

TOKEN_TTL = timedelta(minutes=10)

# Intent names. These are the contract between a propose endpoint and its
# executor; they are also what shows up in the audit log, so keep them readable.
INTENT_CREATE_USE_CASES = "createUseCases"
INTENT_UPDATE_USE_CASE = "updateUseCase"
INTENT_SET_UC_DOMAINS = "setUseCaseDomains"
INTENT_APPLY_DEPENDENCIES = "applyDependencies"
INTENT_APPLY_VALUE_MODEL = "applyValueModel"
INTENT_ADVANCE_ASSETS = "advanceAssetStatus"

VALID_INTENTS = {
    INTENT_CREATE_USE_CASES,
    INTENT_UPDATE_USE_CASE,
    INTENT_SET_UC_DOMAINS,
    INTENT_APPLY_DEPENDENCIES,
    INTENT_APPLY_VALUE_MODEL,
    INTENT_ADVANCE_ASSETS,
}


class ConfirmError(Exception):
    """Raised when a token cannot be consumed. `.reason` is UI-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def issue_token(
    intent: str,
    payload: dict,
    *,
    actor: str,
    before: dict | None = None,
    after: dict | None = None,
    summary: str | None = None,
) -> dict:
    """Store a pending write and return the confirm card's contents.

    `before`/`after` are for display only — the executor reads `payload`. Keeping
    them separate means a rendering tweak can never change what gets written.
    """
    if intent not in VALID_INTENTS:
        raise ConfirmError(f"Unknown intent {intent!r}")

    # 32 bytes of entropy: unguessable, and short enough for a URL path.
    token = secrets.token_urlsafe(32)
    expires_at = _now() + TOKEN_TTL
    await db.execute(
        """INSERT INTO confirm_tokens
           (token, intent, payload_json, before_json, after_json, summary,
            actor, expires_at)
           VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8)""",
        token, intent, json.dumps(payload), json.dumps(before or {}),
        json.dumps(after or {}), summary, actor, expires_at)
    return {
        "token": token,
        "intent": intent,
        "expires_at": expires_at.isoformat(),
        "summary": summary,
        "before": before or {},
        "after": after or {},
    }


async def consume_token(token: str, actor: str) -> tuple[str, dict]:
    """Atomically claim a token. Returns (intent, payload).

    The UPDATE's WHERE clause does the claiming: a single statement flips
    consumed_at only if the row is still unconsumed and unexpired, and RETURNING
    tells us whether we won. Checking-then-updating would leave a race in which
    two concurrent confirms both pass the check and both write.
    """
    row = await db.fetchrow(
        """UPDATE confirm_tokens
           SET consumed_at = now(), consumed_by = $2
           WHERE token = $1
             AND consumed_at IS NULL
             AND expires_at > now()
           RETURNING intent, payload_json""",
        token, actor)
    if row is not None:
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return row["intent"], (payload or {})

    # The claim failed. Distinguish why, so the UI can say something useful
    # instead of a generic error.
    existing = await db.fetchrow(
        "SELECT consumed_at, expires_at FROM confirm_tokens WHERE token = $1", token)
    if existing is None:
        raise ConfirmError("This confirmation is not recognized. Ask again to get a fresh one.")
    if existing["consumed_at"] is not None:
        raise ConfirmError("This change was already applied.")
    raise ConfirmError("This confirmation expired. Ask again to get a fresh one.")


async def peek_token(token: str) -> dict | None:
    """Read a token's card contents without consuming it (for a page reload)."""
    row = await db.fetchrow(
        """SELECT token, intent, summary, before_json, after_json, expires_at,
                  consumed_at, (expires_at <= now()) AS expired
           FROM confirm_tokens WHERE token = $1""", token)
    if row is None:
        return None
    out = dict(row)
    for key in ("before_json", "after_json"):
        if isinstance(out.get(key), str):
            try:
                out[key] = json.loads(out[key])
            except (ValueError, TypeError):
                out[key] = {}
    return out
