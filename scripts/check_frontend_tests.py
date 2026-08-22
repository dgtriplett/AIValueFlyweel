#!/usr/bin/env python3
"""Run the SPA's plumbing unit tests.

    python3 scripts/check_frontend_tests.py [--verbose]

WHAT IT TESTS
-------------
`frontend/test/plumbing.test.ts` pins the three claims Tier-3 Phase 1 adds that
are invisible until they are wrong in front of a customer:

  1. the account header is injected on EVERY request (a missing one silently reads
     another tenant's data — TIER3_MIGRATION_PLAN.md §4.1);
  2. a 429 arrives as an `ApiError` carrying the server's message and
     `Retry-After`, instead of being flattened into "unavailable" (§4.4);
  3. the `<ConfirmCard>` state machine distinguishes expired / already-consumed /
     rate-limited, so it never offers a retry that is guaranteed to fail (§4.5).

It imports the real modules, interceptors attached, and drives axios through its
`adapter` option — so these are assertions about the shipped code, not about a
reimplementation of it.

WHY IT SKIPS INSTEAD OF FAILING WHEN TOOLING IS ABSENT
------------------------------------------------------
This is the same rule the rest of check.py follows: a missing tool is SKIP, never
PASS, because a green run that verified nothing is worse than a red one.

The constraint is CI's, and it is specific. `.github/workflows/ci.yml` pins
**node 20** and installs **no npm packages** — node is there only to `node --check`
the hand-written console. So:

  - node 20 lacks the type-stripping that runs a .ts file directly (that landed in
    22), and
  - without `npm install` there is no vitest, no tsx, and no esbuild.

Adding `npm ci` to CI to run these would mean installing ~160 packages to execute
24 assertions, on a workflow whose node step exists to syntax-check one file. The
honest arrangement is instead: run wherever the frontend has been installed (any
developer who has run `npm install`, on either node version — esbuild is a binary
and does not care), and report SKIP where it has not. The gate says which of those
happened rather than quietly counting absence as success.

Bundling via esbuild (a vite dependency, so already present) also keeps the tests
free of any *new* dependency: nothing was added to package.json for this.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
ENTRY = FRONTEND / "test" / "plumbing.test.ts"
ESBUILD = FRONTEND / "node_modules" / ".bin" / "esbuild"


class Skip(Exception):
    """Tooling is absent, so the check could not look — not a failure."""


def build(out: Path) -> None:
    """Bundle the test entry to CJS.

    CJS rather than ESM because axios's node build pulls CJS-only transitive
    dependencies (`form-data` -> `combined-stream` -> `require('util')`) that an
    ESM bundle cannot `require` at runtime.
    """
    completed = subprocess.run(
        [str(ESBUILD), str(ENTRY), "--bundle", "--format=cjs",
         "--platform=node", f"--outfile={out}", "--log-level=warning"],
        cwd=FRONTEND, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stdout.write((completed.stdout + completed.stderr)[-2000:])
        raise SystemExit("esbuild could not bundle the frontend tests")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true",
                        help="print each test name, not just the summary")
    args = parser.parse_args()

    try:
        if not ENTRY.exists():
            raise Skip(f"{ENTRY.relative_to(ROOT)} is missing")
        if not ESBUILD.exists():
            raise Skip("frontend/node_modules is absent (run: cd frontend && npm install)")
        node = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if node.returncode != 0:
            raise Skip("node is not installed")
    except Skip as skip:
        # Exit 0 with an explicit SKIP line: check.py's contract is that this
        # script fails only when a test fails, and the caller distinguishes
        # "verified" from "could not look" by reading this marker.
        print(f"SKIP: frontend plumbing tests — {skip}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "plumbing.test.cjs"
        build(bundle)
        completed = subprocess.run(["node", str(bundle)],
                                   cwd=FRONTEND, capture_output=True, text=True)

    output = (completed.stdout + completed.stderr).strip()
    if args.verbose or completed.returncode != 0:
        print(output)
    else:
        # The summary line only, so a passing gate stays one line in check.py's
        # output the way the other gates do.
        print(output.strip().split("\n")[-1])

    if completed.returncode != 0:
        print("FAILED: frontend plumbing tests")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
