"""Classification rules — teach the app a utility's naming conventions.

WHY
---
Every utility names its catalogs and schemas by some convention nobody wrote down:
`prod_`, `_dw`, an operating-company prefix, a region code in position three. After
a discovery sweep those conventions are the difference between 40,000 anonymous
tables and 40,000 tables you can filter by environment and business unit.

The app cannot guess the convention, and hand-correcting thousands of rows is not a
real option. So a user states the rule once and it applies to everything discovered,
including future sweeps.

DESIGN
------
Rules are (dimension, field, match_type, pattern) -> value, ordered by priority.
FIRST MATCH WINS per dimension, so a specific rule can be ordered ahead of a
general one — which is how real conventions work ("everything starting `dev_` is
dev, EXCEPT `dev_shared` which is actually prod").

`ignore` is a dimension rather than a flag because it composes with the same
matching machinery: an ignore rule is just a rule whose effect is exclusion.

Regex is offered alongside prefix/suffix/contains because conventions are often
positional in a way substring matching cannot express (`opco_(\\w+)_prod`). Patterns
are compiled defensively: a bad regex disables that one rule and reports itself,
rather than breaking classification for everything.
"""
from __future__ import annotations

import re

DIMENSIONS = ("environment", "lob", "source_category", "zone", "ignore")
FIELDS = ("catalog_name", "schema_name", "table_name", "owner", "comment")
MATCH_TYPES = ("equals", "prefix", "suffix", "contains", "regex")

# Conventions common enough to be worth shipping. Environment naming is nearly
# universal; business-unit prefixes are utility-specific, so none are seeded.
SEED_RULES = [
    {"dimension": "environment", "field": "catalog_name", "match_type": "prefix",
     "pattern": "prod", "value": "production", "priority": 10,
     "notes": "Common convention: prod-prefixed catalogs."},
    {"dimension": "environment", "field": "catalog_name", "match_type": "prefix",
     "pattern": "dev", "value": "development", "priority": 10},
    {"dimension": "environment", "field": "catalog_name", "match_type": "prefix",
     "pattern": "test", "value": "test", "priority": 10},
    {"dimension": "environment", "field": "catalog_name", "match_type": "prefix",
     "pattern": "stg", "value": "staging", "priority": 10},
    {"dimension": "environment", "field": "catalog_name", "match_type": "contains",
     "pattern": "sandbox", "value": "sandbox", "priority": 20},
    # Platform internals: never customer data, and they distort every count.
    {"dimension": "ignore", "field": "schema_name", "match_type": "equals",
     "pattern": "information_schema", "value": None, "priority": 1,
     "notes": "Catalog metadata, not customer data."},
    {"dimension": "ignore", "field": "catalog_name", "match_type": "equals",
     "pattern": "__databricks_internal", "value": None, "priority": 1},
    {"dimension": "ignore", "field": "catalog_name", "match_type": "equals",
     "pattern": "samples", "value": None, "priority": 1,
     "notes": "Databricks sample data."},
]


class RuleError(ValueError):
    """An invalid rule definition."""


def validate_rule(rule: dict) -> dict:
    """Validate and normalize one rule, raising RuleError with a usable message."""
    dimension = str(rule.get("dimension") or "").strip().lower()
    if dimension not in DIMENSIONS:
        raise RuleError(f"dimension must be one of {list(DIMENSIONS)}")
    field = str(rule.get("field") or "").strip().lower()
    if field not in FIELDS:
        raise RuleError(f"field must be one of {list(FIELDS)}")
    match_type = str(rule.get("match_type") or "").strip().lower()
    if match_type not in MATCH_TYPES:
        raise RuleError(f"match_type must be one of {list(MATCH_TYPES)}")
    pattern = str(rule.get("pattern") or "")
    if not pattern.strip():
        raise RuleError("pattern cannot be empty")

    if match_type == "regex":
        # Compile now so the user learns immediately, not on the next sweep.
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleError(f"invalid regex: {exc}")

    value = rule.get("value")
    value = str(value).strip() if value not in (None, "") else None
    # Every dimension except `ignore` exists to ASSIGN something.
    if dimension != "ignore" and not value:
        raise RuleError(f"a '{dimension}' rule needs a value to assign")
    if dimension == "ignore" and value:
        raise RuleError("an 'ignore' rule must not have a value")

    return {
        "dimension": dimension, "field": field, "match_type": match_type,
        "pattern": pattern, "value": value,
        "case_sensitive": bool(rule.get("case_sensitive", False)),
        "priority": int(rule.get("priority", 100)),
        "is_active": bool(rule.get("is_active", True)),
        "notes": str(rule.get("notes") or "").strip() or None,
    }


def matches(rule: dict, subject: str | None) -> bool:
    """Does `rule` match this field value?

    A broken regex returns False rather than raising, so one bad rule cannot break
    classification for an entire estate. `validate_rule` is the place that surfaces
    the problem to a human.
    """
    if subject is None:
        return False
    pattern = rule.get("pattern") or ""
    if not pattern:
        return False
    match_type = rule.get("match_type")

    if rule.get("case_sensitive"):
        text, needle = str(subject), pattern
    else:
        text, needle = str(subject).lower(), pattern.lower()

    if match_type == "equals":
        return text == needle
    if match_type == "prefix":
        return text.startswith(needle)
    if match_type == "suffix":
        return text.endswith(needle)
    if match_type == "contains":
        return needle in text
    if match_type == "regex":
        flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
        try:
            return re.search(pattern, str(subject), flags) is not None
        except re.error:
            return False
    return False


def classify(row: dict, rules: list[dict]) -> dict:
    """Apply rules to one discovered row.

    Returns {dimension: value} for each dimension that matched, plus `ignored` and
    `matched_rules` (rule ids, so a user can see WHY something was classified —
    without that, a surprising result is undebuggable).

    First match wins per dimension: callers pass rules pre-sorted by priority.
    """
    assigned: dict[str, str] = {}
    matched: list[int] = []
    ignored = False

    for rule in rules:
        if not rule.get("is_active", True):
            continue
        dimension = rule["dimension"]
        if dimension != "ignore" and dimension in assigned:
            continue  # already decided by a higher-priority rule
        if not matches(rule, row.get(rule["field"])):
            continue
        matched.append(rule.get("id"))
        if dimension == "ignore":
            ignored = True
            # Keep going: knowing which other rules also matched is useful when
            # deciding whether the ignore rule is too broad.
            continue
        assigned[dimension] = rule["value"]

    return {**assigned, "ignored": ignored, "matched_rules": matched}


def test_rules(rules: list[dict], samples: list[dict]) -> list[dict]:
    """Dry-run rules over sample rows.

    Exists because a rule that looks right is often wrong on real names, and
    finding that out by re-running a sweep over 40,000 tables is a bad loop.
    """
    return [{"sample": sample, "result": classify(sample, rules)}
            for sample in samples]


def summarize(results: list[dict]) -> dict:
    """Distribution of a dry run, for the preview panel."""
    out: dict[str, dict[str, int]] = {}
    ignored = 0
    unmatched = 0
    for entry in results:
        result = entry["result"]
        if result.get("ignored"):
            ignored += 1
        assignments = {k: v for k, v in result.items()
                       if k not in ("ignored", "matched_rules")}
        if not assignments and not result.get("ignored"):
            unmatched += 1
        for dimension, value in assignments.items():
            bucket = out.setdefault(dimension, {})
            bucket[value] = bucket.get(value, 0) + 1
    return {
        "total": len(results),
        "ignored": ignored,
        # The number that tells you whether the rules are actually working.
        "unmatched": unmatched,
        "by_dimension": out,
    }
