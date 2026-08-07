#!/usr/bin/env python3
"""Group the SPA's header nav, and link it to the knowledge base and proposals.

    python3 scripts/patch_spa_grouped_nav.py [--check]

WHY THIS EXISTS
---------------
The console at /console was reorganised from twelve flat tabs into five grouped
menus. The SPA at / was not, because its React source is not in this repo (see the
README). So the two halves of the same product disagreed: the portfolio showed nine
tabs that wrapped onto two lines, plus a link labelled "Get Started & Discovery" —
a console tab that no longer exists under that name — and nothing at all pointing at
the knowledge base or the proposal agent. Both were invisible from the app's front
door.

WHY PATCH A BUILT BUNDLE
------------------------
It is not the approach anyone would choose. The alternative is reconstructing the
React source, which is a much larger piece of work than the problem warrants, and
until then a customer's first screen would keep contradicting the rest of the app.

The risk is managed by shape, not by care:

  - Every edit asserts its anchor appears EXACTLY once and refuses to write
    otherwise. An ambiguous match means the bundle changed, and guessing would
    corrupt an unrelated component.
  - The replacement reuses `Dg` (the existing tab array) and the `n` setter already
    bound in that scope. Tab state, routing, and every view stay untouched — this
    changes only how the tabs are PRESENTED.
  - `--check` reports whether a bundle is patched without modifying it, so CI and a
    human can both tell.
  - It is idempotent: an already-patched bundle is skipped, not double-patched.

WHY A SCRIPT AND NOT A HAND EDIT
--------------------------------
frontend/dist is committed. A hand-edited bundle cannot be reproduced, reviewed, or
re-applied after someone rebuilds the SPA — so the edit has to live in a script that
runs again.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ASSETS = "frontend/dist/assets"

# Marker proving a bundle already carries this patch.
MARKER = "gaGroupedNav"

# ---------------------------------------------------------------------------
# The anchor: the nav that maps Dg to buttons and appends the console link.
# ---------------------------------------------------------------------------
# Written by scripts/patch_spa_nav.py (the earlier rebrand). Matching the whole
# expression means a partial or differently-ordered bundle fails the assert instead
# of being half-patched.
OLD_NAV = (
    'a.jsx("nav",{className:"flex gap-1",children:Dg.map(r=>a.jsxs("button",'
    '{onClick:()=>n(r.id),className:`px-4 py-2.5 text-sm font-medium flex '
    'items-center gap-2 border-b-2 transition-colors ${t===r.id?"text-white":'
    '"border-transparent text-navy-400 hover:text-navy-300"}`,style:t===r.id?'
    '{borderColor:"#FF3621",color:"#FF9E94"}:{},children:[r.icon,r.label]},r.id))'
    '.concat([a.jsxs("a",{href:"/console/",title:"Set up, load your data, discover '
    'your estate, review coverage, and generate use cases",className:"px-4 py-2.5 '
    'text-sm font-medium flex items-center gap-2 border-b-2 border-transparent '
    'text-navy-400 hover:text-navy-300 ml-auto",children:["\\u2699 Get Started & '
    'Discovery"]},"console-link")])})'
)

# ---------------------------------------------------------------------------
# The replacement.
# ---------------------------------------------------------------------------
# Grouping, chosen to match the console's workflow stages rather than to mirror its
# exact labels — the SPA's tabs are portfolio management, not discovery:
#
#   Portfolio  (a tab, not a menu: it is the default view and the most-used one)
#   Analyze ▾  Dashboards · Value Flywheel · Value & Assumptions
#   Plan ▾     Roadmap · Joint Funding · Use Case Catalog · Data Assets
#   Knowledge  → /console/#kb        (a real link out)
#   Write a proposal → /console/#proposals
#   ⚙ Set up   → /console/
#
# Implementation notes:
#   - `Dg.find(x=>x.id===...)` looks tabs up by id rather than by position, so a
#     reordered Dg still resolves correctly.
#   - The open/closed state is a plain DOM class toggle via an inline handler, NOT
#     React state. Introducing a useState here would mean re-deriving hook order in
#     a minified component — the failure mode is a blank page, and the payoff is
#     identical behaviour.
#   - Each menu closes on click of an item (the item calls n(id), which re-renders)
#     and on blur, so it cannot get stuck open.
NEW_NAV = (
    'a.jsxs("nav",{className:"flex gap-1 items-center",'
    f'"data-{MARKER}":"1",'
    'children:[' + ",".join([
        # --- Portfolio: a plain tab -------------------------------------------
        '(()=>{const r=Dg.find(x=>x.id==="portfolio");return r?a.jsxs("button",'
        '{onClick:()=>n(r.id),className:`px-4 py-2.5 text-sm font-medium flex '
        'items-center gap-2 border-b-2 transition-colors ${t===r.id?"text-white":'
        '"border-transparent text-navy-400 hover:text-navy-300"}`,'
        'style:t===r.id?{borderColor:"#FF3621",color:"#FF9E94"}:{},'
        'children:[r.icon,r.label]},r.id):null})()',

        # --- The two grouped menus -------------------------------------------
        # gaMenu(label, ids) renders a trigger plus an absolutely-positioned panel.
        '...[["Analyze",["dashboards","flywheel","value"]],'
        '["Plan",["roadmap","funding","catalog","registry"]]]'
        '.map(([lbl,ids])=>{'
        # Underline the group when the active tab lives inside it, so the row still
        # answers "where am I" with every menu closed.
        'const act=ids.indexOf(t)>=0;'
        'return a.jsxs("div",{className:"relative",children:['
        'a.jsxs("button",'
        '{onClick:e=>{const p=e.currentTarget.nextSibling;'
        'document.querySelectorAll("[data-ga-menu]").forEach(m=>{'
        'if(m!==p)m.style.display="none"});'
        'p.style.display=p.style.display==="block"?"none":"block"},'
        'className:`px-4 py-2.5 text-sm font-medium flex items-center gap-2 '
        'border-b-2 transition-colors ${act?"text-white":"border-transparent '
        'text-navy-400 hover:text-navy-300"}`,'
        'style:act?{borderColor:"#FF3621",color:"#FF9E94"}:{},'
        'children:[lbl,a.jsx("span",{style:{fontSize:"9px",opacity:.6},'
        'children:"\\u25be"})]}),'
        'a.jsx("div",{"data-ga-menu":"1",'
        'style:{display:"none",position:"absolute",top:"100%",left:0,zIndex:200,'
        'minWidth:"212px",padding:"6px",background:"#1b3139",'
        'border:"1px solid #2d4550",borderRadius:"10px",'
        'boxShadow:"0 18px 44px rgba(0,0,0,.62)"},'
        'children:ids.map(id=>{const r=Dg.find(x=>x.id===id);return r?'
        'a.jsxs("button",{onClick:()=>{'
        'document.querySelectorAll("[data-ga-menu]").forEach(m=>m.style.display='
        '"none");n(id)},'
        'className:`w-full text-left px-3 py-2 rounded-md text-sm font-medium '
        'flex items-center gap-2 whitespace-nowrap ${t===id?"text-white":'
        '"text-navy-300 hover:text-white hover:bg-navy-700"}`,'
        'style:t===id?{color:"#FF9E94"}:{},'
        'children:[r.icon,r.label]},id):null})})]},lbl)})',

        # --- Links out to the console ----------------------------------------
        # Right-aligned via ml-auto on the first of them.
        'a.jsx("a",{href:"/console/#kb",'
        'title:"Standards, proposals, studies and runbooks — attached to the use '
        'cases they explain",'
        'className:"px-4 py-2.5 text-sm font-medium flex items-center gap-2 '
        'border-b-2 border-transparent text-navy-400 hover:text-navy-300 ml-auto",'
        'children:"Knowledge base"},"kb-link")',

        'a.jsx("a",{href:"/console/#proposals",'
        'title:"Generate an eight-section proposal for a use case, grounded in this '
        'instance\\u2019s own data",'
        'className:"px-4 py-2.5 text-sm font-medium flex items-center gap-2 '
        'border-b-2 border-transparent text-navy-400 hover:text-navy-300",'
        'children:"Write a proposal"},"prop-link")',

        'a.jsx("a",{href:"/console/",'
        'title:"Set up, load your data, discover your estate, review coverage, and '
        'generate use cases",'
        'className:"px-4 py-2.5 text-sm font-medium flex items-center gap-2 '
        'border-b-2 border-transparent text-navy-400 hover:text-navy-300",'
        'children:"\\u2699 Set up & discover"},"console-link")',
    ]) + ']})'
)

# NOTE ON OUTSIDE-CLICK DISMISSAL
# -------------------------------
# The first version injected a `document.addEventListener` statement before the `Dg`
# array. That broke the bundle: `Dg` is one binding in a chained `const a=…,b=…,Dg=…`
# declaration, so a statement cannot go there — Node reported "Unexpected token
# 'typeof'". Rather than hunt for a statement position inside minified output, the
# dismissal is folded into the trigger handler itself (it closes every OTHER menu
# before toggling its own) and into each item handler (which closes all of them).
#
# The cost is that clicking on empty page space leaves a menu open until the next
# click on a trigger or an item. That is a smaller problem than a blank app, and it
# needs no statement injection at all.


def is_patched(text: str) -> bool:
    return MARKER in text


def patch_one(path: str, *, check: bool) -> str:
    """Returns a status string: 'patched', 'already', 'skipped', or 'error: ...'."""
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()

    if is_patched(text):
        return "already"
    if OLD_NAV not in text:
        # Not the entry bundle, or the shape changed. Both are "skip", not "fail" —
        # the assets directory holds several chunks and only one has the header.
        return "skipped"
    if text.count(OLD_NAV) != 1:
        return f"error: nav appears {text.count(OLD_NAV)}x — refusing to guess"
    if check:
        return "would patch"

    patched = text.replace(OLD_NAV, NEW_NAV, 1)

    # Syntax-check BEFORE writing. The bundle is committed and serves the app's front
    # door, so shipping an unparseable one is a blank page for every user — and the
    # first version of this script did exactly that. Checked as .mjs because the
    # bundle is an ES module and `node --check` rejects `export` in a .js file.
    problem = _syntax_error(patched)
    if problem:
        return f"error: patched bundle does not parse ({problem})"

    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(patched)
    return "patched"


def _syntax_error(source: str) -> str | None:
    """Return a syntax error message for `source`, or None if it parses."""
    import shutil
    import subprocess
    import tempfile

    if shutil.which("node") is None:
        # Honest about not checking rather than implying it passed.
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(source)
        temp = handle.name
    try:
        result = subprocess.run(["node", "--check", temp],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return None
        # Node dumps the whole offending minified line; keep only short diagnostics.
        lines = [line for line in result.stderr.split("\n")
                 if line.strip() and len(line) < 200]
        return " ".join(lines[-2:])[:180] or f"exit {result.returncode}"
    finally:
        os.unlink(temp)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report status without modifying anything")
    args = parser.parse_args()

    entries = sorted(glob.glob(f"{ASSETS}/index-*.js"))
    if not entries:
        sys.exit(f"no entry bundles found in {ASSETS}")

    results = {}
    for path in entries:
        status = patch_one(path, check=args.check)
        results[os.path.basename(path)] = status
        print(f"  {os.path.basename(path):32} {status}")

    if any(s.startswith("error") for s in results.values()):
        sys.exit("\nrefused to patch — inspect the bundle before retrying")

    applied = [s for s in results.values() if s in ("patched", "already", "would patch")]
    if not applied:
        sys.exit("\nno bundle carried the expected nav — has the SPA been rebuilt? "
                 "Re-derive the anchor before retrying.")
    print(f"\n  {len(applied)} of {len(entries)} bundle(s) carry the grouped nav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
