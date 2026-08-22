"""The operator console's navigation.

The console is hand-written JS served with no build step, so there is no bundler or
type checker between a mistake and a customer seeing a blank tab. jsdom cannot
render this page (React 18's bundle fails there too), so behaviour is verified in a
real browser; what is pinned HERE is the structure that a browser check cannot
catch cheaply — every menu item routes somewhere real, nothing is orphaned, and the
CSS invariants that broke during this redesign stay fixed.

TWO REAL BUGS THIS FILE GUARDS
1. `nav.tabs { overflow-x: auto }` was needed when the row held twelve scrolling
   tabs, but it establishes a clipping box — the dropdowns rendered at full size and
   were invisible, cut off at the row's 43px height.
2. The header's `z-index: 30` lost to positioned content in <main>, so page text
   painted over an open menu. That looked exactly like a translucent panel and cost
   a long detour hunting an opacity bug that did not exist.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

ROOT = Path(__file__).parent.parent
CONSOLE = ROOT / "frontend" / "console"
JS = (CONSOLE / "console.js").read_text()
HTML = (CONSOLE / "index.html").read_text()


def nav_items() -> list[str]:
    """Every view id reachable from the menus, parsed from NAV_GROUPS/NAV_ADMIN."""
    block = JS[JS.index("const NAV_GROUPS"):JS.index("/** Which group")]
    return re.findall(r'\["([a-z_]+)",\s*"', block)


def routed_views() -> set[str]:
    """Keys of the VIEWS router map."""
    block = JS[JS.index("const VIEWS = {"):JS.index("// Navigation model")]
    return set(re.findall(r"^\s{4}([a-z_]+):", block, re.M))


class TestEveryMenuItemRoutes(unittest.TestCase):
    """A menu item with no route silently falls through to the default view.

    That is the worst kind of nav bug: the click appears to work, the page changes,
    and the user concludes the feature is broken rather than the link.
    """

    def test_every_item_has_a_route(self):
        missing = sorted(set(nav_items()) - routed_views())
        self.assertEqual(missing, [],
                         f"menu items with no entry in VIEWS: {missing}")

    def test_there_are_menu_items(self):
        self.assertGreaterEqual(len(nav_items()), 10,
                                "parsed too few items — has NAV_GROUPS moved?")

    def test_no_duplicate_items_across_groups(self):
        items = nav_items()
        duplicates = {item for item in items if items.count(item) > 1}
        self.assertEqual(duplicates, set(),
                         f"the same view appears in more than one menu, so the "
                         f"active-group underline would be ambiguous: {duplicates}")

    def test_items_are_labels_only(self):
        """No per-item description text.

        The first version put an explanatory sentence under every item. Inside a
        dropdown those wrapped and clipped, making the menu harder to scan than the
        overloaded tab row it replaced. Context now lives on the GROUP, where the
        panel heading and the trigger tooltip have room for it.
        """
        block = JS[JS.index("const NAV_GROUPS"):JS.index("/** Which group")]
        # A third string in an item tuple would be a per-item hint coming back.
        three_part = re.findall(r'\["[a-z_]+",\s*"[^"]+",\s*\n?\s*"[^"]+"\]', block)
        self.assertEqual(three_part, [],
                         "menu items carry description text again; it clips inside "
                         f"the panel: {three_part}")
        self.assertNotIn("item-hint", JS,
                         "the per-item hint element is back")

    def test_every_group_explains_itself(self):
        """Context moved to the group, so each group must still have a hint."""
        hints = re.findall(r'^\s+hint: "([^"]+)"', JS, re.M)
        self.assertGreaterEqual(len(hints), 3, "a group is missing its hint")
        for hint in hints:
            self.assertGreater(len(hint), 15, f"unhelpful group hint: {hint!r}")

    def test_labels_never_wrap(self):
        """A wrapped label in a menu is the bug that prompted this change."""
        match = re.search(r"\.navmenu button\s*\{([^}]*)\}", HTML)
        self.assertIsNotNone(match, ".navmenu button rule not found")
        self.assertIn("white-space: nowrap", match.group(1),
                      "menu labels must not wrap")

    def test_no_view_is_unreachable(self):
        """Every top-level view should be in a menu.

        Sub-tab deep links and back-compat aliases are exempt: they exist so an old
        bookmark still works, not as navigation.
        """
        back_compat = {
            "aliases", "domains", "setup", "discovery",   # renamed or merged
            "mapping", "needs", "taxonomy", "glossary",   # catalog sub-tabs
            "artifacts", "rules", "branding",
            "catalog",   # reached via its sub-tabs, never as a bare tab
        }
        orphaned = sorted(routed_views() - set(nav_items()) - back_compat)
        self.assertEqual(orphaned, [],
                         f"these views exist but no menu links to them: {orphaned}")


class TestNavStructure(unittest.TestCase):
    def test_exactly_one_portfolio_link(self):
        """It used to render four links to the SPA, all href="/".

        The SPA keeps tab state in React with no URL routing, so Portfolio / Data
        Assets / Dashboards / Roadmap all landed on the default view. Four tabs that
        go to the same place is worse than one honest link.
        """
        # Scoped to renderNav(): the "Then what" list in viewStart legitimately
        # links to the portfolio in body copy, which is not navigation chrome.
        nav_js = JS[JS.index("function renderNav()"):JS.index("function closeMenus()")]
        self.assertEqual(nav_js.count('<a href="/"'), 1,
                         "the nav should hold exactly one link back to the SPA")
        for dead in ("Data Assets", "Dashboards</a>", "Roadmap</a>"):
            self.assertNotIn(dead, HTML,
                             f"{dead!r} is back as a top-level link that cannot "
                             "deep-link")

    def test_html_does_not_hardcode_the_menu(self):
        """One source of truth: index.html ships an empty <nav>, console.js fills it.

        Duplicating the items in markup is how the two drift apart.
        """
        nav_markup = HTML[HTML.index('<nav class="tabs"'):]
        nav_markup = nav_markup[:nav_markup.index("</nav>") + 6]
        self.assertNotIn("data-view", nav_markup,
                         "index.html hardcodes menu items; they belong in "
                         "NAV_GROUPS so there is one source of truth")

    def test_groups_are_workflow_stages(self):
        for group in ("Discover", "Analyze", "Build"):
            self.assertIn(f'label: "{group}"', JS, f"{group} group is missing")

    def test_top_level_item_count_is_small(self):
        """The point of the redesign. Twelve buttons scrolled and nothing was
        findable by scanning."""
        group_count = len(re.findall(r'^\s{6}label: "', JS, re.M))
        # 3 groups + Portfolio link + Settings = 5 top-level items.
        self.assertLessEqual(group_count, 4,
                             f"{group_count} groups is heading back toward the "
                             "overloaded row this replaced")


class TestAccessibility(unittest.TestCase):
    """Keyboard and screen-reader affordances, since this is a customer product."""

    def test_menus_declare_their_roles(self):
        self.assertIn('role="menu"', JS)
        self.assertIn('role="menuitem"', JS)

    def test_triggers_declare_expanded_state(self):
        self.assertIn('aria-expanded="false"', JS)
        self.assertIn('setAttribute("aria-expanded", "true")', JS)

    def test_triggers_declare_haspopup(self):
        self.assertIn('aria-haspopup="menu"', JS)

    def test_escape_closes_a_menu(self):
        self.assertIn('event.key === "Escape"', JS)

    def test_labels_are_escaped(self):
        """Menu labels go through text() like everything else on this page."""
        self.assertIn("text(label)", JS)
        self.assertIn("text(group.hint)", JS)


class TestGetStartedDiscovery(unittest.TestCase):
    def test_bootstrap_is_available_when_discovery_tables_are_missing(self):
        """A first run often has ATLAS_CATALOG set but no discovery tables yet.

        /api/ingestion/summary reports that as configured-but-unavailable; the
        UI must still offer the bootstrap button, otherwise the user cannot get
        from that state to the upload controls.
        """
        self.assertIn("const discoveryConfigured = !!inventory.configured", JS)
        self.assertIn("${discoveryConfigured ? `", JS)
        self.assertIn("Discovery storage is configured but not ready", JS)
        self.assertLess(
            JS.index("${discoveryConfigured ? `"),
            JS.index('data-act="bootstrap"'),
            "the bootstrap button is no longer inside the configured-discovery branch")

    def test_bootstrap_refreshes_the_get_started_state(self):
        """After creating the UC tables the upload controls should appear."""
        block = JS[JS.index("bootstrap: async () => {"):]
        block = block[:block.index('"up-schemas"')]
        self.assertIn('api("/ingestion/bootstrap"', block)
        self.assertIn("await viewStart()", block)


class TestEnhancementScreens(unittest.TestCase):
    def test_research_page_exposes_customer_enhancement_agent(self):
        self.assertIn("/agents/customer-enhancements", JS)
        self.assertIn("Customer enhancement agent", JS)
        self.assertIn("10 app enhancements", JS)

    def test_executive_export_page_is_reachable_and_downloads_pack(self):
        self.assertIn("executive: viewExecutive", JS)
        self.assertIn('["executive", "Executive export"]', JS)
        self.assertIn("/exports/executive-pack", JS)
        self.assertIn("/exports/executive-pack.md", JS)
        self.assertIn("downloadApi", JS)

    def test_maturity_roadmap_import_page_is_reachable(self):
        self.assertIn("roadmap_import: viewRoadmapImport", JS)
        self.assertIn('["roadmap_import", "Import roadmap"]', JS)
        self.assertIn("/sync/maturity-roadmap/preview", JS)
        self.assertIn("/sync/maturity-roadmap/apply", JS)
        self.assertIn("value-flywheel-roadmap?assessmentId", JS)


class TestClickOutsideIsOrderIndependent(unittest.TestCase):
    """The dismiss handler must not depend on listener registration order.

    The first version called stopPropagation() in the trigger and bound
    closeMenus to document. In the browser the menu opened and closed within the
    same click, so nothing appeared to happen. Scoping by target instead is
    independent of which listener runs first.
    """

    def test_dismiss_is_scoped_by_target(self):
        self.assertIn('event.target.closest(".navgroup")', JS,
                      "click-outside must test the target, not rely on "
                      "stopPropagation ordering")

    def test_trigger_does_not_rely_on_stoppropagation(self):
        trigger_block = JS[JS.index('trigger.addEventListener("click"'):]
        trigger_block = trigger_block[:400]
        self.assertNotIn("stopPropagation", trigger_block,
                         "the open/close race this caused was invisible in the UI")

    def test_toggle_reads_state_before_closing(self):
        """Otherwise a second click on the same trigger reopens instead of closing."""
        self.assertIn("wasOpen", JS)


class TestCssInvariants(unittest.TestCase):
    """The two CSS properties whose regression made the menus unusable."""

    def test_nav_does_not_clip_its_dropdowns(self):
        """`overflow-x: auto` establishes a clipping box; the menus vanished."""
        match = re.search(r"nav\.tabs\s*\{([^}]*)\}", HTML)
        self.assertIsNotNone(match, "nav.tabs rule not found")
        rule = match.group(1)
        self.assertNotIn("overflow-x: auto", rule,
                         "overflow-x:auto clips the absolutely positioned "
                         "dropdowns — they render at full size and are invisible")
        self.assertIn("overflow: visible", rule)

    def test_header_stacks_above_page_content(self):
        """z-index only orders siblings within a stacking context.

        At 30 the header lost to positioned content in <main>, so page text painted
        over an open menu — indistinguishable from a translucent panel.
        """
        match = re.search(r"\n      header\s*\{([^}]*)\}", HTML)
        self.assertIsNotNone(match, "header rule not found")
        z_match = re.search(r"z-index:\s*(\d+)", match.group(1))
        self.assertIsNotNone(z_match, "header has no z-index")
        self.assertGreaterEqual(
            int(z_match.group(1)), 100,
            "the header must out-stack anything in <main>, or open menus get "
            "painted over by page content")

    def test_menu_z_index_beats_the_page(self):
        match = re.search(r"\.navmenu\s*\{([^}]*)\}", HTML)
        self.assertIn("z-index", match.group(1))

    def test_menu_width_fits_the_longest_label(self):
        """Wide enough that no label needs to wrap, narrow enough to scan.

        Replaces an earlier assertion about stacking a label above its hint — the
        hints are gone, since inside a dropdown they wrapped and clipped.
        """
        match = re.search(r"\.navmenu\s*\{([^}]*)\}", HTML)
        self.assertRegex(match.group(1), r"min-width:\s*\d+px",
                         "the panel needs a min-width so labels never wrap")

    def test_mobile_breakpoint_keeps_menus_on_screen(self):
        self.assertIn("@media (max-width: 720px)", HTML)
        mobile = HTML[HTML.index("@media (max-width: 720px)"):]
        self.assertIn("position: fixed", mobile[:400],
                      "a 320px panel anchored near the right edge hangs off a "
                      "phone screen")


class TestKnowledgeBaseView(unittest.TestCase):
    """The KB view, including the markdown renderer's security property."""

    def test_kb_and_proposals_are_routed(self):
        for view in ("kb", "proposals"):
            self.assertIn(f"{view}:", JS, f"{view} has no VIEWS entry")

    def test_article_slug_is_addressable(self):
        """An article is the thing people paste into Slack, so #kb/<slug> must
        survive a cold load — verified in a browser against the live app."""
        self.assertIn('name.startsWith("kb/")', JS)

    def test_parameterized_hash_is_not_collapsed(self):
        """The router rewrites the hash to the view name, which would turn
        #kb/<slug> back into #kb and lose the address.

        Generalized from a kb-only special case after the same bug hit
        #proposals/<id>: the SPA drawer's "Write proposal" link arrived with an
        empty form because the id was rewritten away before the handler read it.
        Any `view/parameter` hash must survive.
        """
        self.assertIn('current.startsWith(name + "/")', JS)
        self.assertIn("parameterized", JS)

    def test_proposals_accepts_a_use_case_id(self):
        """#proposals/<id> pre-fills the form and runs the free context check.

        Without this, the SPA drawer could only link to a form that asks which use
        case — which is the step the drawer button exists to remove.
        """
        self.assertIn('name.startsWith("proposals/")', JS)
        self.assertIn("proposalPrefill", JS)

    def test_markdown_escapes_before_parsing(self):
        """THE security property of the renderer.

        Article bodies come from users and from the model. Escaping first and only
        then re-introducing a fixed set of constructs means no input can inject
        markup — by the time any pattern runs, every < > & " ' is an entity. If a
        future edit ever moves a replace() ahead of the text() call, this breaks.
        """
        body = JS[JS.index("function renderMarkdown"):]
        body = body[:body.index("\n  }")]
        escape_at = body.index("text(source)")
        first_replace = body.index(".replace(")
        self.assertLess(escape_at, first_replace,
                        "renderMarkdown must escape the source BEFORE any pattern "
                        "runs, or article content can inject HTML")

    def test_wiki_link_slug_is_restricted_before_reaching_an_attribute(self):
        body = JS[JS.index("function renderMarkdown"):]
        body = body[:body.index("\n  }")]
        self.assertIn('replace(/[^\\w\\s-]/g, "")', body,
                      "the wiki slug goes into an href and a data attribute, so it "
                      "must be stripped to a safe character set first")

    def test_dead_wiki_links_are_marked(self):
        """Found in the browser on the live app: an unresolved [[link]] rendered
        identically to a live one, so clicking it led to an error page. Only the
        server knows which slugs resolve, so the marking happens after render."""
        self.assertIn("reference.exists", JS)
        self.assertIn("replaceWith(span)", JS)

    def test_uploads_do_not_force_a_json_content_type(self):
        """api() special-cases FormData; sending JSON headers breaks multipart."""
        self.assertIn("options.body instanceof FormData", JS)
    def test_javascript_parses(self):
        """A syntax error ships and renders a blank page — there is no build step.

        scripts/check.py runs `node --check` as a gate; this asserts the gate's
        target still exists so the check cannot silently become a no-op.
        """
        self.assertTrue((CONSOLE / "console.js").is_file())
        self.assertGreater(len(JS), 10_000)

    def test_nav_container_exists_for_js_to_fill(self):
        self.assertIn('<nav class="tabs" id="nav"', HTML)
        self.assertIn('$("#nav")', JS)


class TestSpaSourceIsTheSourceOfTruth(unittest.TestCase):
    """The SPA's behaviour must be expressed in frontend/src, not in a patch script.

    HISTORY THIS CLOSES: frontend/src did not exist, so the only way to change the
    SPA was to regex-patch the minified bundle. That workflow is gone — the source
    is committed and dist is built from it — but the guarantees the patches bought
    still matter, so they moved rather than disappeared.

    Asserted HERE (against source, where it is a real structural claim):
      - the nav groups are DERIVED from the tab array rather than restating it
      - the drawer's proposal link reads the id prop and is guarded on it
      - phase stays out of the customer-facing views

    Asserted against the BUILT BUNDLE by TestSpaBundleShipsTheBehaviour below,
    because the bundle is what a customer actually loads.

    What is deliberately NOT asserted anywhere any more: the exact minified spellings
    (`Dg.find(x=>x.id===`, `proposals/${e}`, `e?a.jsx`). Those were substrings of one
    historical bundle; a minifier assigns those names fresh on every build, so
    pinning them would pin the repo to a bundle nobody can reproduce.
    """

    @classmethod
    def setUpClass(cls):
        src = ROOT / "frontend" / "src"
        if not src.is_dir():
            raise unittest.SkipTest("frontend/src is missing")
        cls.header = (src / "components" / "Header.tsx").read_text()
        cls.drawer = (src / "components" / "UseCaseDrawer.tsx").read_text()
        cls.views = {path.name: path.read_text()
                     for path in sorted((src / "views").glob("*.tsx"))}
        cls.components = {path.name: path.read_text()
                          for path in sorted((src / "components").glob("*.tsx"))}

    # Helpers, so the three structural tests below agree on what they are reading
    # rather than each re-deriving it from a slightly different regex.

    def declared_tab_ids(self) -> set:
        return set(re.findall(r"id: '([a-zA-Z_][a-zA-Z0-9_]*)'", self.header))

    def nav_group_ids(self) -> dict:
        """{group label: {tab ids in it}}, parsed from the `ids:` arrays only.

        Reading the ids arrays rather than every quoted string in the block means
        a group LABEL can never be mistaken for a tab id. The previous version
        relied on labels happening to be capitalised — it carried a skip list for
        the then-current labels ("Analyze", "Plan") that the lowercase-only regex
        could never have matched anyway, so a lowercase label would have been
        silently asserted as a tab id.
        """
        block = re.search(r"const NAV_GROUPS[^=]*=\s*\[(.*?)\n\]", self.header, re.S)
        assert block, "could not parse NAV_GROUPS"
        groups = {}
        for label, ids in re.findall(r"label: '([^']+)',\s*ids: \[([^\]]*)\]",
                                     block.group(1)):
            groups[label] = set(re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", ids))
        assert groups, "NAV_GROUPS parsed as empty — the format changed"
        return groups

    def test_the_tab_list_is_declared_once(self):
        """One TABS array is the single source of what the nav can reach.

        `catalog` is deliberately absent: it is the same use-case list at
        `?scope=catalog`, so it is a scope switch inside the portfolio
        destination, not a tab. See components/ScopeSwitch.tsx.
        """
        self.assertIn("export const TABS", self.header,
                      "the tab list must be exported data, not markup")
        for tab_id in ("portfolio", "registry", "flywheel", "dashboards",
                       "roadmap", "funding", "value", "coverage", "whatif",
                       "trend", "glossary", "artifacts", "executive",
                       "knowledge", "sourcemapping", "taxonomy", "rules",
                       "research", "accounts", "admin", "branding",
                       "onboarding"):
            self.assertIn(f"id: '{tab_id}'", self.header,
                          f"the {tab_id} tab is not in TABS")
        self.assertNotIn("id: 'catalog'", self.header,
                         "the catalog is a scope of the portfolio destination, "
                         "not a top-level tab — re-adding it restores the "
                         "duplication the merge removed")

    def test_groups_resolve_tabs_by_id_instead_of_restating_them(self):
        """The replacement for the old `Dg.find(x=>x.id===` assertion.

        Groups hold IDS and look the tab up, so a renamed label or a reordered
        TABS still resolves and the group definition cannot drift out of sync with
        what the tab actually is.
        """
        self.assertIn("TABS.find(", self.header,
                      "nav groups must look tabs up in TABS, not restate them")
        self.assertRegex(self.header, r"NAV_GROUPS[^=]*=\s*\[",
                         "the groups must be declared as data")
        declared = self.declared_tab_ids()
        for label, ids in self.nav_group_ids().items():
            self.assertNotEqual(ids, set(), f"nav group {label!r} holds no tabs")
            for member in ids:
                self.assertIn(member, declared,
                              f"nav group {label!r} references {member!r}, "
                              "which is not a tab id")

    def test_the_groups_are_named_for_the_job_not_the_screen(self):
        """The top level asks what you are DOING, and stays short.

        A flat row of eight destinations — two of which were one list at two
        scopes — made the app read as eight unrelated screens. The value of the
        grouping is that the top-level choice is small and job-shaped, so both
        properties are asserted rather than left to drift back.
        """
        groups = self.nav_group_ids()
        expected_groups = {
            "Portfolio": {
                "portfolio", "flywheel", "registry", "dashboards", "coverage",
                "whatif", "trend",
            },
            "Plan & Fund": {"roadmap", "funding", "executive"},
            "Value": {"value", "research"},
            "Knowledge": {
                "knowledge", "glossary", "taxonomy", "sourcemapping", "rules",
                "artifacts",
            },
            "Settings": {"accounts", "admin", "branding"},
        }
        self.assertEqual(groups, expected_groups,
                         "the grouped nav no longer matches the Tier 3 shell")

    def test_every_grouped_tab_is_reachable(self):
        """A tab in TABS but in no group, and not top-level, is dead code.

        This is the test that would have caught `onboarding`: it was in TabId and
        rendered by App.tsx but in neither TABS nor a group, so OnboardingView
        could not be reached from the UI at all. It is now a tab, exempt below
        because it renders as its own top-level entry-point button.
        """
        grouped = set().union(*self.nav_group_ids().values())
        # The entry-point surface is a standalone top-level button, not in a menu.
        entry = re.search(
            r"const ENTRY_TAB: TabId = '([a-zA-Z_][a-zA-Z0-9_]*)'", self.header
        )
        self.assertIsNotNone(entry, "no ENTRY_TAB declared")
        unreachable = self.declared_tab_ids() - grouped - {entry.group(1)}
        self.assertEqual(unreachable, set(),
                         f"tabs unreachable from the nav: {sorted(unreachable)}")

    def test_every_tab_id_is_a_tab(self):
        """The converse: a TabId the nav never offers is a view nobody can open.

        `onboarding` was exactly this — a valid TabId, rendered by App.tsx, in no
        TABS entry and no group.
        """
        union = re.search(r"export type TabId =(.*?)\n\n", self.header, re.S)
        self.assertIsNotNone(union, "could not parse the TabId union")
        declared = self.declared_tab_ids()
        for member in re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", union.group(1)):
            self.assertIn(member, declared,
                          f"TabId {member!r} has no entry in TABS, so no nav "
                          "item can reach the view it names")

    def test_the_catalog_is_a_scope_of_the_portfolio_not_a_second_view(self):
        """Portfolio and Catalog read the SAME endpoint, differing only by `?scope=`.

        They were two top-level tabs, which made a data filter look like a
        destination and split "what should we build" across two places. The merge
        is only real if the catalog is still REACHABLE from the merged view — a
        collapse that drops a scope is deletion, not consolidation.
        """
        portfolio = self.views["PortfolioView.tsx"]
        self.assertIn("<ScopeSwitch", portfolio,
                      "the merged use-case view offers no scope switch")
        self.assertIn("<CatalogView", portfolio,
                      "the catalog scope is not rendered, so the catalog became "
                      "unreachable rather than merged")
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
        self.assertNotIn("tab === 'catalog'", app,
                         "the catalog is still switched on as its own tab")
        # Both scopes filter through the shared FilterContext, which is the reason
        # a narrowing survives the flip between them.
        self.assertIn("matchesUseCase", self.views["CatalogView.tsx"])
        self.assertIn("matchesUseCase", portfolio)

    def test_the_console_links_are_present(self):
        for href in ("/console/#kb", "/console/#proposals", "/console/"):
            self.assertIn(href, self.header,
                          f"the SPA header does not link to {href}")

    def test_the_menus_are_accessible(self):
        """A dropdown a keyboard cannot close is a trap."""
        self.assertIn("aria-haspopup", self.header)
        self.assertIn("aria-expanded", self.header)
        self.assertIn("role=\"menu\"", self.header)
        self.assertIn("Escape", self.header,
                      "Escape must dismiss an open menu")

    def test_selecting_any_view_dismisses_an_open_menu(self):
        """Changing the view must never leave a menu hanging open over it.

        The click-outside handler is scoped to the nav element, so it deliberately
        does NOT fire for the nav's own DIRECT buttons — the single-item group and
        the entry-point tab. Those call sites have to dismiss explicitly, and two
        of them originally did not: opening a menu and then clicking 'Value' or
        'Get started' switched the view with the old menu still floating over it.

        Asserted as an invariant over EVERY setTab call rather than against those
        two sites, so the next direct button added to the nav cannot reintroduce
        it — which is exactly how these two arrived.
        """
        setters = [match.start() for match in re.finditer(r"\bsetTab\(", self.header)]
        # The prop declaration and the destructured param are not call sites.
        calls = [at for at in setters
                 if not re.match(r"setTab\(\s*(id|next)?\s*:",
                                 self.header[at:at + 40])]
        self.assertGreaterEqual(len(calls), 3,
                                "expected the grouped item plus the two direct "
                                "buttons to select a view")
        for at in calls:
            preceding = self.header[max(0, at - 120):at]
            self.assertIn("setOpenMenu(null)", preceding,
                          "a setTab call does not first dismiss the open menu: "
                          f"...{self.header[max(0, at - 90):at + 30]!r}")

    def test_the_drawer_proposal_link_uses_the_id_prop(self):
        """It must read the ID, not the edit-form draft.

        The original patch bound this to the edit draft, which is only populated
        in edit mode — so the link never rendered on a normal open. It deployed
        looking like the patch had failed. In source the binding is explicit.
        """
        self.assertIn("data-gaProposalBtn", self.drawer)
        self.assertIn("/console/#proposals/${ucId}", self.drawer,
                      "the drawer link must interpolate the ucId prop")
        self.assertNotIn("proposals/${draft", self.drawer,
                         "the draft is only populated in edit mode")

    def test_the_drawer_proposal_link_is_guarded_on_the_id(self):
        """The drawer renders before its data arrives; an unguarded link would
        read #proposals/undefined and open the console pointed at nothing."""
        index = self.drawer.index("data-gaProposalBtn")
        self.assertIn("ucId ?", self.drawer[max(0, index - 200):index],
                      "the proposal link is not guarded on the id being present")

    def test_the_readiness_badge_explains_itself(self):
        badges = self.components["Badges.tsx"]
        self.assertIn("data-gaCustomerVisibility", badges)
        self.assertIn("whitespace-nowrap", badges,
                      "Awaiting prerequisites must stay on one line in tables")
        self.assertIn("Required prerequisites", badges,
                      "the tooltip must name the prerequisite use cases")
        self.assertIn("details", badges,
                      "users need an obvious affordance to inspect prerequisites")

    def test_phase_is_not_customer_visible_in_the_source(self):
        """Phase is derived from prerequisite depth. Shown to a customer it reads
        as a delivery commitment the derivation cannot support, so it is absent
        from the filters, the portfolio table and the drawer.

        The dashboards readiness heatmap still groups by it internally — that is
        an analysis surface, not a promise — so PhaseBadge stays defined.
        """
        customer_facing = {
            **{f"views/{name}": text for name, text in self.views.items()
               if name != "DashboardsView.tsx"},
            **{f"components/{name}": text for name, text in self.components.items()
               if name != "Badges.tsx"},
        }
        for where, text in customer_facing.items():
            for stale in ("Filter by phase", "All phases", "Derived phase:",
                          "Phase updates automatically",
                          "Derived from prerequisite depth"):
                self.assertNotIn(stale, text,
                                 f"customer-visible phase text in {where}: {stale}")

    def test_the_product_name_is_current_in_the_source(self):
        """The wordmark accents its second half, so in JSX the name is split
        across an element (`AI Value <span>Flywheel</span>`) rather than being one
        contiguous literal. Both halves and the accent are asserted here; the
        assembled string is asserted against the built bundle."""
        self.assertRegex(
            self.header,
            r"AI Value\s*<span[^>]*#FF3621[^>]*>\s*Flywheel\s*</span>",
            "the header wordmark is not the current product name")
        # The footer carries the name as a single literal, so it can be exact.
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
        self.assertIn("AI Value Flywheel · Powered by Databricks", app,
                      "the footer does not carry the current product name")
        combined = self.header + app + "".join(self.views.values()) \
            + "".join(self.components.values())
        self.assertNotIn("Grid Atlas", combined,
                         "the superseded product name is back in the source")

    def test_the_feature_name_survives_the_rebrand(self):
        """"Value Flywheel" is also a real FEATURE — the tab and the blast radius.
        A blanket rename would lose the distinction between the product and one
        of its views."""
        self.assertIn("label: 'Value Flywheel'", self.header,
                      "the Value Flywheel TAB lost its name")


class TestSpaBundleShipsTheBehaviour(unittest.TestCase):
    """The committed bundle must carry what the source promises.

    frontend/dist is committed and is what the app serves, so source alone is not
    enough: a stale dist ships old behaviour with a clean source tree. The detailed
    assertions live in scripts/check_spa_bundle.py so that the same check runs from
    a laptop before committing; this delegates to it rather than restating the list
    in two places that can disagree.
    """

    @classmethod
    def setUpClass(cls):
        html_path = ROOT / "frontend" / "dist" / "index.html"
        if not html_path.is_file():
            raise unittest.SkipTest("frontend/dist is not built")
        match = re.search(r"/assets/(index-[\w-]+\.js)", html_path.read_text())
        assert match, "index.html references no entry bundle"
        cls.bundle_name = match.group(1)
        cls.bundle_path = ROOT / "frontend" / "dist" / "assets" / cls.bundle_name

    def test_the_served_bundle_carries_the_behaviour(self):
        """Delegates to the gate CI runs, so the two can never disagree."""
        import subprocess
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_spa_bundle.py")],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0,
                         "the committed bundle does not match the source's "
                         f"behaviour:\n{done.stdout}{done.stderr}")

    def test_index_html_loads_a_bundle_that_exists(self):
        self.assertTrue(self.bundle_path.is_file(),
                        f"index.html loads {self.bundle_name}, which is not in "
                        "frontend/dist/assets — run `npm run build` in frontend/")

    def test_the_bundle_is_reproducible_from_source(self):
        """The point of the whole exercise: dist comes from frontend/src.

        Asserts the tooling and entry point a rebuild needs are present and wired
        to each other. Actually running the build here would need node_modules,
        which CI does not install, so this checks the wiring and
        scripts/check_spa_bundle.py checks the output.
        """
        frontend = ROOT / "frontend"
        self.assertTrue((frontend / "src" / "main.tsx").is_file(),
                        "frontend/src/main.tsx is missing — dist would not be "
                        "reproducible")
        package = (frontend / "package.json").read_text()
        self.assertIn('"build": "tsc && vite build"', package,
                      "the build script changed; dist may no longer come from src")
        self.assertIn("/src/main.tsx", (frontend / "index.html").read_text(),
                      "frontend/index.html must load the real entry module")

    def test_the_shipped_title_matches_the_source_html(self):
        """dist/index.html is GENERATED from frontend/index.html.

        These drifted under the patch workflow: rebrand_bundle.py rewrote the title
        in the built copy only, so a rebuild silently reverted it. Now they must
        agree, which is what makes the rebrand script unnecessary.
        """
        source_title = re.search(
            r"<title>(.*?)</title>",
            (ROOT / "frontend" / "index.html").read_text()).group(1)
        built_title = re.search(
            r"<title>(.*?)</title>",
            (ROOT / "frontend" / "dist" / "index.html").read_text()).group(1)
        self.assertEqual(source_title, built_title,
                         "frontend/index.html and the built dist/index.html "
                         "disagree on the page title — rebuild")


class TestSyncResultIsVisible(unittest.TestCase):
    """Reported as "clicking Preview/Apply does nothing".

    Two separate causes, both real:

      1. The shared `#result` slot is at the TOP of the Get-started view, and section C
         is the last card on a ~1,500px page — so the outcome rendered far off-screen.
         Section C now has its own slot, and the result is scrolled into view.
      2. "0 changes" is the COMMON outcome (the sweep only advances a source that
         Databricks lineage shows real activity against), and the message read
         "Would change: 0 data source(s)" — indistinguishable from a broken button. The
         live endpoint was returning HTTP 200 having scanned 5,000 tables.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.console = (Path(__file__).parent.parent / "frontend" / "console"
                       / "console.js").read_text()

    def test_section_c_has_its_own_result_slot(self):
        self.assertIn('<div id="sync-result"></div>', self.console)

    def test_the_result_is_scrolled_into_view(self):
        """Both call sites are near the bottom of a long page."""
        self.assertIn("slot.scrollIntoView(", self.console)

    def test_a_zero_change_scan_says_it_succeeded(self):
        """The whole point: a successful scan that found nothing must not look like a
        no-op."""
        self.assertIn("Scanned successfully", self.console)

    def test_unreadable_system_tables_are_distinguished(self):
        """Nothing-to-do and cannot-look are different problems with different fixes."""
        self.assertIn("System tables are not readable", self.console)

    def test_preview_says_nothing_was_written(self):
        self.assertIn("Nothing has been written yet", self.console)

    def test_only_one_definition_of_the_renderer(self):
        """Get-started and Admin had two near-identical copies with the same defect.
        Fixing it twice is how they drift apart."""
        self.assertEqual(self.console.count("function renderSyncResult("), 1)
        self.assertEqual(self.console.count("renderSyncResult("), 3,
                         "expected one definition and two call sites")


if __name__ == "__main__":
    unittest.main()
