"""Lookup tables for deterministic source-system resolution.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
The vendor aliases and the per-module keywords both live in `scripts/` — the vendor
table because it is reference data a customer may want to extend, the keywords
because they are part of the shipped catalog that seeds the database. Neither is a
`server` package, and the running app must not depend on `scripts/` being importable
(it is excluded from some deployment layouts).

So this module is the seam: it finds those tables if they are there, builds the
indexes once, and returns None-ish results if they are not. A missing table means
fewer deterministic hits and more LLM calls — degraded, not broken.

WHY THE INDEXES ARE BUILT ONCE
------------------------------
Normalization runs over every discovered schema in an estate, which is tens of
thousands of labels. Rebuilding a 584-entry keyword index per label turns a linear
sweep into a quadratic one; the ingestion endpoint would appear to hang on a large
customer.
"""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# scripts/ sits beside server/ in the repo and in the deployed bundle.
_SCRIPTS = Path(__file__).parent.parent / "scripts"


def _ensure_path() -> None:
    if _SCRIPTS.is_dir() and str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))


@lru_cache(maxsize=1)
def _vendors():
    """The vendor alias table, or None when it is unavailable."""
    _ensure_path()
    try:
        import pu_vendors
        return pu_vendors
    except Exception as exc:  # noqa: BLE001
        logger.info("vendor alias table unavailable (%s) — stage 4 disabled; "
                    "product names will fall through to the model",
                    type(exc).__name__)
        return None


@lru_cache(maxsize=1)
def _keyword_index() -> dict[str, tuple[str, str]]:
    """keyword -> (source_category, module), built from the shipped catalog.

    Longest keywords first, so a specific phrase ("interval usage") is preferred
    over a generic word ("usage") when both are present. First writer wins on a
    collision, which makes the result independent of dict ordering.
    """
    _ensure_path()
    try:
        import pu_catalog
    except Exception as exc:  # noqa: BLE001
        logger.info("catalog keywords unavailable (%s) — stage 5 disabled",
                    type(exc).__name__)
        return {}

    index: dict[str, tuple[str, str]] = {}
    for entry in getattr(pu_catalog, "MODULES", []):
        try:
            category, module, keywords = entry[0], entry[1], entry[2]
        except (IndexError, TypeError):
            continue
        for keyword in keywords or []:
            key = str(keyword).strip().lower()
            # Single characters and very short tokens match everything.
            if len(key) < 3:
                continue
            index.setdefault(key, (category, module))
    return index


# Keywords too generic to assert a system from on their own. Each appears in the
# catalog for a good reason (it IS descriptive of that module) but is also ordinary
# English that shows up in unrelated schema names — "reading" and "usage" would
# attribute half a warehouse to metering.
_GENERIC_KEYWORDS = frozenset({
    "reading", "readings", "usage", "event", "events", "alarm", "alarms",
    "status", "history", "record", "records", "data", "log", "logs", "detail",
    "summary", "master", "reference", "actual", "actuals", "forecast", "plan",
    "cost", "costs", "customer", "account", "asset", "assets", "location",
    "device", "devices", "meter", "point", "points", "value", "values",
    "measurement", "measurements", "quality", "test", "sample", "report",
    # "project" matched `poc_project` to ERP in a false-positive sweep. Every
    # analytics estate has project-named schemas that are not the ERP's Project
    # System, and "project system" as a phrase still matches.
    "project", "projects", "budget", "invoice", "contract", "order", "orders",
    "schedule", "work", "notification", "notifications", "case", "cases",
    "interval", "connectivity", "model", "network",
    # "outage" is guarded even though it is highly domain-specific: an analytics
    # estate has outage reporting schemas that are not the OMS. The
    # `outage + management` pair below is what still resolves the real system.
    "outage", "outages",
})

# Words that are generic ALONE but unambiguous in combination. Without this,
# guarding "outage" to stop `poc_project`-style false positives also broke
# `outage_management_system`, which is about as clear a system name as exists.
_QUALIFIED_PAIRS: tuple[tuple[frozenset[str], tuple[str, str]], ...] = (
    (frozenset({"outage", "management"}),
     ("ADMS (Distribution)", "OMS (Outage Management)")),
    (frozenset({"work", "management"}), ("EAM/APM", "Work Management")),
    (frozenset({"asset", "management"}), ("EAM/APM", "Asset Register")),
    (frozenset({"meter", "management"}), ("Meter/AMI/MDM", "MDM Repository")),
    (frozenset({"meter", "reading"}), ("Meter/AMI/MDM", "Interval Usage Collection")),
    (frozenset({"interval", "usage"}), ("Meter/AMI/MDM", "Interval Usage Collection")),
    (frozenset({"customer", "billing"}), ("CIS/Billing", "Billing Engine / Determinants")),
    (frozenset({"network", "model"}), ("GIS", "Electric Network Model / Connectivity")),
    (frozenset({"vegetation", "management"}), ("GIS", "Vegetation Management")),
    (frozenset({"project", "system"}), ("ERP", "Project System")),
    (frozenset({"capital", "project"}), ("ERP", "Project System")),
    (frozenset({"general", "ledger"}), ("ERP", "Finance & Controlling")),
)


def alias_lookup(tokens: set[str]) -> tuple[str, str | None, str] | None:
    """Stage 4: resolve a product or vendor name.

    Returns (source_category, module_or_None, matched_alias) or None.
    """
    vendors = _vendors()
    if vendors is None:
        return None
    try:
        return vendors.alias_lookup(tokens)
    except Exception as exc:  # noqa: BLE001 - a bad table must not break ingestion
        logger.warning("vendor lookup failed (%s: %s)", type(exc).__name__, exc)
        return None


def keyword_lookup(tokens: set[str]) -> tuple[str, str, str] | None:
    """Stage 5: resolve on a catalog keyword.

    Returns (source_category, module, matched_keyword) or None. Multi-word keywords
    are preferred, then longer ones — "outage management" is far better evidence
    than "outage".
    """
    index = _keyword_index()
    if not index:
        return None

    # Qualified pairs first: they are the only way a word that is generic alone
    # ("outage", "work") can still resolve when it appears with a qualifier.
    for words, (category, module) in _QUALIFIED_PAIRS:
        if words <= tokens:
            return category, module, " ".join(sorted(words))

    best: tuple[str, str, str] | None = None
    best_score = 0
    for keyword, (category, module) in index.items():
        words = keyword.split()
        if len(words) == 1:
            if keyword in _GENERIC_KEYWORDS or keyword not in tokens:
                continue
        elif not set(words) <= tokens:
            continue
        # More words wins; then the longer string, which breaks ties toward the
        # more specific phrase.
        score = len(words) * 100 + len(keyword)
        if score > best_score:
            best, best_score = (category, module, keyword), score
    return best


def table_stats() -> dict:
    """Sizes of the loaded tables, for the setup screen and for tests."""
    vendors = _vendors()
    return {
        "vendor_aliases": len(getattr(vendors, "VENDOR_ALIASES", {}) or {}),
        "catalog_keywords": len(_keyword_index()),
        "vendor_table_loaded": vendors is not None,
    }
