#!/usr/bin/env python3
"""Patch customer-facing phase/readiness details in the built SPA bundles.

The React source for the SPA is not in this repo, so the committed Vite output is
the deployed UI source of record. Keep these edits reproducible and syntax-checked
instead of hand-editing minified files.
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
MARKER = "gaCustomerVisibility"


ENTRY_REPLACEMENTS = [
    (
        'function Kp({r:e,pendingPrereqs:t}){if(!e)return null;if(e==="awaiting_prerequisites"){const r=(t??[]).map(i=>i.title),s=r.length?`Data is ready — build these first: ${r.join(", ")}`:"Data is ready, but prerequisite use cases must be built first.";return a.jsxs("span",{className:"text-xs font-semibold px-2 py-0.5 rounded-full",style:{background:"rgba(124,107,255,0.18)",color:"#B3A7FF",border:"1px solid rgba(124,107,255,0.45)"},title:s,children:[Sl[e],r.length?` (${r.length})`:""]})}const n=e==="shovel_ready"?"badge-low":e==="nearly_ready"?"badge-high":"badge-critical";return a.jsx("span",{className:n,children:Sl[e]})}',
        'function Kp({r:e,pendingPrereqs:t}){if(!e)return null;if(e==="awaiting_prerequisites"){const r=(t??[]).map(i=>i.title),s=r.length?`Data is ready. Required prerequisites: ${r.join(", ")}`:"Data is ready, but prerequisite use cases must be built first.";return a.jsxs("span",{"data-gaCustomerVisibility":"1",className:"inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap",style:{background:"rgba(124,107,255,0.18)",color:"#B3A7FF",border:"1px solid rgba(124,107,255,0.45)"},title:s,children:[Sl[e],r.length?a.jsx("span",{className:"text-[10px] opacity-80",children:`(${r.length})`}):null,r.length?a.jsx("span",{className:"text-[10px] underline decoration-dotted",children:"details"}):null]})}const n=e==="shovel_ready"?"badge-low":e==="nearly_ready"?"badge-high":"badge-critical";return a.jsx("span",{className:n,children:Sl[e]})}',
    ),
    (
        'a.jsxs("select",{id:"filter-phase",name:"filter-phase","aria-label":"Filter by phase",className:ar,value:n.phase??"",onChange:l=>r({phase:l.target.value?Number(l.target.value):null}),children:[a.jsx("option",{value:"",children:"All phases"}),Vp.map(l=>a.jsxs("option",{value:l,children:["Phase ",l," · ",jl[l]]},l))]}),',
        '',
    ),
    (
        'a.jsx(or,{onClick:()=>V("phase"),children:"Phase"}),',
        '',
    ),
    (
        'a.jsx("div",{className:"text-xs text-navy-500",children:R.phase!=null?jl[R.phase]:""})',
        'null',
    ),
    (
        'a.jsx("td",{className:"px-3 py-2.5",children:a.jsx(Io,{phase:R.phase})}),',
        '',
    ),
    (
        'a.jsxs("div",{className:"flex items-center gap-2 mt-1",children:[a.jsx("span",{className:"text-xs text-navy-500",children:"Derived phase:"}),a.jsx(Io,{phase:i.phase}),a.jsx("span",{className:"text-xs text-navy-600",title:"Phase is computed from prerequisite depth",children:"(from prerequisite depth)"})]}),',
        '',
    ),
    (
        'a.jsx("p",{className:"text-xs text-navy-500",children:"Setting status to Live/Value realized auto-lands this use case\'s required data assets. Phase updates automatically as dependencies change."})',
        'a.jsx("p",{className:"text-xs text-navy-500",children:"Setting status to Live/Value realized auto-lands this use case\'s required data assets. Dependency order updates automatically as relationships change."})',
    ),
    (
        'a.jsx("span",{title:"Derived from prerequisite depth",children:a.jsx(Io,{phase:i.phase})}),',
        '',
    ),
]


CATALOG_REPLACEMENTS = [
    (
        'e.jsxs("table",{className:"w-full text-sm min-w-[900px]",',
        'e.jsxs("table",{"data-gaCustomerVisibility":"1",className:"w-full text-sm min-w-[900px]",',
    ),
    (
        'e.jsx("th",{className:"text-left px-3 py-2.5 font-medium",children:"Phase"}),',
        '',
    ),
    (
        'e.jsx("td",{className:"px-3 py-2.5",children:e.jsx(M,{phase:s.phase})}),',
        '',
    ),
]


def _syntax_error(source: str) -> str | None:
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
                 if line.strip() and len(line) < 220]
        return " ".join(lines[-2:])[:200] or f"exit {result.returncode}"
    finally:
        os.unlink(temp)


def _apply(path: str, replacements: list[tuple[str, str]], *, check: bool) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()

    changed = False
    patched = text
    for old, new in replacements:
        if old in patched:
            if patched.count(old) != 1:
                return f"error: anchor appears {patched.count(old)}x in {os.path.basename(path)}"
            patched = patched.replace(old, new, 1)
            changed = True
        elif new and new in patched:
            continue
        elif not new and MARKER in patched:
            continue
        else:
            return f"error: missing anchor in {os.path.basename(path)}: {old[:80]}"

    if MARKER in patched:
        changed = changed or MARKER not in text

    if not changed:
        return "already"
    if check:
        return "would patch"

    problem = _syntax_error(patched)
    if problem:
        return f"error: patched bundle does not parse ({problem})"

    with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write(patched)
    return "patched"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    targets = [(path, ENTRY_REPLACEMENTS)
               for path in sorted(glob.glob(f"{ASSETS}/index-*.js"))
               if "gaGroupedNav" in open(path, encoding="utf-8",
                                         errors="surrogateescape").read()]
    targets += [(path, CATALOG_REPLACEMENTS)
                for path in sorted(glob.glob(f"{ASSETS}/CatalogView-*.js"))]

    if not targets:
        sys.exit(f"no patchable SPA bundles found in {ASSETS}")

    statuses = {}
    for path, replacements in targets:
        status = _apply(path, replacements, check=args.check)
        statuses[os.path.basename(path)] = status
        print(f"  {os.path.basename(path):32} {status}")

    if any(status.startswith("error") for status in statuses.values()):
        sys.exit("\nrefused to patch customer visibility")
    if not any(status in ("patched", "already", "would patch")
               for status in statuses.values()):
        sys.exit("\nno customer visibility patch applied")
    print("\n  customer visibility patch present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
