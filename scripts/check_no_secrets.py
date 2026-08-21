#!/usr/bin/env python3
"""Fail if a credential or a workspace-specific value reached a tracked file.

    python3 scripts/check_no_secrets.py

WHY THIS EXISTS SEPARATELY FROM test_deploy.py
----------------------------------------------
test_deploy.py checks that app.yaml's env entries are empty. That is the specific
regression that has actually happened here (a Lakebase host, an SP client id, and
DEMO_MODE=on were once committed). This is the general case: any tracked file, any
credential shape.

It is a backstop, not a replacement for the pre-commit secret scanner — the
scanner sees only staged changes, so it cannot catch something that landed before
it was installed, or in a file someone committed with the hook bypassed.

DELIBERATELY NOT FLAGGED
------------------------
Documentation and comments have to be able to SHOW the shape of a value
("e.g. ep-xxxx.database..."), or INSTALL.md cannot explain what to set. So a
placeholder-looking match is allowed, and the patterns target values that look
real: a host with a concrete region, a UUID that is not all zeros or x's.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Substrings that mark a match as illustrative rather than real.
PLACEHOLDER_MARKERS = (
    "xxxx", "xxx", "example", "your-", "<", "abc123", "aaaa", "0000",
    "changeme", "placeholder", "redacted", "dummy", "deadbeef",
)

PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("databricks PAT",
     re.compile(r"\bdapi[0-9a-f]{32}\b"),
     "a Databricks personal access token"),
    ("OAuth secret",
     re.compile(r"\bdose[0-9a-f]{32}\b"),
     "a Databricks OAuth client secret"),
    ("JWT",
     # Header.payload.signature with a realistic signature length.
     re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"),
     "a JSON Web Token (a Lakebase OAuth credential looks like this)"),
    ("AWS access key",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
     "an AWS access key id"),
    ("private key block",
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
     "a private key"),
    ("Lakebase endpoint host",
     # A concrete provisioned endpoint, e.g. ep-cool-name.database...
     re.compile(r"\bep-[a-z0-9\-]+\.database\.[a-z0-9.\-]+"),
     "a provisioned Lakebase endpoint host (workspace-specific)"),
    ("UUID",
     re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
     "a UUID that may identify a workspace service principal"),
    ("warehouse id",
     re.compile(r"\b[0-9a-f]{16}\b"),
     "a 16-character Databricks SQL warehouse id"),
    ("Genie space id",
     re.compile(r"\b[0-9a-f]{32}\b"),
     "a 32-character Genie space id"),
    ("deployed app hostname",
     re.compile(r"\b[a-z0-9\-]+-\d{10,}\.[a-z0-9.\-]*databricksapps\.com"),
     "a deployed app hostname containing a workspace id"),
    ("workspace URL with id",
     re.compile(r"\bdbc-[0-9a-f]{8}-[0-9a-f]{4}\.cloud\.databricks\.com"),
     "a specific workspace URL"),
]

# Files that legitimately contain credential-SHAPED strings.
ALLOWLIST_PATHS = {
    # Builds a JWT from base64 parts at runtime to test redaction; the assembled
    # value never appears as a literal, but the assertion text mentions the shape.
    "tests/test_logging.py",
    # Documents the redaction patterns it implements.
    "server/logging_setup.py",
    # This file: the patterns above are themselves credential-shaped.
    "scripts/check_no_secrets.py",
}

SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
                 ".woff", ".woff2", ".ttf", ".zip", ".xlsx")


def tracked_files() -> list[Path]:
    """Only files git tracks. Untracked local scratch is the developer's business."""
    result = subprocess.run(["git", "ls-files"], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("not a git repository (or git unavailable)")
    return [ROOT / line for line in result.stdout.split("\n") if line.strip()]


def looks_like_a_placeholder(match: str) -> bool:
    lowered = match.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def is_legitimate_identifier_fixture(relative: str, label: str,
                                     text: str, match: re.Match) -> bool:
    """Allow non-config identifiers without weakening credential detection."""
    if label not in {"UUID", "warehouse id", "Genie space id"}:
        return False
    if relative.startswith("tests/"):
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    line = text[line_start:line_end if line_end != -1 else len(text)]
    if label == "warehouse id":
        value = match.group(0)
        return value.isdigit() or "request_id" in line
    return False


def main() -> int:
    findings: list[str] = []
    scanned = 0

    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOWLIST_PATHS or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue   # binary or unreadable: nothing to scan
        scanned += 1

        for label, pattern, explanation in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if looks_like_a_placeholder(value):
                    continue
                if is_legitimate_identifier_fixture(relative, label, text, match):
                    continue
                line = text[:match.start()].count("\n") + 1
                # Truncated: the finding must not itself publish the secret into
                # a CI log that is more widely readable than the repo.
                preview = value[:12] + "…" if len(value) > 12 else value
                findings.append(
                    f"  {relative}:{line}  {label} — {explanation}\n"
                    f"      matched: {preview}")

    print(f"Scanned {scanned} tracked text file(s) for "
          f"{len(PATTERNS)} credential patterns.")
    if findings:
        print("\nCOMMITTED SECRET OR WORKSPACE-SPECIFIC VALUE:\n")
        print("\n".join(findings))
        print("\nRemove the value and rotate the credential. Removing it in a NEW "
              "commit is not sufficient — it stays in history.")
        return 1
    print("No committed credentials or workspace-specific hostnames found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
