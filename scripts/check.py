#!/usr/bin/env python3
"""Run every gate CI runs, locally.

    python3 scripts/check.py            # everything
    python3 scripts/check.py --fast     # skip the optional external tools

WHY A SCRIPT AND NOT JUST A WORKFLOW FILE
-----------------------------------------
If the checks live only in YAML, the first time anyone runs them is after a push,
and "fix CI" becomes a series of blind commits. Worse, a workflow that has never
run locally tends to encode a different Python version, a different dependency
set, or a step that silently passes because the tool it invokes isn't installed.

So this script is the single definition of "does this repo pass", the workflow
just calls it, and it reports which gates were SKIPPED rather than counting a
missing tool as a pass — a green run that verified nothing is worse than a red one.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


class Result:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name: str, status: str, detail: str = ""):
        self.name, self.status, self.detail = name, status, detail


def run(name: str, args: list[str], *, cwd: Path = ROOT,
        optional_tool: str | None = None) -> Result:
    """Run one gate. A missing optional tool is SKIP, never PASS."""
    if optional_tool and shutil.which(optional_tool) is None:
        return Result(name, "SKIP", f"{optional_tool} not installed")
    print(f"\n=== {name}\n    $ {' '.join(args)}", flush=True)
    completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    output = (completed.stdout + completed.stderr).strip()
    if output:
        print("\n".join(f"    {line}" for line in output.split("\n")[-40:]))
    if completed.returncode != 0:
        return Result(name, "FAIL", f"exit {completed.returncode}")
    return Result(name, "PASS")


def check_console_bundle() -> Result:
    """The operator console is hand-written JS served as-is — no build step.

    That means a syntax error ships and the page renders blank with the error only
    in the browser console. There is no bundler to catch it, so this is the only
    gate between a typo and a customer seeing an empty tab.
    """
    console = ROOT / "frontend" / "console" / "console.js"
    if not console.exists():
        return Result("console bundle syntax", "FAIL", "console.js missing")
    if shutil.which("node") is None:
        return Result("console bundle syntax", "SKIP", "node not installed")
    completed = subprocess.run(["node", "--check", str(console)],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stderr.strip()[:1500])
        return Result("console bundle syntax", "FAIL", "console.js does not parse")
    return Result("console bundle syntax", "PASS")


def check_app_imports() -> Result:
    """Import app.py in a clean interpreter, against the REAL dependencies.

    The test suite installs stubs for asyncpg/openai/aiohttp, so it would keep
    passing even if the app could only import WITH those stubs in place. This runs
    without them, which is what the deployed app actually does — it is the gate
    that catches "works in tests, crashes on boot".

    Requires the runtime dependencies to be installed. A laptop that has never
    `pip install -r requirements.txt`'d is a SKIP, not a FAIL: the check has not
    found a problem, it has been unable to look. CI installs them, so CI runs it.
    """
    name = "app imports cleanly (real deps)"
    probe = subprocess.run([sys.executable, "-c", "import asyncpg, openai, aiohttp"],
                           cwd=ROOT, capture_output=True, text=True)
    if probe.returncode != 0:
        missing = probe.stderr.strip().split("'")[-2] if "'" in probe.stderr else "?"
        return Result(name, "SKIP",
                      f"runtime deps not installed ({missing}); "
                      "pip install -r requirements.txt")

    completed = subprocess.run(
        [sys.executable, "-c",
         "import app; assert app.app is not None; "
         "print('routes:', len(app.app.routes))"],
        cwd=ROOT, capture_output=True, text=True)
    output = (completed.stdout + completed.stderr).strip()
    print(f"\n=== {name}\n    {output[-800:]}")
    if completed.returncode != 0:
        return Result(name, "FAIL",
                      "app.py does not import against the real dependencies")
    return Result(name, "PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fast", action="store_true",
                        help="skip gates needing tools that may not be installed")
    args = parser.parse_args()

    results: list[Result] = []

    # The suite is stdlib-only by design, so this runs anywhere.
    results.append(run("unit tests",
                       [sys.executable, "-m", "unittest", "discover",
                        "-s", "tests", "-p", "test_*.py"]))

    results.append(check_app_imports())

    # Runs unconditionally: a committed credential is the one failure that is
    # worse to find after a push than before, and it needs no external tooling.
    results.append(run("no committed secrets",
                       [sys.executable, str(ROOT / "scripts" / "check_no_secrets.py")]))

    if not args.fast:
        results.append(run("lint (ruff)", ["ruff", "check", "."],
                           optional_tool="ruff"))
        results.append(check_console_bundle())

    print("\n" + "=" * 62)
    for result in results:
        marker = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[result.status]
        print(f"[{marker}] {result.name}"
              + (f"  ({result.detail})" if result.detail else ""))

    failed = [r for r in results if r.status == "FAIL"]
    skipped = [r for r in results if r.status == "SKIP"]
    print("=" * 62)
    if skipped:
        # Called out explicitly so a green summary is never mistaken for full
        # coverage when a tool was simply absent.
        print(f"{len(skipped)} gate(s) skipped — not verified: "
              f"{', '.join(r.name for r in skipped)}")
    if failed:
        print(f"FAILED: {', '.join(r.name for r in failed)}")
        return 1
    print("All gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
