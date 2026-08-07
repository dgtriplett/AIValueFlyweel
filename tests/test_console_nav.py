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

    def test_slug_hash_is_not_collapsed(self):
        """The router rewrites the hash to the view name; that would turn
        #kb/<slug> back into #kb and lose the address."""
        self.assertIn('name === "kb" && current.startsWith("kb/")', JS)

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


if __name__ == "__main__":
    unittest.main()
