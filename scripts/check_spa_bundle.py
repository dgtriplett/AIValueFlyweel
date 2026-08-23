#!/usr/bin/env python3
"""Verify the committed SPA bundle carries the behaviour the source promises.

    python3 scripts/check_spa_bundle.py [--verbose]

WHAT REPLACED WHAT, AND WHY
---------------------------
`frontend/src/` used to be absent, so the only way to change the SPA was to
regex-patch the minified bundle. Four scripts did that (`patch_spa_nav.py`,
`patch_spa_grouped_nav.py`, `patch_spa_proposal_button.py`,
`patch_spa_customer_visibility.py`) plus `rebrand_bundle.py`, and CI gated three of
them by running `--check` and asserting the patch was still applied.

The source now exists and the bundle is built from it, so those gates cannot work
as written: they assert against MINIFIED IDENTIFIERS (`Dg.find(x=>x.id===`,
`proposals/${e}`) that the minifier assigns fresh on every build. Pinning CI to
them would mean pinning the repo to one exact historical bundle, which is the
opposite of building from source.

So the checks moved rather than disappeared, and split by what they can honestly
prove:

  - BEHAVIOUR the customer sees — the grouped nav, the two console links, the
    drawer's proposal action, the current product name, no customer-visible phase
    text — is still asserted against the BUILT BUNDLE, because that is what ships.
    Those assertions are here, keyed on stable content (marker attributes, hrefs,
    user-visible strings) rather than on minified variable names.

  - INTENT that only source can express — that the nav groups are derived from the
    tab array instead of restating it, that the proposal link is guarded on the id
    prop, and that all reconstructed views remain wired into the app — is asserted
    directly against `frontend/src/` here. Keeping it in this dedicated gate avoids
    coupling the source-built frontend to unrelated Python unit tests.

WHY THIS IS A SCRIPT AND NOT ONLY A TEST
----------------------------------------
Same reason as the patch scripts it replaces: the failure mode worth catching is a
bundle that was rebuilt from stale source, or not rebuilt at all, and the person
who needs to know is whoever is about to commit. `scripts/check.py` calls this, so
it runs on a laptop and in CI identically.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIST = ROOT / "frontend" / "dist"
SRC = ROOT / "frontend" / "src"


def entry_bundle() -> tuple[str, str]:
    """The one chunk index.html actually loads, and its text.

    Scoped deliberately: assets/ can hold several index-*.js files (a stale build
    leaves orphans behind), and only the one index.html references is served.
    Checking "some chunk contains X" would pass while the served bundle changed
    nothing a user sees — that was a real weakness in the patch --check gates.
    """
    index = DIST / "index.html"
    if not index.is_file():
        sys.exit(f"missing {index} — run `npm run build` in frontend/")
    match = re.search(r"/assets/(index-[\w-]+\.js)", index.read_text())
    if not match:
        sys.exit(f"{index} references no entry bundle")
    path = DIST / "assets" / match.group(1)
    if not path.is_file():
        sys.exit(f"{index} loads {match.group(1)}, which does not exist — rebuild")
    return match.group(1), path.read_text(errors="surrogateescape")


# Each entry is (needle, why it matters if it goes missing).
REQUIRED = [
    ("gaGroupedNav",
     "the header nav is not the grouped one — the eight flat tabs are back"),
    ('label:"Plan & Fund"',
     "the job-based nav groups are gone; the top level is back to naming screens "
     "instead of what the customer is trying to do"),
    ("gaScopeSwitch",
     "the portfolio/catalog scope switch is missing — the merged use-case "
     "destination can only show one of its two scopes"),
    ("Your portfolio",
     "the scope switch lost the portfolio scope's label"),
    ("/console/#kb",
     "nothing links to the knowledge base, so the feature is invisible from the "
     "app's front door"),
    ("Write a proposal",
     "the in-SPA proposal agent is not reachable from the header"),
    ("Generate use cases",
     "the in-SPA use-case generator is not reachable from the header"),
    ("Import roadmap",
     "the in-SPA roadmap import is not reachable from the header"),
    ("data-ga-menu",
     "the nav dropdowns are missing, so the grouped items cannot be reached"),
    ("gaProposalBtn",
     "the use-case drawer has no Write proposal action; the proposal agent is "
     "only reachable by typing an id into the console"),
    ("AI Value Flywheel",
     "the bundle does not carry the current product name"),
    ('label:"Value Flywheel"',
     "the Value Flywheel TAB lost its name — the rebrand must not consume the "
     "FEATURE name, only the product name"),
    ("gaCustomerVisibility",
     "the readiness/prerequisite treatment is missing"),
    ("whitespace-nowrap",
     "Awaiting prerequisites must stay on one line inside a table cell"),
    ("Required prerequisites",
     "the readiness tooltip must name the prerequisite use cases"),
]

FORBIDDEN = [
    ("Get Started & Discovery",
     "a console tab that no longer exists under that name"),
    ("Grid Atlas",
     "the superseded product name is back"),
    ('children:"Phase"',
     "a customer-visible Phase table header is back"),
    ("Derived phase:",
     "customer-visible phase derivation text is back"),
    ("Filter by phase",
     "the phase filter is back; phase is derived and not a customer concept"),
    ("All phases",
     "the phase filter is back"),
    ("/console/#proposals",
     "the old console proposal cross-link is back"),
    ("Phase updates automatically",
     "superseded drawer copy naming phase is back"),
    ("Derived from prerequisite depth",
     "customer-visible phase derivation text is back"),
]

SOURCE_REQUIRED = {
    "components/Header.tsx": [
        "const NAV_GROUPS",
        "TABS.find((candidate) => candidate.id === id)",
        "NAV_GROUPS.map((group) =>",
        "href: '/console/#kb'",
        "const TOOL_TABS: TabId[] = ['generate', 'roadmap_import', 'proposals']",
        "setTab(id)",
        'data-gaGroupedNav="1"',
    ],
    "components/UseCaseDrawer.tsx": [
        "{ucId ? (",
        'data-gaProposalBtn="1"',
        "onWriteProposal(ucId)",
    ],
    "components/Badges.tsx": [
        'data-gaCustomerVisibility="1"',
        "Required prerequisites:",
        "whitespace-nowrap",
    ],
    # Every reconstructed view stays wired into the app. `CatalogView` is mounted
    # by PortfolioView rather than here: the catalog is the same use-case list at
    # `?scope=catalog`, so it is a scope of one destination, not a destination.
    "App.tsx": [
        "<PortfolioView",
        "<RegistryView",
        "<FlywheelTab",
        "<DashboardsView",
        "<RoadmapView",
        "<JointFundingView",
        "<AssumptionsView",
        "<OnboardingView",
        # Tier 3 Phase 5 — the curation writes. Each replaced a `<ComingSoon>`
        # slot, and a regression here is a white screen on a tab the nav offers.
        "<SourceMappingView",
        "<TaxonomyView",
        "<RulesView",
        "<GenerateView",
        "<ProposalsView",
        "<RoadmapImportView",
        # Tier 3 Phase 8 — the Settings surfaces. Each replaced a `<ComingSoon>`
        # slot in the Settings nav group; a regression here is a white screen on a
        # tab the nav offers.
        "<AccountsView",
        "<AdminView",
        "<BrandingView",
    ],
    # These three WRITE, and the disciplines that make that safe are invisible in
    # the rendered output: a mutation that forgot `NO_RETRY` on a `generate`-limited
    # endpoint looks identical until a 429 turns into four. So they are asserted in
    # source, where the intent lives.
    "views/SourceMappingView.tsx": [
        "api.patchSourceAlias",
        "queryClient.invalidateQueries({ queryKey: ['source-aliases'] })",
    ],
    "views/TaxonomyView.tsx": [
        "api.classifyTaxonomy",
        "...NO_RETRY",
        "queryClient.invalidateQueries({ queryKey: ['taxonomy'] })",
    ],
    "views/RulesView.tsx": [
        "api.testRules",
        "...NO_RETRY",
        "queryKey: ['rules']",
    ],
    "views/GenerateView.tsx": [
        "api.generateUseCases",
        "api.prepareGeneratedUseCases",
        "<ConfirmCard",
        "...NO_RETRY",
    ],
    "views/ProposalsView.tsx": [
        "api.proposalContext",
        "api.generateProposal",
        "<Markdown",
        "<ConfirmCard",
        "...NO_RETRY",
    ],
    "views/RoadmapImportView.tsx": [
        "api.previewRoadmapImport",
        "api.applyRoadmapImport",
        "<FileDrop",
        "<ConfirmCard",
        "...NO_RETRY",
    ],
    "views/PortfolioView.tsx": [
        "<CatalogView",
        "<ScopeSwitch",
        "scope === 'catalog'",
    ],
    # Tier 3 Phase 8 — the Settings surfaces. The account SWITCH must CLEAR the
    # cache (not invalidate), the destructive admin ops must be ConfirmCard +
    # NO_RETRY, and the logo upload must go through the shared FileDrop. These
    # disciplines are invisible in the rendered output, so they are pinned here.
    "views/AccountsView.tsx": [
        "api.accounts",
        "queryClient.clear()",
        "...NO_RETRY",
    ],
    "views/AdminView.tsx": [
        "api.demoLoad",
        "api.demoReset",
        "api.genieProvision",
        "<ConfirmCard",
        "...NO_RETRY",
    ],
    "views/BrandingView.tsx": [
        "api.updateBranding",
        "api.uploadBrandingLogo",
        "<FileDrop",
        "...NO_RETRY",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true",
                        help="list every assertion, not only the failures")
    args = parser.parse_args()

    name, bundle = entry_bundle()
    print(f"  entry bundle: {name} ({len(bundle):,} bytes)")

    failures: list[str] = []

    for relative_path, needles in SOURCE_REQUIRED.items():
        path = SRC / relative_path
        if not path.is_file():
            failures.append(f"MISSING source file {path.relative_to(ROOT)}")
            continue
        source = path.read_text()
        for needle in needles:
            if needle not in source:
                failures.append(
                    f"MISSING {needle!r} in {path.relative_to(ROOT)} — source parity "
                    "or source-to-bundle wiring regressed"
                )
            elif args.verbose:
                print(f"    ok      source:  {relative_path}: {needle}")

    for needle, why in REQUIRED:
        if needle in bundle:
            if args.verbose:
                print(f"    ok      present: {needle}")
        else:
            failures.append(f"MISSING {needle!r} — {why}")

    for needle, why in FORBIDDEN:
        if needle not in bundle:
            if args.verbose:
                print(f"    ok      absent:  {needle}")
        else:
            failures.append(f"PRESENT {needle!r} — {why}")

    # A bundle that does not parse is a blank page for every user, and nothing
    # else in this script would notice. Checked as .mjs because it is an ES module
    # and `node --check` rejects `export` in a .js file.
    if shutil.which("node") is None:
        print("    skip    bundle syntax (node not installed)")
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         errors="surrogateescape") as handle:
            handle.write(bundle)
            temp = handle.name
        try:
            done = subprocess.run(["node", "--check", temp],
                                  capture_output=True, text=True)
            if done.returncode != 0:
                failures.append("the entry bundle does not parse: "
                                f"{done.stderr.strip()[:300]}")
            elif args.verbose:
                print("    ok      bundle parses as an ES module")
        finally:
            os.unlink(temp)

    # An orphaned second generation of chunks is how the old patch workflow drifted:
    # scripts globbed index-*.js and reported success from a chunk nobody serves.
    # `emptyOutDir` should prevent it now, so its return means something is wrong.
    orphans = [os.path.basename(p)
               for p in sorted(glob.glob(str(DIST / "assets" / "index-*.js")))
               if os.path.basename(p) != name]
    if orphans:
        failures.append(
            f"orphaned entry chunk(s) in frontend/dist/assets: {', '.join(orphans)} "
            "— index.html serves only one; delete dist and rebuild")

    if failures:
        print()
        for failure in failures:
            print(f"  FAIL  {failure}")
        print(f"\n{len(failures)} problem(s). The bundle is built from frontend/src — "
              "fix the source and re-run `npm run build` in frontend/.")
        return 1

    source_assertions = sum(len(needles) for needles in SOURCE_REQUIRED.values())
    print(f"  {source_assertions} source assertions, {len(REQUIRED)} behaviours present, "
          f"{len(FORBIDDEN)} regressions absent, bundle parses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
