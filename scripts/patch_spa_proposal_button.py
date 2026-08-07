#!/usr/bin/env python3
"""Add a "Write proposal" action to the SPA's use-case detail drawer.

    python3 scripts/patch_spa_proposal_button.py [--check]

WHY
---
The proposal agent lives at /console/#proposals and takes a use-case id typed into a
box. That is backwards: you decide to write a proposal while looking AT a use case,
not while looking at a form that asks which one. Every extra step between the
intention and the action is a step where the feature does not get used.

This puts the action where the decision happens — beside Edit in the drawer header —
and carries the id across, so the console opens already pointed at that use case.

WHY A LINK AND NOT AN IN-DRAWER GENERATION
------------------------------------------
Generating takes ~30 seconds and produces an 8,000-character document that needs a
preview, a confirm gate, and a markdown renderer. All of that already exists in the
console. Rebuilding it inside a minified React drawer would mean reimplementing the
confirm flow in a component whose source is not in this repo — far more risk than
the round trip is worth, and the console page is a better place to read a long
document anyway.

So the button is an anchor to /console/#proposals/<id>. The console reads the id
from the hash and pre-fills its form.

SAFETY
------
Same discipline as the other bundle patches: the anchor must appear exactly once, the
result is syntax-checked before it is written, `--check` reports status without
modifying anything, and it is idempotent.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ASSETS = "frontend/dist/assets"
MARKER = "gaProposalBtn"

# The drawer header's action row: the Edit button, immediately followed by Close.
# Matching both sides means a bundle whose drawer changed shape fails the assert
# rather than getting a button injected somewhere unrelated.
ANCHOR = (
    'a.jsxs("button",{className:"text-navy-400 hover:text-lava-300 flex '
    'items-center gap-1 text-sm",onClick:()=>f(!0),children:[a.jsx(gp,'
    '{className:"w-4 h-4"})," Edit"]}),a.jsx("button",{"aria-label":'
)

# The new anchor element, inserted between Edit and Close.
#
# `m` is the use case bound in this scope (the same variable the Save handler uses),
# so the link always points at the record on screen. Guarded with `m&&m.id` because
# the drawer renders briefly before its data arrives, and an href of
# "#proposals/undefined" would open the console on nothing.
REPLACEMENT = (
    'a.jsxs("button",{className:"text-navy-400 hover:text-lava-300 flex '
    'items-center gap-1 text-sm",onClick:()=>f(!0),children:[a.jsx(gp,'
    '{className:"w-4 h-4"})," Edit"]}),'
    f'm&&m.id?a.jsx("a",{{"data-{MARKER}":"1",'
    'href:`/console/#proposals/${m.id}`,'
    'title:"Generate an eight-section proposal for this use case, grounded in its '
    'computed value, its real data gaps and this instance\\u2019s company profile",'
    'className:"text-navy-400 hover:text-lava-300 flex items-center gap-1 text-sm",'
    'children:"\\u270e Write proposal"},"ga-proposal"):null,'
    'a.jsx("button",{"aria-label":'
)


def _syntax_error(source: str) -> str | None:
    """Return a diagnostic if `source` does not parse, else None.

    Checked as .mjs: the bundle is an ES module, and `node --check` rejects
    `export` in a .js file — so a .js check would report a false failure.
    """
    if shutil.which("node") is None:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8",
                                     errors="surrogateescape") as handle:
        handle.write(source)
        temp = handle.name
    try:
        result = subprocess.run(["node", "--check", temp],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return None
        lines = [line for line in result.stderr.split("\n")
                 if line.strip() and len(line) < 200]
        return " ".join(lines[-2:])[:180] or f"exit {result.returncode}"
    finally:
        os.unlink(temp)


def patch_one(path: str, *, check: bool) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()

    if MARKER in text:
        return "already"
    if ANCHOR not in text:
        return "skipped"
    if text.count(ANCHOR) != 1:
        return f"error: anchor appears {text.count(ANCHOR)}x — refusing to guess"
    if check:
        return "would patch"

    patched = text.replace(ANCHOR, REPLACEMENT, 1)
    problem = _syntax_error(patched)
    if problem:
        return f"error: patched bundle does not parse ({problem})"

    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(patched)
    return "patched"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report status without modifying anything")
    args = parser.parse_args()

    entries = sorted(glob.glob(f"{ASSETS}/index-*.js"))
    if not entries:
        sys.exit(f"no entry bundles found in {ASSETS}")

    statuses = {}
    for path in entries:
        status = patch_one(path, check=args.check)
        statuses[os.path.basename(path)] = status
        print(f"  {os.path.basename(path):32} {status}")

    if any(s.startswith("error") for s in statuses.values()):
        sys.exit("\nrefused to patch — inspect the bundle before retrying")
    if not any(s in ("patched", "already", "would patch")
               for s in statuses.values()):
        sys.exit("\nno bundle carried the drawer's Edit button — the SPA may have "
                 "been rebuilt. Re-derive the anchor before retrying.")
    print("\n  drawer proposal button present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
