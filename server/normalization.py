"""Source-system canonicalization.

THE PROBLEM
-----------
AI enrichment labels each discovered table with a free-text `source_system`. Left
alone, that yields roughly a thousand distinct strings for the ~50 systems a
utility actually runs — "PI Historian", "OSIsoft PI Historian", "AVEVA PI",
"PI (Historian)", "pi historian" are all one system. Every rollup, gap analysis,
and Sankey built on the raw label is then meaningless.

THE CASCADE
-----------
Each unresolved raw label is pushed through progressively more expensive stages,
stopping at the first hit. Ordering is the whole design: the LLM is the last
resort, and only ever sees labels that nothing cheaper could resolve.

  1. exact       — case/whitespace-insensitive match against a canonical name.
  2. alias       — already-resolved raw label in `data_asset_aliases` (the memo
                   table). This is why a label costs at most one LLM call ever.
  3. normalized  — strip vendor noise (parentheticals, version suffixes,
                   punctuation, known vendor prefixes) and re-match.
  4. llm         — ONE batched call for whatever is left, with the canonical list
                   supplied as a closed vocabulary.
  5. Other       — no confident fit. Surfaced in the UI for a human to map.

INVARIANTS
----------
- Idempotent. Re-running resolves only labels not already in the memo table.
- `mapped_by='manual'` or `is_user_edited=true` is NEVER overwritten. A human
  correction is permanent, including across `--reseed`.
- The raw label is always preserved alongside the canonical.

This module holds the pure, testable logic. `routes/ingestion.py` owns the DB and
LLM calls that drive it.
"""
from __future__ import annotations

import re

# Vendor/product tokens that carry no canonical meaning. Stripped during stage 3
# so "OSIsoft PI Historian" and "AVEVA PI" both reduce toward "pi historian".
_VENDOR_NOISE = {
    "osisoft", "aveva", "sap", "oracle", "microsoft", "ms", "ibm", "ge",
    "generalelectric", "siemens", "schneider", "abb", "itron", "landisgyr",
    "landis", "gyr", "sensus", "honeywell", "emerson", "yokogawa", "esri",
    "aspentech", "aspen", "hitachi", "opentext", "salesforce", "servicenow",
    "maximo", "infor", "ellipse", "avantis", "smallworld", "milsoft",
    "survalent", "opennms", "netcracker", "clevest", "cgi", "itineris",
}

# Words that appear in nearly every label and so never discriminate.
_STOPWORDS = {
    "system", "systems", "data", "database", "db", "platform", "software",
    "application", "app", "tool", "suite", "server", "service", "the", "and",
    "of", "for", "solution", "module", "enterprise", "corporate", "legacy",
    "internal", "external", "prod", "production", "dev", "test", "uat", "qa",
}

_VERSION_RE = re.compile(r"\b(v|ver|version|release|r)?\s*\d+(\.\d+)*\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

OTHER = "Other"


def normalize_raw(value: str) -> str:
    """Lowercase + trim, the key used for case-insensitive alias lookup.

    Kept deliberately conservative: this is the *identity* of a raw label, so it
    must not collapse two genuinely different labels together.
    """
    return " ".join((value or "").strip().lower().split())


def canonical_key(value: str) -> str:
    """Aggressive fingerprint for fuzzy matching (stage 3).

    Drops version numbers, vendor names, stopwords, and all punctuation, then
    sorts the remaining tokens so word order doesn't matter. "OSIsoft PI
    Historian (v2023)" and "Historian - PI" both fingerprint to "historian pi".

    Parenthetical CONTENT is kept, only the brackets are dropped: in this domain
    the parenthetical usually carries the discriminating term — "EMS
    (Transmission)" vs "ADMS (Distribution)" differ only inside the parens, and
    "PI (Historian)" would otherwise reduce to a bare vendor token.

    This is intentionally a token-set match, not a similarity score. It unifies
    labels that share discriminating words and gives up otherwise: "AVEVA PI"
    fingerprints to "pi", which will NOT match "historian pi", so it falls
    through to the LLM stage. Guessing that a bare vendor token implies a
    capability is exactly the judgement the cascade defers to the model.

    Returns "" when nothing discriminating survives, which callers MUST treat as
    "no match" rather than as a key — otherwise every noise-only label would
    collide into one bucket.
    """
    text = (value or "").lower()
    text = _VERSION_RE.sub(" ", text)
    tokens = [t for t in _NON_ALNUM_RE.split(text) if t]
    kept = [t for t in tokens if t not in _STOPWORDS and t not in _VENDOR_NOISE]
    # If stripping removed everything, fall back to the non-stopword tokens so a
    # label that is *only* a vendor name ("Maximo") still fingerprints to
    # something rather than to "".
    if not kept:
        kept = [t for t in tokens if t not in _STOPWORDS]
    return " ".join(sorted(set(kept)))


def build_canonical_index(canonicals: list[str]) -> dict[str, str]:
    """Map fuzzy fingerprint -> canonical name.

    On collision the first canonical wins and later ones are skipped: a silently
    overwritten entry would make matching depend on list order. Callers should
    pass a de-duplicated, stably-ordered vocabulary.
    """
    index: dict[str, str] = {}
    for name in canonicals:
        key = canonical_key(name)
        if key and key not in index:
            index[key] = name
    return index


def resolve_deterministic(
    raw: str,
    canonicals: list[str],
    alias_map: dict[str, str] | None = None,
    canonical_index: dict[str, str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Stages 1-3. Returns (canonical, mapped_by, confidence).

    (None, None, None) means "nothing deterministic matched" — the caller should
    batch this label for the LLM stage.

    Args:
        raw: the free-text label to resolve.
        canonicals: the canonical vocabulary (e.g. data_assets.source_category).
        alias_map: normalized raw -> canonical, from the memo table.
        canonical_index: precomputed fingerprint index; built on demand if absent
            (pass it in when resolving many labels — it is O(len(canonicals))).
    """
    normalized = normalize_raw(raw)
    if not normalized:
        return None, None, None

    # Stage 1: exact, case-insensitively.
    for name in canonicals:
        if normalize_raw(name) == normalized:
            return name, "exact", "high"

    # Stage 2: previously resolved (including human corrections).
    if alias_map:
        hit = alias_map.get(normalized)
        if hit:
            return hit, "exact", "high"

    # Stage 3: fuzzy fingerprint.
    index = canonical_index if canonical_index is not None else build_canonical_index(canonicals)
    key = canonical_key(raw)
    if key and key in index:
        return index[key], "normalized", "medium"

    return None, None, None


def build_llm_prompt(unresolved: list[str], canonicals: list[str]) -> str:
    """Stage 4 prompt: ONE call for every remaining label.

    A closed vocabulary plus an explicit "Other" escape is what keeps this from
    inventing new system names. Asking for per-item confidence lets the caller
    route low-confidence answers to human review instead of trusting them.
    """
    listed = "\n".join(f"- {c}" for c in canonicals)
    items = "\n".join(f"{i + 1}. {label}" for i, label in enumerate(unresolved))
    return (
        "You are a Power & Utilities data architect normalizing source-system "
        "names. Each INPUT below is a free-text label for a system that feeds a "
        "data table. Map each one to exactly one name from the CANONICAL LIST.\n\n"
        "Rules:\n"
        f"- Use ONLY names from the canonical list, or \"{OTHER}\" if none fits.\n"
        "- Different vendors of the same capability map to the same canonical "
        "(e.g. both Maximo and SAP Plant Maintenance are asset/work management).\n"
        f"- If a label is ambiguous or clearly not a source system, return \"{OTHER}\" "
        "with confidence \"low\".\n"
        "- confidence is \"high\", \"medium\", or \"low\".\n\n"
        f"CANONICAL LIST:\n{listed}\n\n"
        f"INPUTS:\n{items}\n\n"
        "Return STRICT JSON, one entry per input, in the same order:\n"
        '{"mappings":[{"input":str,"canonical":str,"confidence":"high|medium|low"}]}'
    )


def parse_llm_mappings(
    parsed: dict | None,
    unresolved: list[str],
    canonicals: list[str],
) -> dict[str, tuple[str, str]]:
    """Validate an LLM mapping response. Returns {raw: (canonical, confidence)}.

    Every value is checked against the vocabulary, because a model will
    occasionally return a plausible-but-absent name however strict the prompt.
    Anything unrecognized becomes ('Other', 'low') so it lands in human review
    rather than silently corrupting the rollups. Labels the model omitted are
    left out entirely, so the caller can retry or default them.
    """
    if not parsed:
        return {}
    allowed = {normalize_raw(c): c for c in canonicals}
    requested = {normalize_raw(u): u for u in unresolved}
    out: dict[str, tuple[str, str]] = {}
    for item in parsed.get("mappings") or []:
        if not isinstance(item, dict):
            continue
        raw = requested.get(normalize_raw(str(item.get("input", ""))))
        if raw is None:
            continue  # a label we never asked about
        proposed = normalize_raw(str(item.get("canonical", "")))
        canonical = allowed.get(proposed)
        confidence = str(item.get("confidence", "")).lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "low"
        if canonical is None:
            # Includes the model explicitly answering "Other".
            out[raw] = (OTHER, "low")
        else:
            out[raw] = (canonical, confidence)
    return out
