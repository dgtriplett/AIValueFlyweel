"""Asset taxonomy — effective-dated classification of data assets.

WHY ONLY THREE DIMENSIONS
-------------------------
The BHE catalog classifies schemas across eight dimensions. Five of them restate
things this app already models, and importing them would create two competing
answers to the same question:

  category / data_domain  -> data_domains (the semantic layer, with real M:N
                             mappings and readiness semantics behind it)
  department              -> lobs
  industry_vertical       -> sub_vertical on use_cases and data_assets
  use_case                -> use_cases themselves, as first-class rows

What survives is the three that describe an asset in ways nothing else here does:

  integration_pattern  HOW the data arrives. Drives ingest effort and latency
                       expectations — a streaming source and a quarterly file drop
                       are not the same project even at identical business value.
  criticality          HOW MUCH it matters operationally. Independent of the value
                       a use case derives from it: a T1 source can serve a small
                       use case, and vice versa.
  vendor_type          Build / buy / share posture. Drives licensing and access
                       questions that decide whether "land this source" is a
                       two-week or six-month conversation.

WHY EFFECTIVE-DATED
-------------------
Classification changes as people learn ("this was T3 until we found the outage
model depends on it"). UPDATE-in-place loses that; a new row with the old one
closed keeps the history, and "current" is simply `effective_to IS NULL`. A partial
unique index enforces one current value per (asset, dimension).
"""
from __future__ import annotations

import json

DIMENSIONS = ("integration_pattern", "criticality", "vendor_type")

# Closed vocabularies. Mirrored by the CHECK on asset_taxonomy.dimension; the
# VALUES themselves are validated in Python so a bad LLM answer is rejected with
# a useful message rather than a constraint violation.
ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "integration_pattern": (
        "Real-time streaming",     # Kafka, MQTT, event hubs — sub-minute
        "Real-time replication",   # CDC / log-based mirroring
        "Micro-batch",             # minutes; scheduled incremental
        "Batch",                   # nightly / daily
        "Periodic file drop",      # SFTP / manual export, weekly or slower
        "API pull",                # REST/SOAP polling
        "Delta Sharing",           # already-governed share, no pipeline
        "Manual entry",            # spreadsheets, no system of record
    ),
    "criticality": (
        "T1 - Mission critical",   # grid ops / safety / regulatory reporting
        "T2 - Important",          # material to operations, tolerates an outage
        "T3 - Supporting",         # analytical / historical
    ),
    "vendor_type": (
        "Commercial COTS",         # licensed vendor product, on-prem or hosted
        "Cloud SaaS",              # vendor-hosted, API-first
        "Open source",
        "Internally built",
        "Delta Share provider",    # a partner shares it into the lakehouse
        "Regulator / market feed",  # ISO, NERC, EPA — external mandated source
    ),
}

# Directional ingest-difficulty weights, used to explain WHY a source is hard to
# land rather than only that it is. Deliberately coarse: these order options for a
# human, they are not a cost model.
INTEGRATION_EFFORT: dict[str, str] = {
    "Delta Sharing": "S",
    "API pull": "M",
    "Batch": "M",
    "Micro-batch": "M",
    "Real-time replication": "L",
    "Periodic file drop": "L",       # cheap to build, expensive to trust
    "Real-time streaming": "L",
    "Manual entry": "XL",            # the hard part is the process, not the code
}


class TaxonomyError(ValueError):
    """Invalid dimension or value."""


def validate(dimension: str, value: str) -> str:
    """Return the canonical value, or raise TaxonomyError.

    Matching is case-insensitive so a model answering "t1 - mission critical" is
    accepted and normalized rather than rejected on capitalization.
    """
    if dimension not in DIMENSIONS:
        raise TaxonomyError(
            f"Unknown dimension {dimension!r}. Allowed: {', '.join(DIMENSIONS)}")
    allowed = ALLOWED_VALUES[dimension]
    target = (value or "").strip().lower()
    for candidate in allowed:
        if candidate.lower() == target:
            return candidate
    raise TaxonomyError(
        f"{value!r} is not a valid {dimension}. Allowed: {', '.join(allowed)}")


def coerce(dimension: str, value: str) -> str | None:
    """Like validate() but returns None instead of raising.

    Used on LLM output, where one bad classification among hundreds should be
    dropped rather than aborting the batch.
    """
    try:
        return validate(dimension, value)
    except TaxonomyError:
        return None


# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------
RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "asset_taxonomy",
        "schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "asset_id": {"type": "integer"},
                            "integration_pattern": {
                                "type": "string",
                                "enum": list(ALLOWED_VALUES["integration_pattern"])},
                            "criticality": {
                                "type": "string",
                                "enum": list(ALLOWED_VALUES["criticality"])},
                            "vendor_type": {
                                "type": "string",
                                "enum": list(ALLOWED_VALUES["vendor_type"])},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["asset_id", "integration_pattern",
                                     "criticality", "vendor_type"],
                    },
                },
            },
            "required": ["classifications"],
        },
        "strict": True,
    },
}


def build_prompt(assets: list[dict]) -> str:
    """Classify a batch of assets in one call.

    Batched because these are three short labels per asset: one call for 40 assets
    is far cheaper than 40 calls, and the model benefits from seeing sibling
    modules when judging criticality relatively.
    """
    listed = "\n".join(
        f"  - id={a['id']} | {a.get('source_category') or a.get('source_system')}"
        f" :: {a['module']}"
        + (f" | vendor: {a['vendor']}" if a.get("vendor") else "")
        + (f" | {str(a['description'])[:160]}" if a.get("description") else "")
        for a in assets)

    def vocab(dimension: str) -> str:
        return "\n".join(f"    - {v}" for v in ALLOWED_VALUES[dimension])

    return f"""You are a Power & Utilities data architect classifying source-system modules.

For EACH module below, assign exactly one value per dimension. Use ONLY the listed
values, verbatim.

integration_pattern — how this data typically reaches a data platform:
{vocab('integration_pattern')}

criticality — how critical the data is to utility OPERATIONS (not how valuable it
is analytically; a T1 source can feed a small use case):
{vocab('criticality')}

vendor_type — what kind of thing produces it:
{vocab('vendor_type')}

Judge by what is TYPICAL for a regulated utility. Grid operations, safety, and
mandated regulatory reporting are T1. Market and ISO feeds are usually
"Regulator / market feed". Historians and SCADA are typically real-time.

MODULES:
{listed}

Also give a one-sentence `reasoning` per module explaining the criticality call.

Return STRICT JSON: {{"classifications": [{{"asset_id": <int>, ...}}]}}"""


def parse_classifications(parsed: dict | None, valid_ids: set[int]) -> tuple[list[dict], list[str]]:
    """Validate LLM classifications. Returns (rows, warnings).

    An entry is kept only if its asset id is real and at least one dimension
    validates; individual bad dimensions are dropped so a partially-good
    classification is still useful. Unknown ids are reported rather than ignored,
    because they usually mean the batch and the response drifted out of sync.
    """
    warnings: list[str] = []
    if not parsed:
        return [], ["The model returned no parseable classifications."]

    items = parsed.get("classifications")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            items = None
    if not isinstance(items, list) or not items:
        return [], ["The model returned no classifications."]

    rows: list[dict] = []
    seen: set[int] = set()
    unknown: list[int] = []
    dropped: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            asset_id = int(item.get("asset_id"))
        except (TypeError, ValueError):
            continue
        if asset_id not in valid_ids:
            unknown.append(asset_id)
            continue
        if asset_id in seen:
            continue  # first answer wins
        seen.add(asset_id)

        values: dict[str, str] = {}
        for dimension in DIMENSIONS:
            canonical = coerce(dimension, str(item.get(dimension) or ""))
            if canonical:
                values[dimension] = canonical
            else:
                dropped.append(f"{dimension} for asset {asset_id}")
        if not values:
            continue
        rows.append({
            "asset_id": asset_id,
            "values": values,
            "reasoning": str(item.get("reasoning") or "").strip() or None,
        })

    if unknown:
        warnings.append(
            "Ignored classifications for asset ids not in this batch: "
            + ", ".join(str(i) for i in sorted(set(unknown))[:8]))
    if dropped:
        warnings.append(
            f"Dropped {len(dropped)} invalid dimension value(s), e.g. {dropped[0]}.")
    missing = valid_ids - seen
    if missing:
        warnings.append(
            f"{len(missing)} asset(s) were not classified and remain unlabelled.")
    return rows, warnings


def summarize(rows: list[dict]) -> dict:
    """Distribution per dimension, for the taxonomy dashboard."""
    out: dict[str, dict[str, int]] = {d: {} for d in DIMENSIONS}
    for row in rows:
        for dimension, value in row.get("values", {}).items():
            out[dimension][value] = out[dimension].get(value, 0) + 1
    return out
