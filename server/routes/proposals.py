"""Use-case proposal agent.

    POST /api/proposals/use-cases/{id}          generate (409 if one exists)
    POST /api/proposals/use-cases/{id}?regenerate=true   supersede the existing one
    GET  /api/proposals/use-cases/{id}          the current proposal, if any
    GET  /api/proposals/use-cases/{id}/context  what the agent would be told

WHY THE CONTEXT ENDPOINT EXISTS
-------------------------------
`/context` returns exactly the instance data the prompt will carry, without calling
the model. It is how you answer "why did it say that?" without paying for a
generation, and how you notice that a use case has no value model or no domains
mapped BEFORE spending a large prompt discovering it.

WHY 409 RATHER THAN OVERWRITE
-----------------------------
A proposal is a document someone may have edited by hand after it was generated.
Silently replacing it would destroy that work. `regenerate=true` is explicit, and
even then the previous text is preserved as a version, so nothing is lost.

WHY THIS PATH IS CONFIRM-GATED AND ORDINARY KB EDITS ARE NOT
-----------------------------------------------------------
Here the MODEL is the author. The proposal-generation call produces a document
asserting a business case, a timeline, and a set of risks for a real customer — and
it costs a large prompt. That is exactly the class of write the propose/confirm gate
exists for. A person editing an article they are looking at is not.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import confirm as cf
from .. import knowledge as kb
from .. import proposals as pr
from ..common import current_user, write_audit
from ..db import db
from ..limits import limiter
from ..readiness import BUILT_SQL_LIST, readiness_map
from ..value_engine import compute_value_range, load_assumptions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proposals", tags=["proposals"])

# Where generated proposals are filed, matching the folder 007 seeds.
_PROPOSAL_FOLDER_PATH = "/proposals-business-cases/"
_GENERATOR = "proposal_agent"


async def _gather_context(use_case_id: int) -> tuple[dict, dict]:
    """Everything the prompt needs, from this instance's real state.

    Returns (use_case, context). Raises 404 if the use case is gone.
    """
    row = await db.fetchrow("SELECT * FROM use_cases WHERE id = $1", use_case_id)
    if row is None:
        raise HTTPException(404, "Use case not found")
    use_case = dict(row)

    assumptions = await load_assumptions()
    value_range = compute_value_range(use_case.get("hypothesized_value_json"),
                                     assumptions)

    # Which assumptions this use case's own formula actually depends on, with their
    # calibration confidence. Sending all 34 would bury the relevant three and
    # invite the model to reason about numbers this use case does not use.
    formula = use_case.get("hypothesized_value_json")
    if isinstance(formula, str):
        import json
        try:
            formula = json.loads(formula)
        except (ValueError, TypeError):
            formula = None
    used_keys: list[str] = []
    components: list[str] = []
    if isinstance(formula, dict):
        for component in formula.get("components") or []:
            keys = component.get("assumptionKeys") or []
            used_keys.extend(keys)
            display = component.get("calculationDisplay") or component.get("name")
            if display:
                components.append(str(display))

    assumption_lines: list[str] = []
    if used_keys:
        found = await db.fetch(
            "SELECT key, label, value, unit FROM value_assumptions "
            "WHERE key = ANY($1::text[])", list(dict.fromkeys(used_keys)))
        # Research confidence, when the instance has been calibrated.
        confidence: dict[str, str] = {}
        try:
            rows = await db.fetch(
                "SELECT key, confidence FROM assumption_research")
            confidence = {r["key"]: r["confidence"] for r in rows}
        except Exception:  # noqa: BLE001 - table absent on an un-researched install
            pass
        for item in found:
            note = f" [{confidence[item['key']]} confidence]" \
                if item["key"] in confidence else " [uncalibrated]"
            assumption_lines.append(
                f"{item['label'] or item['key']} = {item['value']} "
                f"{item['unit'] or ''}".strip() + note)

    # Domain position: satisfied vs gap, by name.
    domain_rows = await db.fetch("""
        SELECT dd.label,
               COALESCE(bool_or(da.ingestion_status IN ('curated','governed')),
                        false) AS satisfied
        FROM uc_requires_domain urd
        JOIN data_domains dd ON dd.id = urd.domain_id
        LEFT JOIN asset_serves_domain asd ON asd.domain_id = urd.domain_id
        LEFT JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE urd.use_case_id = $1 AND urd.necessity = 'required'
        GROUP BY dd.label ORDER BY dd.label
    """, use_case_id)
    satisfied = [r["label"] for r in domain_rows if r["satisfied"]]
    gaps = [r["label"] for r in domain_rows if not r["satisfied"]]

    # Source systems that would serve it, so the design section can name them.
    source_rows = await db.fetch("""
        SELECT DISTINCT COALESCE(da.source_category, da.source_system) AS source
        FROM uc_requires_domain urd
        JOIN asset_serves_domain asd ON asd.domain_id = urd.domain_id
        JOIN data_assets da ON da.id = asd.data_asset_id
        WHERE urd.use_case_id = $1
        UNION
        SELECT DISTINCT COALESCE(da.source_category, da.source_system)
        FROM uc_requires_asset ura JOIN data_assets da ON da.id = ura.data_asset_id
        WHERE ura.use_case_id = $1
    """, use_case_id)
    sources = sorted({r["source"] for r in source_rows if r["source"]})[:20]

    prereq_rows = await db.fetch(f"""
        SELECT up.title, (up.status IN ({BUILT_SQL_LIST})) AS built
        FROM uc_enables_uc e JOIN use_cases up ON up.id = e.from_use_case_id
        WHERE e.to_use_case_id = $1 ORDER BY up.title
    """, use_case_id)

    company = await db.fetchrow("SELECT * FROM company_profile WHERE id = 1")
    lob = None
    if use_case.get("lob_id"):
        lob_row = await db.fetchrow("SELECT name FROM lobs WHERE id = $1",
                                    use_case["lob_id"])
        lob = lob_row["name"] if lob_row else None

    readiness = None
    try:
        readiness = (await readiness_map()).get(use_case_id, {}).get("readiness")
    except Exception:  # noqa: BLE001 - a label is a nicety, not a blocker
        pass

    return use_case, {
        "company": dict(company) if company else {},
        "value": {**(value_range or {}),
                  "driver": (formula or {}).get("driver") if isinstance(formula, dict) else None,
                  "components": components},
        "assumptions": assumption_lines,
        "domains": {"satisfied": satisfied, "gaps": gaps},
        "sources": sources,
        "prerequisites": [{"title": r["title"], "built": r["built"]}
                          for r in prereq_rows],
        "readiness": readiness,
        "lob": lob,
    }


@router.get("/use-cases/{use_case_id}/context")
async def proposal_context(use_case_id: int):
    """What the agent would be told. No model call, no cost."""
    use_case, context = await _gather_context(use_case_id)
    return {
        "use_case": {"id": use_case["id"], "title": use_case["title"],
                     "status": use_case["status"],
                     "effort_tshirt": use_case.get("effort_tshirt")},
        "context": context,
        # Named up front so the UI can warn before someone spends a generation on a
        # use case whose proposal would be mostly caveats.
        "warnings": _context_warnings(context),
    }


def _context_warnings(context: dict) -> list[str]:
    warnings = []
    if (context.get("value") or {}).get("mid") is None:
        warnings.append("This use case has no value model, so the proposal cannot "
                        "state a figure. Quantify it first for a stronger document.")
    if not context.get("company"):
        warnings.append("No company research has been run, so the proposal will be "
                        "generic. Run Company research first.")
    if not context.get("sources"):
        warnings.append("No source systems are mapped, so the design section will "
                        "be non-specific.")
    uncalibrated = [a for a in context.get("assumptions") or []
                    if "uncalibrated" in a]
    if uncalibrated:
        warnings.append(f"{len(uncalibrated)} of the assumptions behind the value "
                        "figure are uncalibrated defaults.")
    return warnings


@router.get("/use-cases/{use_case_id}")
async def get_proposal(use_case_id: int):
    """The current proposal for a use case, if one exists."""
    row = await db.fetchrow("""
        SELECT a.id, a.title, a.slug, a.summary, a.status, a.version,
               a.generated_by, a.created_by, a.updated_at, l.relation
        FROM kb_links l JOIN kb_articles a ON a.id = l.article_id
        WHERE l.entity_type = 'use_case' AND l.entity_id = $1
          AND l.relation = 'proposal' AND a.status <> 'archived'
        ORDER BY a.updated_at DESC LIMIT 1
    """, use_case_id)
    if row is None:
        return {"exists": False, "use_case_id": use_case_id}
    return {"exists": True, "use_case_id": use_case_id, "proposal": dict(row)}


@router.post("/use-cases/{use_case_id}",
             dependencies=[Depends(limiter("research"))])
async def propose_proposal(use_case_id: int, request: Request,
                           regenerate: bool = Query(default=False)):
    """Generate a proposal and stage it for confirmation.

    Rate-limited as 'research' rather than 'generate': eight prose sections at 8000
    output tokens is the same order of cost as company research, not the same as a
    short classification call.
    """
    actor = current_user(request)
    use_case, context = await _gather_context(use_case_id)

    existing = await db.fetchrow("""
        SELECT a.id, a.slug, a.version FROM kb_links l
        JOIN kb_articles a ON a.id = l.article_id
        WHERE l.entity_type = 'use_case' AND l.entity_id = $1
          AND l.relation = 'proposal' AND a.status <> 'archived'
        ORDER BY a.updated_at DESC LIMIT 1
    """, use_case_id)
    if existing is not None and not regenerate:
        raise HTTPException(
            409,
            f"A proposal already exists for this use case ({existing['slug']}, "
            f"v{existing['version']}). It may have been edited by hand since it was "
            "generated. Pass regenerate=true to supersede it — the current text is "
            "kept as a version either way.")

    from .agents import _llm_json

    prompt = pr.build_prompt(use_case, context)
    parsed, used_llm, note = await _llm_json(
        prompt, max_tokens=pr.MAX_OUTPUT_TOKENS,
        response_schema=pr.RESPONSE_SCHEMA)

    if not used_llm or not parsed:
        # No heuristic fallback here, deliberately. Every other agent in this app
        # degrades to a deterministic ranking, which is honest because a ranking is
        # a judgement. A template-filled "proposal" would be a document that looks
        # authored and says nothing — worse than telling the user it failed.
        raise HTTPException(
            502, f"The proposal could not be generated. {note or ''} "
                 "Nothing was saved.".strip())

    # Doubly-nested JSON has been observed from this endpoint family.
    if isinstance(parsed, dict) and len(parsed) == 1:
        only = next(iter(parsed.values()))
        if isinstance(only, str) and only.strip().startswith("{"):
            import json
            try:
                parsed = json.loads(only)
            except (ValueError, TypeError):
                pass

    try:
        sections = pr.validate_sections(parsed)
    except pr.ProposalRejected as exc:
        raise HTTPException(422, str(exc)) from exc

    body_md = pr.assemble_markdown(use_case, sections, context)
    summary = pr.summarize(use_case, context)
    title = f"Proposal — {use_case['title']}"

    payload = {
        "use_case_id": use_case_id,
        "title": title,
        "body_md": body_md,
        "summary": summary,
        "existing_article_id": existing["id"] if existing else None,
    }
    card = await cf.issue_token(
        cf.INTENT_CREATE_PROPOSAL, payload, actor=actor,
        before={"exists": existing is not None,
                "slug": existing["slug"] if existing else None,
                "version": existing["version"] if existing else None},
        after={"title": title, "sections": len(sections),
               "characters": len(body_md), "summary": summary},
        summary=(f"{'Replace' if existing else 'Create'} the proposal for "
                 f"{use_case['title']} ({len(sections)} sections, "
                 f"{len(body_md):,} characters)"))

    return {
        "use_case_id": use_case_id,
        "confirm": card,
        "preview_md": body_md,
        "sections": list(sections),
        "warnings": _context_warnings(context),
        "regenerating": existing is not None,
    }


async def execute_create_proposal(payload: dict, actor: str) -> dict:
    """Executor for INTENT_CREATE_PROPOSAL. Called only after confirmation."""
    use_case_id = payload["use_case_id"]
    existing_id = payload.get("existing_article_id")

    folder = await db.fetchrow(
        "SELECT id FROM kb_folders WHERE path = $1", _PROPOSAL_FOLDER_PATH)
    folder_id = folder["id"] if folder else None

    if existing_id:
        # Supersede: snapshot the current text as a version, then overwrite. This is
        # why regenerating cannot lose a hand edit.
        current = await db.fetchrow(
            "SELECT title, body_md, summary, version FROM kb_articles WHERE id = $1",
            existing_id)
        if current is not None:
            await db.execute("""
                INSERT INTO kb_article_versions (article_id, version, title,
                    body_md, summary, change_note, edited_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (article_id, version) DO NOTHING
            """, existing_id, current["version"], current["title"],
                current["body_md"], current["summary"],
                "Superseded by a regenerated proposal", actor)
        row = await db.fetchrow("""
            UPDATE kb_articles SET title = $2, body_md = $3, summary = $4,
                   generated_by = $5, version = version + 1,
                   updated_by = $6, updated_at = now()
            WHERE id = $1 RETURNING id, slug, version
        """, existing_id, payload["title"], payload["body_md"],
            payload["summary"], _GENERATOR, actor)
        await write_audit("kb_article", existing_id, "regenerate_proposal", actor,
                          {"use_case_id": use_case_id})
        return {"article_id": row["id"], "slug": row["slug"],
                "version": row["version"], "replaced": True}

    taken = {r["slug"] for r in await db.fetch("SELECT slug FROM kb_articles")}
    slug = kb.slugify(payload["title"], existing=taken)
    row = await db.fetchrow("""
        INSERT INTO kb_articles (title, slug, folder_id, body_md, summary, tags,
            status, generated_by, created_by, updated_by)
        VALUES ($1,$2,$3,$4,$5,$6,'draft',$7,$8,$8)
        RETURNING id, slug, version
    """, payload["title"], slug, folder_id, payload["body_md"], payload["summary"],
        ["proposal", "generated"], _GENERATOR, actor)

    # Attach it to the use case, which is what makes it findable from the portfolio.
    await db.execute("""
        INSERT INTO kb_links (article_id, entity_type, entity_id, relation, created_by)
        VALUES ($1,'use_case',$2,'proposal',$3)
        ON CONFLICT (article_id, entity_type, entity_id, relation) DO NOTHING
    """, row["id"], use_case_id, actor)

    await write_audit("kb_article", row["id"], "create_proposal", actor,
                      {"use_case_id": use_case_id, "slug": row["slug"]})
    return {"article_id": row["id"], "slug": row["slug"],
            "version": row["version"], "replaced": False}
