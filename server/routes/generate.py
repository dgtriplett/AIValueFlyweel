"""Use-case generation agent + the propose/confirm gate.

FLOW
----
    POST /api/generate/use-cases          -> preview (writes nothing)
    POST /api/generate/use-cases/commit   -> issues a confirm token
    POST /api/confirm/{token}             -> performs the write
    GET  /api/confirm/{token}             -> re-read a card after a page reload

Generation never writes. The preview is cached in Lakebase (see
migrations/004_agents.sql) so the user can pick a subset from an expensive call,
and the actual insert goes through the single-use token gate in server/confirm.py.

AFTER THE COMMIT
----------------
A committed use case is deliberately handed to the EXISTING agents rather than
duplicating their work: `detect-dependencies` wires it into the flywheel and
`estimate-value` builds a real parameterized value model from the customer's own
34 assumptions. That combination — BHE-style authoring feeding the value engine —
is the thing neither app could do alone, and it is why the generator does not
emit a dollar figure of its own.
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from .. import confirm as cf
from .. import generation as gen
from ..common import current_user, rows_to_list, write_audit
from ..config import SERVING_ENDPOINT
from ..db import db

router = APIRouter(prefix="/generate", tags=["generation"])
confirm_router = APIRouter(prefix="/confirm", tags=["generation"])

PREVIEW_TTL = timedelta(minutes=30)


class GenerateIn(BaseModel):
    lob_id: int | None = None
    lens: str = "both"
    count: int = Field(default=6, ge=gen.MIN_COUNT, le=gen.MAX_COUNT)
    time_horizon_bias: str | None = None
    value_type_bias: str | None = None
    prioritize_regulatory: bool = False
    company_name: str | None = None


class CommitIn(BaseModel):
    preview_id: str
    candidate_ids: list[str] = []
    # Bring the accepted use cases straight into the active portfolio. Off means
    # they land as catalog ideas for later triage.
    in_portfolio: bool = True


# ---------------------------------------------------------------------------
# Domain context: what the utility has vs. lacks
# ---------------------------------------------------------------------------
async def _domain_context() -> tuple[list[dict], list[dict], dict[str, dict], set[str]]:
    """Return (satisfied, unsatisfied, index_by_name, satisfied_names).

    "Satisfied" uses exactly the readiness rule — ANY serving asset curated or
    governed — so a use case the model calls 'ready' is ready by the same
    definition the rest of the app uses.
    """
    rows = await db.fetch("""
        SELECT dd.name, dd.label, dd.description, dd.category,
               COUNT(asd.data_asset_id) FILTER (
                   WHERE da.ingestion_status IN ('curated','governed')
               ) AS ready_assets
        FROM data_domains dd
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = dd.id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE COALESCE(dd.is_active, true) = true
        GROUP BY dd.id, dd.name, dd.label, dd.description, dd.category
        ORDER BY dd.category NULLS LAST, dd.label
    """)
    satisfied: list[dict] = []
    unsatisfied: list[dict] = []
    index: dict[str, dict] = {}
    for row in rows:
        entry = {"name": row["name"], "label": row["label"],
                 "description": row["description"], "category": row["category"]}
        index[row["name"]] = entry
        (satisfied if (row["ready_assets"] or 0) > 0 else unsatisfied).append(entry)
    return satisfied, unsatisfied, index, {d["name"] for d in satisfied}


async def _resolve_lob(lob_id: int | None) -> tuple[int | None, str]:
    if lob_id is None:
        return None, "the utility"
    row = await db.fetchrow("SELECT id, name FROM lobs WHERE id = $1", lob_id)
    if row is None:
        raise HTTPException(404, "Line of business not found")
    return row["id"], row["name"]


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
@router.post("/use-cases")
async def generate_use_cases(body: GenerateIn, request: Request):
    """Generate candidate use cases. Writes nothing but the preview row."""
    if body.lens not in gen.LENSES:
        raise HTTPException(422, f"lens must be one of {list(gen.LENSES)}")
    actor = current_user(request)
    lob_id, lob_name = await _resolve_lob(body.lob_id)
    satisfied, unsatisfied, index, satisfied_names = await _domain_context()

    if not index:
        raise HTTPException(
            409,
            "No data domains are defined yet, so generated use cases could not "
            "declare what data they need. Seed the reference catalog first.")
    if body.lens == "ready" and not satisfied:
        raise HTTPException(
            409,
            "No data domain is satisfied yet, so there are no 'ready' use cases to "
            "propose. Mark some sources curated/governed, or use lens='gap' to "
            "build the case for landing data.")
    if body.lens == "gap" and not unsatisfied:
        raise HTTPException(
            409, "Every data domain is already satisfied — there are no gaps to "
                 "build a case for. Use lens='ready'.")

    # Collision guard. Scoped to the LOB when one is given, because a title that
    # collides across LOBs is usually a legitimately different use case.
    if lob_id is None:
        title_rows = await db.fetch("SELECT title FROM use_cases")
    else:
        title_rows = await db.fetch(
            "SELECT title FROM use_cases WHERE lob_id = $1 OR lob_id IS NULL", lob_id)
    existing_titles = [r["title"] for r in title_rows]

    prompt = gen.build_prompt(
        company_name=body.company_name or "the utility",
        lob_name=lob_name,
        lens=body.lens,
        count=body.count,
        satisfied_domains=satisfied,
        unsatisfied_domains=unsatisfied,
        existing_titles=existing_titles,
        time_horizon_bias=body.time_horizon_bias,
        value_type_bias=body.value_type_bias,
        prioritize_regulatory=body.prioritize_regulatory,
    )

    from .agents import _llm_json
    parsed, used_llm, note = await _llm_json(
        prompt, max_tokens=4000, response_schema=gen.RESPONSE_SCHEMA)

    candidates, warnings = gen.validate_candidates(
        parsed,
        lob_name=lob_name,
        requested_lens=body.lens,
        domain_index=index,
        satisfied_names=satisfied_names,
        existing_titles=existing_titles,
        limit=body.count,
    )
    if note:
        warnings.insert(0, note)
    if not candidates:
        # 502 rather than 200-with-empty: the caller asked for candidates and got
        # none, which is a failed request, not an empty result set.
        raise HTTPException(
            502,
            "The model returned no usable use cases. "
            + (" ".join(warnings[:3]) if warnings else "Try a smaller count or a different lens."))

    preview_id = f"prev_{secrets.token_hex(8)}"
    expires_at = datetime.now(timezone.utc) + PREVIEW_TTL
    await db.execute(
        """INSERT INTO uc_generation_previews
           (id, lob_id, lens, request_json, candidates_json, model, used_llm,
            actor, expires_at)
           VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9)""",
        preview_id, lob_id, body.lens, json.dumps(body.model_dump()),
        json.dumps(candidates), SERVING_ENDPOINT if used_llm else "heuristic",
        used_llm, actor, expires_at)

    return {
        "preview_id": preview_id,
        "lob_id": lob_id,
        "lob_name": lob_name,
        "lens": body.lens,
        "model": SERVING_ENDPOINT if used_llm else "heuristic",
        "used_llm": used_llm,
        "candidates": candidates,
        "summary": gen.summarize_batch(candidates),
        "warnings": warnings,
        "expires_at": expires_at.isoformat(),
    }


@router.get("/use-cases/{preview_id}")
async def get_preview(preview_id: str):
    """Re-read a preview (page reload, or a second reviewer opening the link)."""
    row = await db.fetchrow(
        "SELECT *, (expires_at <= now()) AS expired FROM uc_generation_previews "
        "WHERE id = $1", preview_id)
    if row is None:
        raise HTTPException(404, "Preview not found or already cleaned up")
    candidates = row["candidates_json"]
    if isinstance(candidates, str):
        candidates = json.loads(candidates)
    return {
        "preview_id": row["id"],
        "lob_id": row["lob_id"],
        "lens": row["lens"],
        "model": row["model"],
        "used_llm": row["used_llm"],
        "candidates": candidates,
        "summary": gen.summarize_batch(candidates),
        "expired": row["expired"],
        "committed_at": row["committed_at"],
        "expires_at": row["expires_at"],
    }


# ---------------------------------------------------------------------------
# Commit -> propose (issues a confirm token; still writes no use cases)
# ---------------------------------------------------------------------------
@router.post("/use-cases/commit")
async def commit_use_cases(body: CommitIn, request: Request):
    """Propose inserting the selected candidates. Returns a confirm card.

    Nothing is written until POST /api/confirm/{token}: the preview is model
    output, and creating portfolio rows from it is exactly the kind of write that
    should need an explicit human yes.
    """
    actor = current_user(request)
    row = await db.fetchrow(
        "SELECT * FROM uc_generation_previews WHERE id = $1", body.preview_id)
    if row is None:
        raise HTTPException(404, "Preview not found or already cleaned up")
    if row["committed_at"] is not None:
        raise HTTPException(409, "This preview was already committed.")
    # Expiry is checked here rather than only at issue time so a stale tab can't
    # commit output generated against a long-gone domain state.
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "This preview expired. Generate a fresh batch.")

    candidates = row["candidates_json"]
    if isinstance(candidates, str):
        candidates = json.loads(candidates)
    by_id = {c["candidate_id"]: c for c in candidates}

    wanted = body.candidate_ids or list(by_id)
    chosen = [by_id[cid] for cid in wanted if cid in by_id]
    unknown = [cid for cid in wanted if cid not in by_id]
    if not chosen:
        raise HTTPException(422, "None of the given candidate_ids are in this preview.")

    lob_id, lob_name = await _resolve_lob(row["lob_id"])
    token = await cf.issue_token(
        cf.INTENT_CREATE_USE_CASES,
        {
            "preview_id": body.preview_id,
            "lob_id": lob_id,
            "lens": row["lens"],
            "in_portfolio": body.in_portfolio,
            "candidates": chosen,
        },
        actor=actor,
        before={"portfolio_use_cases": await _portfolio_count()},
        after={"portfolio_use_cases": await _portfolio_count()
               + (len(chosen) if body.in_portfolio else 0)},
        summary=(f"Create {len(chosen)} use case{'s' if len(chosen) != 1 else ''} "
                 f"in {lob_name}"
                 + (" and add them to the portfolio" if body.in_portfolio else
                    " as catalog ideas")),
    )
    return {
        **token,
        "candidates": chosen,
        "unknown_candidate_ids": unknown,
    }


async def _portfolio_count() -> int:
    row = await db.fetchrow("SELECT count(*) AS n FROM use_cases WHERE in_portfolio = true")
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Confirm — the only place agent-proposed writes actually happen
# ---------------------------------------------------------------------------
async def _execute_create_use_cases(payload: dict, actor: str) -> dict:
    """Insert the accepted candidates, their domain requirements, and audit rows."""
    lob_id = payload.get("lob_id")
    in_portfolio = bool(payload.get("in_portfolio", True))
    lens = payload.get("lens")
    preview_id = payload.get("preview_id")

    domain_rows = await db.fetch("SELECT id, name FROM data_domains")
    domain_id_by_name = {r["name"]: r["id"] for r in domain_rows}

    created = []
    for candidate in payload.get("candidates", []):
        # Re-check the collision at write time: another user may have created this
        # title between the propose and the confirm.
        clash = await db.fetchrow(
            "SELECT id FROM use_cases WHERE lower(title) = lower($1)", candidate["title"])
        if clash is not None:
            continue

        row = await db.fetchrow(
            """INSERT INTO use_cases
               (title, description, lob_id, sub_vertical, status, effort_tshirt,
                origin, in_portfolio, created_by, generated_from_preview,
                generation_lens)
               VALUES ($1,$2,$3,$4,'not_started',$5,'custom',$6,$7,$8,$9)
               RETURNING id, title""",
            candidate["title"],
            _describe(candidate),
            lob_id,
            candidate.get("sub_vertical") or "cross",
            candidate.get("effort_tshirt") or gen.DEFAULT_EFFORT,
            in_portfolio, actor, preview_id, candidate.get("lens") or lens)
        if row is None:
            continue
        uc_id = row["id"]

        for necessity, field in (("required", "required_domains"),
                                 ("helpful", "helpful_domains")):
            for entry in candidate.get(field) or []:
                domain_id = domain_id_by_name.get(entry.get("name"))
                if domain_id is None:
                    continue
                await db.execute(
                    """INSERT INTO uc_requires_domain
                       (use_case_id, domain_id, necessity, rationale, mapped_by)
                       VALUES ($1,$2,$3,$4,'generation')
                       ON CONFLICT (use_case_id, domain_id) DO NOTHING""",
                    uc_id, domain_id, necessity,
                    "Declared by the use-case generation agent.")

        created.append({"id": uc_id, "title": row["title"], "lens": candidate.get("lens")})
        await write_audit("use_case", uc_id, "generate", actor,
                          {"preview_id": preview_id, "lens": candidate.get("lens")})

    if preview_id:
        await db.execute(
            "UPDATE uc_generation_previews SET committed_at = now() WHERE id = $1",
            preview_id)

    return {
        "created": created,
        "created_count": len(created),
        "skipped_duplicates": len(payload.get("candidates", [])) - len(created),
        # The caller should run these next; a generated use case is only fully
        # useful once it is wired into the flywheel and valued.
        "next_steps": [
            {"endpoint": "/api/agents/detect-dependencies",
             "why": "wire each new use case into the dependency graph",
             "use_case_ids": [c["id"] for c in created]},
            {"endpoint": "/api/agents/estimate-value",
             "why": "build a parameterized value model from your own assumptions",
             "use_case_ids": [c["id"] for c in created]},
        ],
    }


def _describe(candidate: dict) -> str:
    """Fold the model's prose into the single description column.

    business_value and value_rationale are kept because they are the parts a
    reviewer reads; dropping them would lose the reasoning behind the proposal.
    """
    parts = [candidate.get("description") or ""]
    if candidate.get("business_value"):
        parts.append(f"Business value: {candidate['business_value']}")
    if candidate.get("value_rationale"):
        parts.append(f"Value rationale: {candidate['value_rationale']}")
    return "\n\n".join(p.strip() for p in parts if p.strip())


async def _research_executor(payload: dict, actor: str) -> dict:
    """Apply calibrated value assumptions. Owned by routes/research.py.

    Imported lazily: research.py imports this module's confirm helpers, so a
    module-scope import would close a cycle.
    """
    from .research import execute_apply_research
    return await execute_apply_research(payload, actor)


# Intent -> executor. Adding an intent means adding a row here; an intent with no
# executor is rejected at confirm time rather than silently succeeding.
_EXECUTORS = {
    cf.INTENT_CREATE_USE_CASES: _execute_create_use_cases,
    # Registered here rather than in research.py because /api/confirm lives in this
    # module — every agent-proposed write shares that one gate.
    cf.INTENT_APPLY_RESEARCH: _research_executor,
}


@confirm_router.get("/{token}")
async def read_confirm(token: str = Path(..., min_length=8)):
    """Re-read a pending confirm card without consuming it."""
    card = await cf.peek_token(token)
    if card is None:
        raise HTTPException(404, "Confirmation not found")
    return card


@confirm_router.post("/{token}")
async def apply_confirm(request: Request, token: str = Path(..., min_length=8)):
    """Consume a token and perform its write. Single-use."""
    actor = current_user(request)
    try:
        intent, payload = await cf.consume_token(token, actor)
    except cf.ConfirmError as exc:
        # 409: the token is real but no longer usable (already applied, expired).
        raise HTTPException(409, exc.reason)

    executor = _EXECUTORS.get(intent)
    if executor is None:
        raise HTTPException(500, f"No executor is registered for intent {intent!r}")

    started = time.monotonic()
    result = await executor(payload, actor)
    return {
        "ok": True,
        "intent": intent,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        **result,
    }


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
@router.post("/cleanup")
async def cleanup(request: Request):
    """Delete expired previews and consumed/expired tokens.

    Called by the UI occasionally rather than on a schedule — these tables are
    small and correctness never depends on the sweep (expiry is enforced at read
    time), so a cron would be more machinery than the problem warrants.
    """
    actor = current_user(request)
    previews = await db.execute(
        "DELETE FROM uc_generation_previews WHERE expires_at < now() - INTERVAL '1 day'")
    tokens = await db.execute(
        "DELETE FROM confirm_tokens WHERE expires_at < now() - INTERVAL '7 days'")
    await write_audit("generation", None, "cleanup", actor,
                      {"previews": previews, "tokens": tokens})
    return {"ok": True, "previews_deleted": previews, "tokens_deleted": tokens}


# ---------------------------------------------------------------------------
# Research — chain compression for "draft a use case about X"
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "using", "use",
    "case", "data", "analytics", "based", "management", "system", "our", "their",
}


def _tokenize(topic: str) -> list[str]:
    """Words worth scoring on. Deliberately simple — lowercase, split, drop short
    and generic terms. A real tokenizer would be over-engineering here; missing a
    synonym is cheaper than a flaky scoring function nobody can reason about."""
    words = [w.strip("-_/") for w in (topic or "").lower().replace("/", " ").split()]
    return [w for w in words if len(w) > 3 and w not in _STOPWORDS]


@router.get("/research")
async def research(topic: str, limit: int = 8):
    """One structured brief for a use-case topic, instead of 4-6 round trips.

    Without this, drafting a use case means separately fetching similar use cases
    (for style and collision-checking), the domain vocabulary, and which domains
    are satisfied — then aggregating by hand. Doing it server-side is faster, and
    more importantly it is consistent: the same brief every time, rather than
    depending on which lookups the caller remembered to make.

    Returns suggestions, not decisions. Nothing is written.
    """
    tokens = _tokenize(topic)
    if not tokens:
        raise HTTPException(
            422, "Give a more specific topic — at least one word longer than three "
                 "characters that isn't a generic term like 'data' or 'use case'.")

    # Score existing use cases: a token in the title is a much stronger signal
    # than one buried in a paragraph of description, hence 3x weighting.
    rows = await db.fetch("""
        SELECT uc.id, uc.title, uc.description, uc.status, uc.effort_tshirt,
               uc.sub_vertical, uc.in_portfolio, l.name AS lob_name,
               (SELECT count(*) * 3 FROM unnest($1::text[]) t
                 WHERE position(t in lower(uc.title)) > 0)
               + (SELECT count(*) FROM unnest($1::text[]) t
                   WHERE position(t in lower(coalesce(uc.description,''))) > 0)
               AS score
        FROM use_cases uc
        LEFT JOIN lobs l ON l.id = uc.lob_id
        ORDER BY score DESC, uc.title
        LIMIT $2
    """, tokens, limit)
    similar = [dict(r) for r in rows if (r["score"] or 0) > 0]

    satisfied, unsatisfied, index, satisfied_names = await _domain_context()

    # Domains the closest existing use cases already depend on — the best
    # available prior for what a new use case on this topic will need.
    suggested_domains: list[dict] = []
    if similar:
        domain_rows = await db.fetch("""
            SELECT dd.name, dd.label, urd.necessity, count(*) AS uses
            FROM uc_requires_domain urd
            JOIN data_domains dd ON dd.id = urd.domain_id
            WHERE urd.use_case_id = ANY($1::int[])
            GROUP BY dd.name, dd.label, urd.necessity
            ORDER BY uses DESC, dd.label
            LIMIT 12
        """, [s["id"] for s in similar])
        suggested_domains = [
            {**dict(r), "satisfied": r["name"] in satisfied_names} for r in domain_rows]

    warnings = []
    exact = [s for s in similar if s["title"].lower() == topic.strip().lower()]
    if exact:
        warnings.append(
            f"A use case titled {exact[0]['title']!r} already exists (#{exact[0]['id']}).")
    if len(tokens) == 1:
        warnings.append("Single-word topic — matches may be broad.")

    return {
        "topic": topic,
        "tokens_used": tokens,
        "matched_count": len(similar),
        "similar_use_cases": similar,
        "suggestions": {
            "domains": suggested_domains,
            "lob_name": similar[0]["lob_name"] if similar else None,
            "effort_tshirt": similar[0]["effort_tshirt"] if similar else gen.DEFAULT_EFFORT,
            "sub_vertical": similar[0]["sub_vertical"] if similar else gen.DEFAULT_SUB_VERTICAL,
            # A topic whose prior domains are all satisfied is a 'ready' candidate.
            "likely_lens": ("ready" if suggested_domains and all(
                d["satisfied"] for d in suggested_domains
                if d["necessity"] == "required") else "gap"),
        },
        "domain_counts": {"satisfied": len(satisfied), "unsatisfied": len(unsatisfied),
                          "total": len(index)},
        "warnings": warnings,
    }


@router.get("/previews")
async def list_previews(limit: int = 20):
    rows = await db.fetch(
        """SELECT id, lob_id, lens, model, used_llm, actor, created_at, expires_at,
                  committed_at, (expires_at <= now()) AS expired,
                  jsonb_array_length(candidates_json) AS candidate_count
           FROM uc_generation_previews ORDER BY created_at DESC LIMIT $1""", limit)
    return rows_to_list(rows)
