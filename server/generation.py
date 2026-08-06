"""Use-case generation: prompt construction and candidate validation.

WHAT THIS ADDS OVER THE EXISTING AGENTS
---------------------------------------
`routes/agents.py` reasons about use cases that already exist — detecting
dependencies, estimating value, sequencing a roadmap. It cannot author new ones.
This module does, and then hands the results straight to those agents so a
generated use case immediately gets a real parameterized value model instead of a
flat dollar guess.

THE LENS
--------
`lens` is the feature that makes this useful in a customer conversation:

  ready : use cases buildable with data the utility ALREADY has (required domains
          are satisfied). The "you could start Monday" list.
  gap   : use cases that justify NEW ingestion (they need unsatisfied domains).
          The "here is what landing AMI would unlock" list — which is exactly the
          joint-funding argument.
  both  : a mix, asked for in one call.

The lens is enforced twice: stated in the prompt, then CHECKED against the actual
domain-satisfaction state when candidates come back. A model claiming a use case
is "ready" when it requires an unlanded domain gets corrected, because that claim
is the whole basis of the recommendation.

VALIDATION POSTURE
------------------
Every field is validated against a closed vocabulary and every domain reference
against the live vocabulary. A hallucinated domain is dropped rather than created:
inventing vocabulary entries from model output would corrupt the layer that
readiness depends on. Candidates surviving with no valid required domain are
flagged, not silently accepted.
"""
from __future__ import annotations

import hashlib
import json
import re

# Closed vocabularies. These mirror the CHECK constraints in 001_init.sql; a
# candidate whose value falls outside them is coerced, not rejected, because a
# good use case with a bad enum is still worth showing.
EFFORT_VALUES = ("S", "M", "L", "XL")
SUB_VERTICALS = ("fossil", "hydro", "renewables", "nuclear", "cross")
TIME_HORIZONS = ("quick_win", "strategic")
VALUE_TYPES = ("cost", "revenue", "risk", "mixed")
LENSES = ("ready", "gap", "both")

DEFAULT_EFFORT = "M"
DEFAULT_SUB_VERTICAL = "cross"

# Bounds on a batch. Below ~1 there is nothing to review; above ~15 the model's
# quality degrades and the response risks truncation.
MIN_COUNT = 1
MAX_COUNT = 15

# Strict JSON schema for the response. Paired with ai_query's responseFormat (or
# the chat API's equivalent) this removes the "parse prose for a JSON object"
# failure mode that brace-scraping has.
RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "use_case_generation",
        "schema": {
            "type": "object",
            "properties": {
                "use_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "business_value": {"type": "string"},
                            "value_rationale": {"type": "string"},
                            "effort_tshirt": {"type": "string",
                                              "enum": list(EFFORT_VALUES)},
                            "sub_vertical": {"type": "string",
                                             "enum": list(SUB_VERTICALS)},
                            "lens": {"type": "string", "enum": ["ready", "gap"]},
                            "time_horizon": {"type": "string",
                                             "enum": list(TIME_HORIZONS)},
                            "value_type": {"type": "string",
                                           "enum": list(VALUE_TYPES)},
                            "is_regulatory": {"type": "boolean"},
                            "required_domains": {
                                "type": "array", "items": {"type": "string"}},
                            "helpful_domains": {
                                "type": "array", "items": {"type": "string"}},
                        },
                        "required": ["title", "description", "business_value",
                                     "effort_tshirt", "lens", "required_domains"],
                    },
                },
            },
            "required": ["use_cases"],
        },
        "strict": True,
    },
}


def unwrap_list(payload: dict | None, key: str, max_depth: int = 8) -> list | None:
    """Pull a list out of `payload[key]`, undoing any stringified nesting.

    Observed against `databricks-claude-sonnet-5` with a strict `response_format`:
    the reply came back DOUBLY nested —

        {"use_cases": "{\\"use_cases\\": [ {...}, {...} ]}"}

    — i.e. the array was JSON-encoded as a string, and that string was itself a
    full copy of the response envelope. A single `json.loads` retry recovers the
    inner object but then finds a dict where a list belongs, so the candidates
    were being discarded even though the model had produced perfectly good ones.

    This unwraps repeatedly: parse a string, and if what comes back is an envelope
    carrying the same key, descend into it. Each level of nesting costs TWO
    iterations (parse the string, then step into the dict), so `max_depth` is set
    well above the observed depth of 2 — iterations are trivially cheap and the
    only real requirement is that a pathological reply terminates.
    Returns None when no list can be recovered.
    """
    if not payload:
        return None
    value = payload.get(key)
    for _ in range(max_depth):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return None
            continue
        if isinstance(value, dict):
            # An envelope: prefer the same key, else a lone list-valued key.
            if key in value:
                value = value[key]
                continue
            lists = [v for v in value.values() if isinstance(v, list)]
            if len(lists) == 1:
                return lists[0]
            return None
        return None
    return None


def candidate_id(title: str, lob_name: str) -> str:
    """Stable id for a (title, LOB) pair.

    Deterministic so re-generating the same idea yields the same id, which lets the
    UI recognise a duplicate across previews instead of showing it twice.
    """
    digest = hashlib.sha1(
        f"{(title or '').strip().lower()}|{(lob_name or '').strip().lower()}".encode()
    ).hexdigest()[:12]
    return f"cand_{digest}"


def normalize_title(title: str) -> str:
    """Collapse whitespace and strip enumeration the model sometimes prefixes."""
    text = re.sub(r"\s+", " ", (title or "").strip())
    return re.sub(r"^\d+[\.\)]\s*", "", text)


def _coerce(value, allowed: tuple[str, ...], default: str | None) -> str | None:
    if value is None:
        return default
    text = str(value).strip().lower()
    for candidate in allowed:
        if text == candidate.lower():
            return candidate
    return default


def build_prompt(
    *,
    company_name: str,
    lob_name: str,
    lens: str,
    count: int,
    satisfied_domains: list[dict],
    unsatisfied_domains: list[dict],
    existing_titles: list[str],
    time_horizon_bias: str | None = None,
    value_type_bias: str | None = None,
    prioritize_regulatory: bool = False,
) -> str:
    """Build the generation prompt.

    The two domain lists are the substance: they tell the model what the utility
    actually has and what it lacks, which is what makes a "ready" use case
    genuinely ready rather than aspirational. `existing_titles` is a collision
    guard — without it the model happily re-proposes what is already in the
    portfolio.
    """
    def render(domains: list[dict]) -> str:
        if not domains:
            return "  (none)"
        return "\n".join(
            f"  - {d['name']}: {d.get('label') or d['name']}"
            + (f" — {d['description']}" if d.get("description") else "")
            for d in domains)

    if lens == "ready":
        lens_rule = (
            "Generate ONLY 'ready' use cases: every entry in required_domains MUST "
            "come from AVAILABLE DATA DOMAINS. These must be buildable today with "
            "no new ingestion. Set lens to \"ready\"."
        )
    elif lens == "gap":
        lens_rule = (
            "Generate ONLY 'gap' use cases: required_domains MUST include at least "
            "one domain from MISSING DATA DOMAINS, to make the business case for "
            "landing that data. Set lens to \"gap\"."
        )
    else:
        lens_rule = (
            "Generate a mix. For each use case set lens to \"ready\" if every "
            "required domain is in AVAILABLE, or \"gap\" if it needs at least one "
            "from MISSING. Aim for roughly half of each."
        )

    biases = []
    if time_horizon_bias in TIME_HORIZONS:
        biases.append(
            "Favor quick wins deliverable in a quarter." if time_horizon_bias == "quick_win"
            else "Favor strategic, higher-ceiling initiatives.")
    if value_type_bias in VALUE_TYPES:
        biases.append(f"Favor use cases whose value is primarily {value_type_bias}.")
    if prioritize_regulatory:
        biases.append("Prioritize regulatory and compliance-driven use cases.")
    bias_block = ("\nEMPHASIS:\n" + "\n".join(f"- {b}" for b in biases)) if biases else ""

    collision_block = ""
    if existing_titles:
        listed = "\n".join(f"  - {t}" for t in existing_titles[:60])
        collision_block = (
            "\nALREADY IN THE PORTFOLIO — do NOT propose these or close "
            f"paraphrases of them:\n{listed}\n")

    return f"""You are a Power & Utilities data and AI strategy advisor working with {company_name}.

Propose {count} high-value data/AI use case(s) for the {lob_name} line of business.

{lens_rule}
{bias_block}
AVAILABLE DATA DOMAINS (data the utility has already landed and governed):
{render(satisfied_domains)}

MISSING DATA DOMAINS (not yet landed — needed for 'gap' use cases):
{render(unsatisfied_domains)}
{collision_block}
For EACH use case return:
- title: short and specific (e.g. "Predictive Transformer Failure"), not generic
- description: 2-3 sentences on what it does and how it works
- business_value: 1-2 sentences of customer-facing framing
- value_rationale: 1-2 sentences on WHERE the value comes from (which cost line
  falls, which revenue rises, which risk shrinks). Do NOT invent a dollar figure —
  the app computes value from the customer's own assumptions.
- effort_tshirt: one of S, M, L, XL
- sub_vertical: one of {', '.join(SUB_VERTICALS)}
- lens: "ready" or "gap", obeying the rule above
- time_horizon: "quick_win" or "strategic"
- value_type: one of {', '.join(VALUE_TYPES)}
- is_regulatory: true or false
- required_domains: array of domain `name` keys (snake_case, exactly as listed
  above) that this use case CANNOT work without. Never empty. Use only names from
  the two lists.
- helpful_domains: array of domain `name` keys that improve it but are not
  required. May be empty.

Be concrete and realistic for a regulated utility. Prefer use cases with a clear
operational owner over generic "build a data platform" work.

Return STRICT JSON: {{"use_cases": [...]}}"""


def validate_candidates(
    parsed: dict | None,
    *,
    lob_name: str,
    requested_lens: str,
    domain_index: dict[str, dict],
    satisfied_names: set[str],
    existing_titles: list[str],
    limit: int,
) -> tuple[list[dict], list[str]]:
    """Validate and normalize raw model output. Returns (candidates, warnings).

    Enforces, in order:
      - a usable title, de-duplicated within the batch and against the portfolio
      - closed vocabularies for every enum
      - domain references that exist (hallucinated names are dropped)
      - at least one valid required domain, else the candidate is unusable
      - the lens CLAIM against real domain satisfaction, correcting the model

    `warnings` is for the UI: it explains what was adjusted, so a reviewer can see
    the model was wrong about something rather than silently trusting the output.
    """
    warnings: list[str] = []
    if not parsed:
        return [], ["The model returned no parseable output."]

    # The array can arrive stringified, or even doubly nested, despite a strict
    # response schema — see unwrap_list().
    items = unwrap_list(parsed, "use_cases")
    if not items:
        return [], ["The model returned no use cases."]

    seen_titles = {normalize_title(t).lower() for t in existing_titles}
    batch_titles: set[str] = set()
    out: list[dict] = []
    dropped_domains: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        title = normalize_title(str(item.get("title") or ""))
        if not title:
            continue
        key = title.lower()
        if key in batch_titles:
            continue  # duplicate within this batch
        if key in seen_titles:
            warnings.append(f"Skipped {title!r} — already in the portfolio.")
            continue
        batch_titles.add(key)

        def domain_list(field: str) -> list[str]:
            raw = item.get(field) or []
            if not isinstance(raw, list):
                return []
            names = []
            for entry in raw:
                name = str(entry or "").strip()
                if not name:
                    continue
                if name in domain_index:
                    names.append(name)
                else:
                    dropped_domains.add(name)
            return list(dict.fromkeys(names))

        required = domain_list("required_domains")
        helpful = [d for d in domain_list("helpful_domains") if d not in required]

        if not required:
            # Without a required domain the use case cannot be scored on the
            # domain path, which defeats the point of generating it.
            warnings.append(
                f"Skipped {title!r} — no recognized required data domain.")
            continue

        # Recompute the lens from reality rather than trusting the claim.
        actual_lens = "ready" if all(d in satisfied_names for d in required) else "gap"
        claimed_lens = _coerce(item.get("lens"), ("ready", "gap"), actual_lens)
        if claimed_lens != actual_lens:
            warnings.append(
                f"{title!r}: model said {claimed_lens!r} but its required data is "
                f"{'all available' if actual_lens == 'ready' else 'not fully landed'} "
                f"— recorded as {actual_lens!r}.")
        if requested_lens in ("ready", "gap") and actual_lens != requested_lens:
            warnings.append(
                f"Skipped {title!r} — you asked for {requested_lens!r} use cases and "
                f"this one is {actual_lens!r}.")
            continue

        missing = [d for d in required if d not in satisfied_names]
        out.append({
            "candidate_id": candidate_id(title, lob_name),
            "title": title,
            "description": str(item.get("description") or "").strip(),
            "business_value": str(item.get("business_value") or "").strip(),
            "value_rationale": str(item.get("value_rationale") or "").strip(),
            "effort_tshirt": _coerce(item.get("effort_tshirt"), EFFORT_VALUES, DEFAULT_EFFORT),
            "sub_vertical": _coerce(item.get("sub_vertical"), SUB_VERTICALS,
                                    DEFAULT_SUB_VERTICAL),
            "lens": actual_lens,
            "time_horizon": _coerce(item.get("time_horizon"), TIME_HORIZONS, None),
            "value_type": _coerce(item.get("value_type"), VALUE_TYPES, None),
            "is_regulatory": bool(item.get("is_regulatory")),
            "required_domains": [
                {"name": n, "label": domain_index[n].get("label") or n,
                 "satisfied": n in satisfied_names} for n in required],
            "helpful_domains": [
                {"name": n, "label": domain_index[n].get("label") or n,
                 "satisfied": n in satisfied_names} for n in helpful],
            "missing_domains": [
                {"name": n, "label": domain_index[n].get("label") or n} for n in missing],
        })
        if len(out) >= limit:
            break

    if dropped_domains:
        warnings.append(
            "Ignored data domains the model invented: "
            + ", ".join(sorted(dropped_domains)[:8])
            + ". Only domains in your vocabulary can be required.")
    return out, warnings


def summarize_batch(candidates: list[dict]) -> dict:
    """Rollup for the preview header."""
    ready = sum(1 for c in candidates if c["lens"] == "ready")
    return {
        "total": len(candidates),
        "ready": ready,
        "gap": len(candidates) - ready,
        "regulatory": sum(1 for c in candidates if c["is_regulatory"]),
        "quick_wins": sum(1 for c in candidates if c["time_horizon"] == "quick_win"),
        "distinct_missing_domains": len(
            {d["name"] for c in candidates for d in c["missing_domains"]}),
    }
