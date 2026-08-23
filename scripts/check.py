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


def check_frontend_tests() -> Result:
    """The SPA's plumbing unit tests (account header, 429 -> ApiError, ConfirmCard).

    Delegates to scripts/check_frontend_tests.py, which prints a `SKIP:` line and
    exits 0 when the toolchain it needs is absent — CI pins node 20 and installs no
    npm packages, so it cannot run these. That is reported as SKIP rather than PASS
    here for the same reason as every other gate: a green summary must never stand
    in for a check that never executed. See that script's docstring for why adding
    `npm ci` to CI to run 24 assertions is the wrong trade.
    """
    name = "frontend plumbing tests"
    script = ROOT / "scripts" / "check_frontend_tests.py"
    if not script.exists():
        return Result(name, "SKIP", "check_frontend_tests.py missing")
    print(f"\n=== {name}\n    $ python3 {script.relative_to(ROOT)}", flush=True)
    completed = subprocess.run([sys.executable, str(script)],
                               cwd=ROOT, capture_output=True, text=True)
    output = (completed.stdout + completed.stderr).strip()
    if output:
        print("\n".join(f"    {line}" for line in output.split("\n")[-40:]))
    if completed.returncode != 0:
        return Result(name, "FAIL", f"exit {completed.returncode}")
    if output.startswith("SKIP:"):
        return Result(name, "SKIP", output[len("SKIP:"):].strip())
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

    # Pure source parsing, so CI can prove every nav destination renders without
    # installing npm packages or relying on TypeScript being available there.
    results.append(run("SPA TabIds have render cases",
                       [sys.executable, str(ROOT / "scripts"
                                            / "check_tab_render.py")]))

    results.append(check_app_imports())

    # Runs unconditionally: a committed credential is the one failure that is
    # worse to find after a push than before, and it needs no external tooling.
    results.append(run("no committed secrets",
                       [sys.executable, str(ROOT / "scripts" / "check_no_secrets.py")]))

    if not args.fast:
        results.append(run("lint (ruff)", ["ruff", "check", "."],
                           optional_tool="ruff"))
        # frontend/dist is committed and serves the app's front door, and it is now
        # BUILT from frontend/src rather than patched in place. The failure this
        # catches is a stale dist: source says one thing, the bundle a customer
        # loads says another, and nothing else notices. It asserts the behaviour —
        # grouped nav, the knowledge-base and proposal links, the drawer's proposal
        # action, the current product name, no customer-visible phase text — on the
        # exact chunk index.html references.
        #
        # This replaces the historical patch_spa_grouped_nav.py,
        # patch_spa_proposal_button.py, and patch_spa_customer_visibility.py
        # `--check` gates. Those asserted
        # minified identifiers (`Dg.find(x=>x.id===`) that a minifier reassigns on
        # every build, so they could only ever pass for one historical bundle.
        # The structural half of what they guarded moved to
        # tests/test_console_nav.py::TestSpaSourceIsTheSourceOfTruth, which checks
        # the source and is a stronger claim than a substring of minified output.
        results.append(run("SPA bundle carries the source's behaviour",
                           [sys.executable, str(ROOT / "scripts"
                                                / "check_spa_bundle.py")]))
        results.append(check_frontend_tests())

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
