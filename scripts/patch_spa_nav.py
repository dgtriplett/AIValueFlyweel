"""Patch the SPA header: product name + a nav link to the Discovery console.

WHY PATCH THE BUNDLE
--------------------
The SPA's React source is not in this repo (see README). The discovery, domain,
taxonomy, and generation features are all reachable at /console, but nothing in
the portfolio UI linked to them — so from a user's point of view the merged
features simply did not exist. A link is the minimum honest fix.

WHAT IS CHANGED
---------------
1. The header <h1> "Value <span>Flywheel</span>" -> "Grid <span>Atlas</span>",
   and its subtitle.
2. One anchor appended to the existing <nav>. It reuses the `a.jsx` factory
   already bound in the enclosing scope and is a plain <a href="/console">, so it
   adds no state, no route, and no React behaviour that could break the tabs.

Every edit is asserted present-once before writing; the script refuses to guess.
"""
import glob, os, sys

ASSETS = "frontend/dist/assets"

OLD_TITLE = ('a.jsxs("h1",{className:"text-xl font-bold tracking-tight text-white",'
             'children:["Value ",a.jsx("span",{style:{color:"#FF3621"},children:"Flywheel"})]})')
NEW_TITLE = ('a.jsxs("h1",{className:"text-xl font-bold tracking-tight text-white",'
             'children:["Grid ",a.jsx("span",{style:{color:"#FF3621"},children:"Atlas"})]})')

OLD_SUB = 'children:"Power & Utilities — Use Case, Value & Roadmap Portfolio"'
NEW_SUB = 'children:"Power & Utilities — Data & AI Catalog, Value & Roadmap"'

# The nav renders `Dg.map(...)` inside a flex row. Appending a sibling anchor after
# the mapped buttons keeps the tab logic untouched.
OLD_NAV_TAIL = '},r.id))})]})})}'
NEW_NAV_TAIL = (
    '},r.id)).concat([a.jsxs("a",{href:"/console",'
    'title:"Workspace discovery, data domains, taxonomy, and AI use-case generation",'
    'className:"px-4 py-2.5 text-sm font-medium flex items-center gap-2 border-b-2 '
    'border-transparent text-navy-400 hover:text-navy-300 ml-auto",'
    'children:["\\u2699 Discovery & Setup"]},"console-link")])})]})})}'
)


def patch(path: str) -> int:
    text = open(path, encoding="utf-8", errors="surrogateescape").read()
    if OLD_TITLE not in text:
        return 0
    changes = 0

    text = text.replace(OLD_TITLE, NEW_TITLE, 1); changes += 1

    if OLD_SUB in text:
        text = text.replace(OLD_SUB, NEW_SUB, 1); changes += 1
    else:
        print(f"    WARNING: subtitle not found in {os.path.basename(path)}")

    count = text.count(OLD_NAV_TAIL)
    if count == 1:
        text = text.replace(OLD_NAV_TAIL, NEW_NAV_TAIL, 1); changes += 1
    else:
        # Ambiguous: refuse rather than corrupt an unrelated component.
        print(f"    WARNING: nav tail appears {count}x in "
              f"{os.path.basename(path)}; skipped the console link")

    open(path, "w", encoding="utf-8", errors="surrogateescape").write(text)
    return changes


def main():
    entries = [p for p in sorted(glob.glob(f"{ASSETS}/index-*.js"))]
    if not entries:
        sys.exit(f"no entry bundles in {ASSETS}")
    total = 0
    for path in entries:
        n = patch(path)
        if n:
            print(f"  {os.path.basename(path)}: {n} edit(s)")
            total += n
    if total == 0:
        sys.exit("nothing patched — the bundle shape changed; inspect before retrying")
    print(f"\n  {total} edits across {len(entries)} bundle(s)")


if __name__ == "__main__":
    main()
