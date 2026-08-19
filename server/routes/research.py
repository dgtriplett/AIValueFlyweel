"""Company research — cold-start a brand-new account.

    POST /api/research/company        research profile + calibrate assumptions
    GET  /api/research/company        the stored profile
    GET  /api/research/assumptions    the latest proposals, with provenance
    POST /api/research/apply          propose applying them (confirm-gated)
    GET  /api/research/runs           history

WHAT THIS IS FOR
----------------
The generation agent proposes use cases grounded in data the utility already has.
That is the right tool once an inventory exists — and useless on day one at a new
account, where nothing has been ingested and every dollar figure comes from generic
defaults describing a hypothetical utility.

This fills that gap: name a company, and get a profile, calibrated value
assumptions, and (optionally) starter lines of business. See server/research.py for
why the assumption calibration is the substantive part and how confidence is
assigned.

Applying goes through the confirm gate, because recalibrating the assumptions
re-quantifies the entire portfolio in one move.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import accounts
from .. import confirm as cf
from .. import research as rs
from ..common import current_user, rows_to_list, write_audit
from ..config import SERVING_ENDPOINT
from ..db import db
from ..limits import limiter

router = APIRouter(prefix="/research", tags=["research"])


class ResearchIn(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    # Assumption calibration is the point, so it defaults on. The profile is a
    # prerequisite for it (it grounds the numbers) and is always fetched.
    calibrate_assumptions: bool = True
    propose_lobs: bool = True


class ApplyIn(BaseModel):
    run_id: int | None = None
    # Which keys to apply. Empty = all from the run. Lets a reviewer take the
    # high-confidence numbers and leave the rest at defaults.
    keys: list[str] = []
    apply_profile: bool = True
    apply_lobs: bool = False


async def _start_run(company: str, scope: list[str], actor: str) -> int | None:
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            "INSERT INTO research_runs (account_id, company_name, scope, actor) "
            "VALUES ($1,$2,$3,$4) RETURNING id",
            account_id, company, scope, actor)
    else:
        row = await db.fetchrow(
            "INSERT INTO research_runs (company_name, scope, actor) "
            "VALUES ($1,$2,$3) RETURNING id", company, scope, actor)
    return row["id"] if row else None


async def _finish_run(run_id: int | None, status: str, stats: dict,
                      warnings: list[str], model: str, used_llm: bool,
                      error: str | None = None) -> None:
    if run_id is None:
        return
    await db.execute(
        "UPDATE research_runs SET status=$1, stats_json=$2::jsonb, warnings=$3, "
        "model=$4, used_llm=$5, error=$6, finished_at=now() WHERE id=$7",
        status, json.dumps(stats), warnings, model, used_llm, error, run_id)


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------
@router.post("/company", dependencies=[Depends(limiter("research"))])
async def research_company(body: ResearchIn, request: Request):
    """Research a company. Writes only the profile + proposals, never assumptions."""
    actor = current_user(request)
    company = body.company_name.strip()
    scope = ["profile"]
    if body.calibrate_assumptions:
        scope.append("assumptions")
    if body.propose_lobs:
        scope.append("lobs")

    run_id = await _start_run(company, scope, actor)
    from .agents import _llm_json

    warnings: list[str] = []
    stats: dict = {}
    used_llm = False

    try:
        # --- 1. profile -----------------------------------------------------
        parsed, profile_llm, note = await _llm_json(
            rs.build_profile_prompt(company), max_tokens=2000,
            response_schema=rs.RESPONSE_SCHEMA_PROFILE)
        used_llm = used_llm or profile_llm
        if note:
            warnings.append(note)
        try:
            profile = rs.parse_profile(parsed)
        except rs.ResearchError as exc:
            await _finish_run(run_id, "failed", stats, warnings,
                              SERVING_ENDPOINT, used_llm, str(exc))
            raise HTTPException(
                502,
                f"Could not research {company}: {exc} The model may not recognize "
                "this company — check the spelling, or use the full legal name.")

        # The profile IS stored: it is a statement about the world, not a change to
        # the portfolio, and every later pass needs it as grounding.
        account_id = await accounts.current()
        await db.execute(
            """INSERT INTO company_profile
               (account_id, company_name, utility_type, segments, service_territory,
                regulator, iso_rto, description, research_notes, researched_at,
                researched_by, model)
               VALUES ($11,$1,$2,$3,$4,$5,$6,$7,$8,now(),$9,$10)
               ON CONFLICT (account_id) DO UPDATE SET
                 company_name=EXCLUDED.company_name,
                 utility_type=EXCLUDED.utility_type,
                 segments=EXCLUDED.segments,
                 service_territory=EXCLUDED.service_territory,
                 regulator=EXCLUDED.regulator, iso_rto=EXCLUDED.iso_rto,
                 description=EXCLUDED.description,
                 research_notes=EXCLUDED.research_notes,
                 researched_at=now(), researched_by=EXCLUDED.researched_by,
                 model=EXCLUDED.model, updated_at=now()""",
            profile["company_name"], profile["utility_type"], profile["segments"],
            profile["service_territory"], profile["regulator"], profile["iso_rto"],
            profile["description"], profile["caveats"], actor,
            SERVING_ENDPOINT if profile_llm else "heuristic", account_id)
        stats["profile_confidence"] = profile["confidence"]

        # --- 2. assumptions -------------------------------------------------
        proposals: list[dict] = []
        summary: dict = {}
        if body.calibrate_assumptions:
            if account_id is not None:
                current = rows_to_list(await db.fetch(
                    """SELECT DISTINCT ON (key) key, label, value, unit, category
                       FROM value_assumptions
                       WHERE account_id = $1 OR account_id IS NULL
                       ORDER BY key, (account_id IS NULL)""",
                    account_id))
                current.sort(key=lambda r: (r.get("category") or "", r.get("key") or ""))
            else:
                current = rows_to_list(await db.fetch(
                    "SELECT key, label, value, unit, category FROM value_assumptions "
                    "ORDER BY category, key"))
            if not current:
                warnings.append(
                    "No value assumptions exist yet — seed the reference library "
                    "first, then re-run research to calibrate them.")
            else:
                parsed, assumption_llm, note = await _llm_json(
                    rs.build_assumption_prompt(company, profile, current),
                    max_tokens=8000,
                    response_schema=rs.RESPONSE_SCHEMA_ASSUMPTIONS)
                used_llm = used_llm or assumption_llm
                if note:
                    warnings.append(note)
                proposals, assumption_warnings = rs.parse_assumptions(parsed, current)
                warnings.extend(assumption_warnings)
                summary = rs.summarize_assumptions(proposals)
                stats["assumptions"] = summary

                # Proposals are recorded (not applied) so they survive a page
                # reload and can be reviewed by someone other than the requester.
                for proposal in proposals:
                    if account_id is not None:
                        await db.execute(
                            """INSERT INTO assumption_research
                               (account_id, key, value_before, value_proposed,
                                confidence, rationale, basis, research_run)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                            account_id, proposal["key"], proposal["value_before"],
                            proposal["value_proposed"], proposal["confidence"],
                            proposal["rationale"], proposal["basis"], run_id)
                    else:
                        await db.execute(
                            """INSERT INTO assumption_research
                               (key, value_before, value_proposed, confidence,
                                rationale, basis, research_run)
                               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                            proposal["key"], proposal["value_before"],
                            proposal["value_proposed"], proposal["confidence"],
                            proposal["rationale"], proposal["basis"], run_id)

        # --- 3. lines of business ------------------------------------------
        lob_proposals: list[dict] = []
        if body.propose_lobs:
            existing = {r["name"].lower() for r in await db.fetch("SELECT name FROM lobs")}
            # Derived from the profile's segments rather than a separate LLM call:
            # the mapping from segment to LOB is deterministic, and a model asked
            # for it just re-words the segment list.
            candidates = {
                "generation": ("Generation", "Owned generation fleet operations."),
                "transmission": ("Transmission", "High-voltage transmission system."),
                "distribution": ("Distribution", "Distribution grid and field operations."),
                "retail": ("Customer", "Retail customer, billing, and contact centre."),
            }
            for segment in profile["segments"]:
                name, description = candidates.get(segment, (None, None))
                if name and name.lower() not in existing:
                    lob_proposals.append({"name": name, "description": description})
            # Every utility has these regardless of segment mix.
            for name, description in (
                ("Corporate Services", "Finance, HR, supply chain, and shared services."),
                ("Safety & Compliance", "Safety, environmental, and regulatory compliance."),
            ):
                if name.lower() not in existing:
                    lob_proposals.append({"name": name, "description": description})
            stats["lobs_proposed"] = len(lob_proposals)

        status = "partial" if warnings else "succeeded"
        await _finish_run(run_id, status, stats, warnings,
                          SERVING_ENDPOINT if used_llm else "heuristic", used_llm)
        await write_audit("research", run_id, "company", actor,
                          {"company": company, "scope": scope})

        return {
            "run_id": run_id,
            "company": profile,
            "assumptions": proposals,
            "assumption_summary": summary,
            "lobs": lob_proposals,
            "model": SERVING_ENDPOINT if used_llm else "heuristic",
            "used_llm": used_llm,
            "warnings": warnings,
            # Nothing about the portfolio has changed yet; say so explicitly.
            "applied": False,
            "next": "Review the calibrated values, then POST /api/research/apply "
                    "to stage them for confirmation.",
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await _finish_run(run_id, "failed", stats, warnings,
                          SERVING_ENDPOINT, used_llm, str(exc))
        raise HTTPException(500, f"Research failed: {exc}")


@router.get("/company")
async def get_company():
    row = await db.fetchrow(
        "SELECT * FROM company_profile WHERE account_id = $1",
        await accounts.current())
    if row is None:
        return {"researched": False,
                "hint": "POST /api/research/company with a company name."}
    return {"researched": True, **dict(row)}


@router.get("/assumptions")
async def get_assumption_research(run_id: int | None = None):
    """The latest proposal per assumption, with provenance.

    Defaults to the most recent run so the review screen shows one coherent set
    rather than a mixture of passes.
    """
    if run_id is None:
        account_id = await accounts.current()
        if account_id is not None:
            latest = await db.fetchrow(
                "SELECT id FROM research_runs WHERE account_id=$1 AND status <> 'failed' "
                "ORDER BY started_at DESC LIMIT 1", account_id)
        else:
            latest = await db.fetchrow(
                "SELECT id FROM research_runs WHERE status <> 'failed' "
                "ORDER BY started_at DESC LIMIT 1")
        run_id = latest["id"] if latest else None
    if run_id is None:
        return {"run_id": None, "assumptions": [], "summary": {}}

    account_id = await accounts.current()
    if account_id is not None:
        owned = await db.fetchrow(
            "SELECT id FROM research_runs WHERE id=$1 AND account_id=$2",
            run_id, account_id)
        if owned is None:
            raise HTTPException(404, "Research run not found")

    rows = await db.fetch("""
        SELECT ar.*, va.label, va.unit, va.category, va.value AS value_current,
               va.source AS current_source
        FROM assumption_research ar
        LEFT JOIN LATERAL (
            SELECT label, unit, category, value, source
            FROM value_assumptions
            WHERE key = ar.key
              AND ($2::int IS NULL OR account_id = $2 OR account_id IS NULL)
            ORDER BY (account_id IS NULL)
            LIMIT 1
        ) va ON true
        WHERE ar.research_run = $1
          AND ($2::int IS NULL OR ar.account_id = $2)
        ORDER BY va.category NULLS LAST, ar.key
    """, run_id, account_id)
    proposals = rows_to_list(rows)
    for proposal in proposals:
        before = float(proposal.get("value_before") or 0)
        after = float(proposal.get("value_proposed") or 0)
        proposal["pct_change"] = (round(100 * (after - before) / before, 1)
                                  if before else None)
    return {
        "run_id": run_id,
        "assumptions": proposals,
        "summary": rs.summarize_assumptions([
            {"confidence": p["confidence"] or "low",
             "value_before": float(p.get("value_before") or 0),
             "value_proposed": float(p.get("value_proposed") or 0)}
            for p in proposals]),
    }


async def _latest_run_id() -> int | None:
    account_id = await accounts.current()
    if account_id is not None:
        latest = await db.fetchrow(
            "SELECT id FROM research_runs WHERE account_id=$1 AND status <> 'failed' "
            "ORDER BY started_at DESC LIMIT 1", account_id)
    else:
        latest = await db.fetchrow(
            "SELECT id FROM research_runs WHERE status <> 'failed' "
            "ORDER BY started_at DESC LIMIT 1")
    return latest["id"] if latest else None


# ---------------------------------------------------------------------------
# Apply — confirm-gated
# ---------------------------------------------------------------------------
@router.post("/apply", dependencies=[Depends(limiter("write"))])
async def propose_apply(body: ApplyIn, request: Request):
    """Stage the research for confirmation. Writes nothing itself.

    Recalibrating the assumptions changes every dollar figure in the app at once,
    which is exactly the kind of write that should need an explicit human yes.
    """
    actor = current_user(request)
    run_id = body.run_id
    if run_id is None:
        run_id = await _latest_run_id()
    if run_id is None:
        raise HTTPException(404, "No research run to apply. Run research first.")

    account_id = await accounts.current()
    if account_id is not None:
        owned = await db.fetchrow(
            "SELECT id FROM research_runs WHERE id=$1 AND account_id=$2",
            run_id, account_id)
        if owned is None:
            raise HTTPException(404, "Research run not found")
        rows = await db.fetch(
            "SELECT key, value_before, value_proposed, confidence, rationale, basis "
            "FROM assumption_research WHERE research_run = $1 AND account_id=$2",
            run_id, account_id)
    else:
        rows = await db.fetch(
            "SELECT key, value_before, value_proposed, confidence, rationale, basis "
            "FROM assumption_research WHERE research_run = $1", run_id)
    if not rows:
        raise HTTPException(404, f"Research run {run_id} proposed no assumptions.")

    wanted = set(body.keys) if body.keys else None
    chosen = [dict(r) for r in rows
              if (wanted is None or r["key"] in wanted)
              and float(r["value_before"] or 0) != float(r["value_proposed"] or 0)]
    if not chosen:
        raise HTTPException(
            422,
            "None of the selected assumptions would change. "
            + (f"Unknown keys: {sorted(wanted - {r['key'] for r in rows})}"
               if wanted else "The proposals match the current values."))

    profile = await db.fetchrow(
        "SELECT company_name FROM company_profile WHERE account_id = $1",
        await accounts.current())
    company = profile["company_name"] if profile else "this utility"
    by_confidence: dict[str, int] = {}
    for row in chosen:
        level = row["confidence"] or "low"
        by_confidence[level] = by_confidence.get(level, 0) + 1

    token = await cf.issue_token(
        cf.INTENT_APPLY_RESEARCH,
        {"run_id": run_id, "keys": [r["key"] for r in chosen],
         "apply_profile": body.apply_profile, "apply_lobs": body.apply_lobs},
        actor=actor,
        before={"calibrated_assumptions": await _calibrated_count()},
        after={"calibrated_assumptions": await _calibrated_count() + len(chosen)},
        summary=(f"Recalibrate {len(chosen)} value assumption(s) to {company} "
                 f"({by_confidence.get('high', 0)} high / "
                 f"{by_confidence.get('medium', 0)} medium / "
                 f"{by_confidence.get('low', 0)} low confidence). "
                 f"This re-quantifies every use case's value."),
    )
    return {**token, "assumptions": chosen, "run_id": run_id}


async def _calibrated_count() -> int:
    account_id = await accounts.current()
    if account_id is not None:
        row = await db.fetchrow(
            "SELECT count(*) AS n FROM value_assumptions "
            "WHERE account_id=$1 AND source = 'research'",
            account_id)
    else:
        row = await db.fetchrow(
            "SELECT count(*) AS n FROM value_assumptions WHERE source = 'research'")
    return int(row["n"]) if row else 0


async def execute_apply_research(payload: dict, actor: str) -> dict:
    """Confirm executor: write the calibrated values.

    Registered in routes/generate.py's executor table. Each write records that the
    value came from research, so the UI can badge a calibrated number differently
    from a shipped default and a reviewer can always find the rationale.
    """
    run_id = payload.get("run_id")
    keys = payload.get("keys") or []
    account_id = await accounts.current()
    if account_id is not None:
        rows = await db.fetch(
            """SELECT key, value_before, value_proposed, confidence, rationale, basis
               FROM assumption_research
               WHERE research_run = $1 AND account_id=$2 AND key = ANY($3::text[])""",
            run_id, account_id, keys)
    else:
        rows = await db.fetch(
            "SELECT key, value_before, value_proposed, confidence, rationale, basis "
            "FROM assumption_research WHERE research_run = $1 AND key = ANY($2::text[])",
            run_id, keys)

    applied = []
    for row in rows:
        note_parts = [p for p in (row["basis"], row["rationale"]) if p]
        if account_id is not None:
            meta = await db.fetchrow(
                """SELECT label, unit, category
                   FROM value_assumptions
                   WHERE key=$1 AND (account_id=$2 OR account_id IS NULL)
                   ORDER BY (account_id IS NULL) LIMIT 1""",
                row["key"], account_id)
            await db.execute(
                """INSERT INTO value_assumptions
                   (account_id, key, label, value, unit, category, source,
                    source_note, confidence, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,'research',$7,$8,now())
                   ON CONFLICT (account_id, key) DO UPDATE SET
                     value=EXCLUDED.value,
                     source='research',
                     source_note=EXCLUDED.source_note,
                     confidence=EXCLUDED.confidence,
                     updated_at=now()""",
                account_id, row["key"],
                meta["label"] if meta else row["key"],
                row["value_proposed"],
                meta["unit"] if meta else None,
                meta["category"] if meta else None,
                " — ".join(note_parts) or None, row["confidence"])
            await db.execute(
                "UPDATE assumption_research SET applied=true, applied_at=now() "
                "WHERE research_run=$1 AND account_id=$2 AND key=$3",
                run_id, account_id, row["key"])
        else:
            await db.execute(
                "UPDATE value_assumptions SET value=$1, source='research', "
                "source_note=$2, confidence=$3, updated_at=now() WHERE key=$4",
                row["value_proposed"], " — ".join(note_parts) or None,
                row["confidence"], row["key"])
            await db.execute(
                "UPDATE assumption_research SET applied=true, applied_at=now() "
                "WHERE research_run=$1 AND key=$2", run_id, row["key"])
        applied.append({"key": row["key"],
                        "from": float(row["value_before"] or 0),
                        "to": float(row["value_proposed"] or 0),
                        "confidence": row["confidence"]})

    created_lobs = []
    if payload.get("apply_lobs"):
        profile = await db.fetchrow(
            "SELECT segments FROM company_profile WHERE account_id = $1",
            await accounts.current())
        segments = list(profile["segments"] or []) if profile else []
        mapping = {
            "generation": ("Generation", "Owned generation fleet operations."),
            "transmission": ("Transmission", "High-voltage transmission system."),
            "distribution": ("Distribution", "Distribution grid and field operations."),
            "retail": ("Customer", "Retail customer, billing, and contact centre."),
        }
        for segment in segments:
            name, description = mapping.get(segment, (None, None))
            if not name:
                continue
            row = await db.fetchrow(
                "INSERT INTO lobs (name, description) VALUES ($1,$2) "
                "ON CONFLICT (name) DO NOTHING RETURNING id, name",
                name, description)
            if row:
                created_lobs.append(dict(row))

    await write_audit("value_assumptions", None, "apply_research", actor,
                      {"run_id": run_id, "applied": len(applied)})

    # Applying calibrated assumptions re-quantifies every use case at once, which is
    # the single largest move the portfolio value ever makes. Capturing here is what
    # puts the before/after on the trend chart. Quietly: the assumptions ARE applied,
    # and failing to chart that must not report the apply as failed.
    from .. import snapshots as snap
    await snap.capture_quietly(
        snap.REASON_RESEARCH, actor=actor,
        detail=f"applied {len(applied)} calibrated assumption(s) from research run "
               f"{run_id}")

    return {
        "applied_count": len(applied),
        "applied": applied,
        "created_lobs": created_lobs,
        # The whole portfolio was just re-quantified, so say what to look at.
        "next_steps": [
            {"endpoint": "/api/value-assumptions/computed/portfolio",
             "why": "see the portfolio value recomputed against the new assumptions"},
            {"endpoint": "/api/agents/recommend",
             "why": "next-best use cases now that value reflects this company"},
        ],
    }


@router.get("/runs")
async def list_runs(limit: int = 20):
    return rows_to_list(await db.fetch(
        "SELECT * FROM research_runs ORDER BY started_at DESC LIMIT $1", limit))
