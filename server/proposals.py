"""Use-case proposal generation — prompt construction and output validation.

WHAT THIS PRODUCES
------------------
An eight-section written proposal for one use case, stored as a knowledge-base
article: Executive Summary, Business Case & Value, Deliverables, High-Level Design,
Assumptions & Dependencies, Risks & Mitigations, Timeline, Open Items & Next Steps.

WHY IT IS GROUNDED, NOT FREE-FORM
---------------------------------
A model asked to "write a proposal for outage prediction" writes a plausible
proposal for a generic utility. It invents a customer size, a value figure, and a
data landscape. That output is worse than nothing in a real engagement: it reads as
authoritative and every number in it is wrong, so the first person who checks one
loses trust in the whole document.

So the prompt carries the instance's actual state — the researched company profile,
this use case's real computed value from the value engine, which of its data domains
are actually satisfied and which are gaps, what its prerequisites are and whether
they are built, its effort estimate and readiness. And the prompt says explicitly
that these figures are authoritative and must not be replaced with invented ones.

WHY THE OUTPUT IS VALIDATED, NOT TRUSTED
----------------------------------------
The model is asked for a JSON object with one key per section, and every section is
checked for presence and minimum substance. A proposal missing its business case, or
with a section containing "TODO", is rejected rather than stored — a half-written
document in the knowledge base is indistinguishable from a finished one once the
generation context is gone.

`validate_sections` also strips the model's own headings. It reliably re-emits
"## Executive Summary" inside the section text even when told not to, which would
render as a duplicate heading under the one the assembler writes.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Section definitions
# ---------------------------------------------------------------------------
# (key, heading, what it must contain, minimum characters)
#
# The minimums are deliberately modest. They exist to catch a section the model
# skipped or stubbed, not to enforce a word count — a genuinely short timeline for a
# two-week use case is fine, and rejecting it would push the model toward padding.
SECTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("executive_summary", "Executive Summary",
     "Three to five sentences a utility executive can read standing up: what this "
     "does, what it is worth, and what it needs. No preamble.", 200),
    ("business_case", "Business Case & Value",
     "The value drivers and the arithmetic behind the figure given to you. State "
     "the assumptions the number rests on and their confidence. Do NOT invent a "
     "different figure.", 250),
    ("deliverables", "Deliverables",
     "A bulleted list of concrete artifacts — tables, pipelines, models, "
     "dashboards, runbooks — that a delivery team would hand over.", 200),
    ("design", "High-Level Design",
     "Sources -> ingestion -> transformation -> serving, in Databricks terms "
     "(Unity Catalog, Lakeflow, DLT, Model Serving, AI/BI). Name the actual source "
     "systems listed for this use case.", 250),
    ("assumptions", "Assumptions & Dependencies",
     "What must be true for this to work, including the data domains that are "
     "still gaps and any prerequisite use cases that are not yet built.", 200),
    ("risks", "Risks & Mitigations",
     "The three to five risks that would actually derail this, each with a "
     "mitigation. Include regulatory and data-quality risks where they apply.", 200),
    ("timeline", "Timeline",
     "Phases with durations consistent with the effort estimate given to you. "
     "Do not promise a date.", 150),
    ("next_steps", "Open Items & Next Steps",
     "The decisions and approvals still outstanding, and who has to make them.",
     150),
)

SECTION_KEYS = tuple(key for key, _, _, _ in SECTIONS)

# Reasoning models spend output tokens before answering, and eight prose sections is
# a large answer. Sized from observation: 4000 truncated the last two sections.
MAX_OUTPUT_TOKENS = 8000

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "use_case_proposal",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                key: {"type": "string"} for key in SECTION_KEYS
            },
            "required": list(SECTION_KEYS),
            "additionalProperties": False,
        },
    },
}


class ProposalRejected(ValueError):
    """The model's output was not a usable proposal. The message names why."""


# Phrases that mean the model stubbed a section instead of writing it. Checked
# because a section containing "TODO" is worse than a missing one: it looks written.
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bTBD\b", re.I),
    re.compile(r"\bTODO\b", re.I),
    re.compile(r"\[insert[^\]]*\]", re.I),
    re.compile(r"\[[a-z\s]*placeholder[^\]]*\]", re.I),
    re.compile(r"<[a-z_]+>"),                     # <company_name>
    re.compile(r"\bLorem ipsum\b", re.I),
    re.compile(r"\bas an AI\b", re.I),            # the model talking about itself
    re.compile(r"^\s*(N/?A|None|Not applicable)\s*$", re.I),
)


def _strip_own_heading(text: str, heading: str) -> str:
    """Remove a heading the model re-emitted inside its own section text.

    It does this even when told not to, and the result renders as a duplicate
    heading under the one the assembler writes.
    """
    lines = (text or "").strip().split("\n")
    while lines:
        first = lines[0].strip()
        normalized = re.sub(r"^#{1,6}\s*", "", first).strip().rstrip(":").lower()
        if normalized == heading.lower() or (
                first.startswith("#") and heading.lower() in normalized):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def validate_sections(parsed: dict) -> dict[str, str]:
    """Check and clean the model's section map, or raise ProposalRejected."""
    if not isinstance(parsed, dict):
        raise ProposalRejected("The model did not return a JSON object.")

    cleaned: dict[str, str] = {}
    missing: list[str] = []
    thin: list[str] = []
    stubbed: list[str] = []

    for key, heading, _guidance, minimum in SECTIONS:
        raw = parsed.get(key)
        if raw is None or not isinstance(raw, str) or not raw.strip():
            missing.append(heading)
            continue
        text = _strip_own_heading(raw, heading)
        if len(text) < minimum:
            thin.append(f"{heading} ({len(text)} of {minimum} chars)")
            continue
        if any(pattern.search(text) for pattern in _PLACEHOLDER_PATTERNS):
            stubbed.append(heading)
            continue
        cleaned[key] = text

    problems = []
    if missing:
        problems.append(f"missing: {', '.join(missing)}")
    if thin:
        problems.append(f"too short: {', '.join(thin)}")
    if stubbed:
        problems.append(f"left placeholders in: {', '.join(stubbed)}")
    if problems:
        raise ProposalRejected(
            "The generated proposal was incomplete (" + "; ".join(problems)
            + "). Nothing was saved — try again, or narrow the use case.")
    return cleaned


def assemble_markdown(use_case: dict, sections: dict[str, str],
                      context: dict) -> str:
    """Build the article body from validated sections.

    The provenance block at the top is not decoration. A generated proposal that
    reads as a hand-written standard is a liability — someone will quote its numbers
    in a rate filing. It states what produced it, when, and which figures came from
    the instance rather than the model.
    """
    company = (context.get("company") or {}).get("company_name") or "the utility"
    value = context.get("value") or {}
    lines: list[str] = []

    lines.append(f"> **Generated proposal** for {company}. Drafted by the AI Value Flywheel "
                 "proposal agent from this instance's portfolio data.")
    lines.append("> Value figures come from the value engine and the calibrated "
                 "assumptions, not from the model. Review before sharing.")
    if value.get("mid") is not None:
        band = ""
        if value.get("low") is not None and value.get("high") is not None:
            band = f" (range ${value['low']:.1f}M–${value['high']:.1f}M)"
        lines.append(">")
        lines.append(f"> **Annual value:** ${value['mid']:.1f}M{band} · "
                     f"**Effort:** {use_case.get('effort_tshirt') or 'M'} · "
                     f"**Readiness:** {context.get('readiness') or 'unknown'}")
    lines.append("")

    for key, heading, _guidance, _minimum in SECTIONS:
        if key not in sections:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(sections[key])
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def build_prompt(use_case: dict, context: dict) -> str:
    """Construct the generation prompt from real instance state."""
    company = context.get("company") or {}
    value = context.get("value") or {}
    domains = context.get("domains") or {}
    satisfied = domains.get("satisfied") or []
    gaps = domains.get("gaps") or []
    sources = context.get("sources") or []
    prereqs = context.get("prerequisites") or []
    assumptions = context.get("assumptions") or []

    parts: list[str] = [
        "You are a Databricks solutions architect writing a use-case proposal for a "
        "Power & Utilities customer. Write for a utility audience: a director of "
        "grid modernization and a VP of IT will both read it.",
        "",
        "RULES",
        "- Every figure below is AUTHORITATIVE. It comes from the customer's own "
        "portfolio data and calibrated assumptions. Use these numbers. Do NOT "
        "invent different ones, and do not add figures of your own.",
        "- Name the actual source systems and data domains listed. Do not "
        "substitute generic examples.",
        "- If something is unknown, say so plainly in Open Items. Never write TBD, "
        "TODO, or a bracketed placeholder.",
        "- Do not repeat the section heading inside the section text.",
        "- Markdown within a section is fine (bullets, bold). No top-level heading.",
        "",
        "=== THE CUSTOMER ===",
    ]
    if company.get("company_name"):
        parts.append(f"Company: {company['company_name']}")
    for field, label in (("utility_type", "Utility type"),
                         ("service_territory", "Service territory"),
                         ("customer_count", "Customers served"),
                         ("regulatory_environment", "Regulator"),
                         ("capital_plan", "Capital plan"),
                         ("strategic_priorities", "Stated priorities")):
        if company.get(field):
            parts.append(f"{label}: {company[field]}")
    if not company:
        parts.append("No company research has been run for this instance. Write for "
                     "a generic investor-owned electric utility and say so in Open "
                     "Items, so the reader knows to supply specifics.")

    parts += ["", "=== THE USE CASE ===",
              f"Title: {use_case.get('title')}",
              f"Description: {use_case.get('description') or '(none recorded)'}",
              f"Line of business: {context.get('lob') or 'unassigned'}",
              f"Sub-vertical: {use_case.get('sub_vertical') or 'cross'}",
              f"Effort estimate (t-shirt): {use_case.get('effort_tshirt') or 'M'}",
              f"Current status: {use_case.get('status')}",
              f"Readiness: {context.get('readiness') or 'unknown'}"]
    if use_case.get("risk_tags"):
        parts.append(f"Risk themes: {', '.join(use_case['risk_tags'])}")

    parts += ["", "=== VALUE (from the value engine — authoritative) ==="]
    if value.get("mid") is not None:
        parts.append(f"Annual value, midpoint: ${value['mid']:.2f}M")
        if value.get("low") is not None:
            parts.append(f"Low / high band: ${value['low']:.2f}M / "
                         f"${value['high']:.2f}M")
        if value.get("driver"):
            parts.append(f"Driver: {value['driver']}")
        for component in value.get("components") or []:
            parts.append(f"  - {component}")
    else:
        parts.append("This use case has no value model yet. Do not invent a figure; "
                     "describe the value qualitatively and list quantifying it as an "
                     "open item.")

    if assumptions:
        parts += ["", "Assumptions the figure rests on (value, unit, confidence):"]
        parts += [f"  - {a}" for a in assumptions]

    parts += ["", "=== DATA POSITION (authoritative) ==="]
    parts.append(f"Data domains satisfied ({len(satisfied)}): "
                 + (", ".join(satisfied) if satisfied else "none"))
    parts.append(f"Data domains still GAPS ({len(gaps)}): "
                 + (", ".join(gaps) if gaps else "none"))
    if sources:
        parts.append("Source systems that would serve this use case: "
                     + ", ".join(sources))
    if prereqs:
        parts.append("Prerequisite use cases: "
                     + "; ".join(f"{p['title']} ({'built' if p['built'] else 'NOT built'})"
                                 for p in prereqs))
    else:
        parts.append("Prerequisite use cases: none.")

    parts += ["", "=== WHAT TO WRITE ===",
              "Return STRICT JSON with exactly these keys, each a markdown string:"]
    for key, heading, guidance, minimum in SECTIONS:
        parts.append(f'  "{key}"  — {heading}. {guidance} (at least ~{minimum} chars)')

    return "\n".join(parts)


def summarize(use_case: dict, context: dict) -> str:
    """One-line summary stored on the article, for search results and lists."""
    value = (context.get("value") or {}).get("mid")
    money = f"${value:.1f}M/yr" if value is not None else "value not yet quantified"
    gaps = len((context.get("domains") or {}).get("gaps") or [])
    gap_text = "no data gaps" if gaps == 0 else f"{gaps} data gap(s)"
    return (f"Generated proposal for {use_case.get('title')} — {money}, "
            f"{gap_text}, effort {use_case.get('effort_tshirt') or 'M'}.")
