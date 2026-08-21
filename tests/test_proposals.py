"""The use-case proposal agent.

The validation tests carry the weight here. A proposal is a document a customer may
quote in a funding request or a rate filing, so the two failure modes that matter are
a proposal containing invented figures, and a proposal that LOOKS finished while
being stubbed. The first is addressed by grounding the prompt in real instance data
(tested below by asserting those values actually reach the prompt); the second by
rejecting output that is missing, thin, or full of placeholders.

There is deliberately no heuristic fallback. Every other agent in this app degrades
to a deterministic ranking, which is honest because a ranking is a judgement. A
template-filled proposal would be a document that reads as authored and says
nothing — so the route fails loudly instead.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401

from server import proposals as pr  # noqa: E402

USE_CASE = {
    "id": 42, "title": "Outage Prediction",
    "description": "Predict feeder outages from weather and asset health",
    "effort_tshirt": "L", "status": "not_started", "sub_vertical": "electric",
    "risk_tags": ["reliability", "storm response"],
}

CONTEXT = {
    "company": {"company_name": "Eversource Energy", "utility_type": "wires-only",
                "customer_count": "4.4M", "regulatory_environment": "DPU / PURA"},
    "value": {"low": 8.7, "mid": 12.4, "high": 16.1,
              "driver": "avoided outage minutes",
              "components": ["SAIDI reduction x customers x $/minute"]},
    "assumptions": ["Value of one SAIDI minute = 0.42 $M [high confidence]",
                    "Customers served = 4400000 count [uncalibrated]"],
    "domains": {"satisfied": ["Outage & Interruption Records", "Asset Health"],
                "gaps": ["AMI Interval Usage"]},
    "sources": ["OMS", "AMI Head-End", "GIS"],
    "prerequisites": [{"title": "Meter Data Foundation", "built": False},
                      {"title": "Asset Registry", "built": True}],
    "readiness": "nearly_ready", "lob": "Distribution",
}


def full_sections(**overrides) -> dict:
    """A section map that passes validation, for mutating in a specific test."""
    body = {}
    for key, heading, _guidance, minimum in pr.SECTIONS:
        body[key] = f"Substantive content about {heading}. " * (minimum // 20 + 4)
    body.update(overrides)
    return body


class TestSectionDefinitions(unittest.TestCase):
    def test_eight_sections(self):
        """The spec is eight named sections; a missing one is a missing chapter."""
        self.assertEqual(len(pr.SECTIONS), 8)

    def test_expected_sections_present(self):
        headings = [heading for _key, heading, _g, _m in pr.SECTIONS]
        for expected in ("Executive Summary", "Business Case & Value",
                         "Deliverables", "High-Level Design",
                         "Assumptions & Dependencies", "Risks & Mitigations",
                         "Timeline", "Open Items & Next Steps"):
            self.assertIn(expected, headings)

    def test_keys_are_unique(self):
        self.assertEqual(len(set(pr.SECTION_KEYS)), len(pr.SECTION_KEYS))

    def test_schema_requires_every_section(self):
        schema = pr.RESPONSE_SCHEMA["json_schema"]["schema"]
        self.assertEqual(set(schema["required"]), set(pr.SECTION_KEYS))
        self.assertFalse(schema["additionalProperties"],
                         "extra keys should be refused, not silently ignored")

    def test_token_budget_is_large_enough_for_prose(self):
        """4000 truncated the last two sections in practice."""
        self.assertGreaterEqual(pr.MAX_OUTPUT_TOKENS, 6000)


class TestPromptIsGrounded(unittest.TestCase):
    """The prompt must carry REAL instance values, not just ask for a proposal.

    This is what separates a usable document from a plausible-sounding one. If a
    value, a domain gap, or a source system fails to reach the prompt, the model
    invents a substitute and the result reads authoritative while being wrong.
    """

    def setUp(self):
        self.prompt = pr.build_prompt(USE_CASE, CONTEXT)

    def test_includes_the_company(self):
        self.assertIn("Eversource Energy", self.prompt)
        self.assertIn("wires-only", self.prompt)

    def test_includes_the_computed_value(self):
        self.assertIn("12.40", self.prompt)
        self.assertIn("8.70", self.prompt)
        self.assertIn("16.10", self.prompt)

    def test_marks_the_figures_authoritative(self):
        # Without this the model "improves" the numbers.
        self.assertIn("AUTHORITATIVE", self.prompt)
        self.assertIn("Do NOT invent", self.prompt)

    def test_includes_domain_gaps_and_satisfied(self):
        self.assertIn("AMI Interval Usage", self.prompt)
        self.assertIn("Outage & Interruption Records", self.prompt)

    def test_includes_source_systems(self):
        for source in ("OMS", "AMI Head-End", "GIS"):
            self.assertIn(source, self.prompt)

    def test_includes_prerequisite_build_state(self):
        self.assertIn("Meter Data Foundation", self.prompt)
        self.assertIn("NOT built", self.prompt)

    def test_includes_assumption_confidence(self):
        # So the business case can say which figures are soft.
        self.assertIn("high confidence", self.prompt)
        self.assertIn("uncalibrated", self.prompt)

    def test_forbids_placeholders(self):
        self.assertIn("Never write TBD", self.prompt)

    def test_asks_for_every_section_key(self):
        for key in pr.SECTION_KEYS:
            self.assertIn(f'"{key}"', self.prompt)

    def test_handles_a_use_case_with_no_value_model(self):
        """Must tell the model NOT to invent a figure, rather than omitting the topic."""
        prompt = pr.build_prompt(USE_CASE, {**CONTEXT, "value": {}})
        self.assertIn("no value model", prompt)
        self.assertIn("Do not invent a figure", prompt)

    def test_handles_an_un_researched_instance(self):
        prompt = pr.build_prompt(USE_CASE, {**CONTEXT, "company": {}})
        self.assertIn("No company research", prompt)
        # And it must say so in the document, not silently fake specifics.
        self.assertIn("Open Items", prompt)

    def test_handles_no_prerequisites(self):
        prompt = pr.build_prompt(USE_CASE, {**CONTEXT, "prerequisites": []})
        self.assertIn("Prerequisite use cases: none", prompt)


class TestValidation(unittest.TestCase):
    def test_accepts_a_complete_proposal(self):
        cleaned = pr.validate_sections(full_sections())
        self.assertEqual(set(cleaned), set(pr.SECTION_KEYS))

    def test_rejects_a_missing_section(self):
        partial = full_sections()
        del partial["business_case"]
        with self.assertRaises(pr.ProposalRejected) as caught:
            pr.validate_sections(partial)
        self.assertIn("Business Case", str(caught.exception))

    def test_rejects_an_empty_section(self):
        with self.assertRaises(pr.ProposalRejected):
            pr.validate_sections(full_sections(risks="   "))

    def test_rejects_a_thin_section(self):
        with self.assertRaises(pr.ProposalRejected) as caught:
            pr.validate_sections(full_sections(executive_summary="Short."))
        self.assertIn("too short", str(caught.exception))

    def test_rejects_placeholders(self):
        """A stubbed section is worse than a missing one: it looks written."""
        for stub in ("TODO: write this. " * 30,
                     "TBD pending review. " * 30,
                     "Starts [insert date here] and proceeds. " * 12,
                     "The <company_name> will benefit greatly. " * 12,
                     "Lorem ipsum dolor sit amet consectetur. " * 12):
            with self.assertRaises(pr.ProposalRejected,
                                   msg=f"accepted a stub: {stub[:30]}"):
                pr.validate_sections(full_sections(timeline=stub))

    def test_rejects_the_model_talking_about_itself(self):
        with self.assertRaises(pr.ProposalRejected):
            pr.validate_sections(full_sections(
                risks="As an AI I cannot assess regulatory risk. " * 12))

    def test_rejects_non_dict(self):
        for bad in ([], "text", None, 42):
            with self.assertRaises(pr.ProposalRejected):
                pr.validate_sections(bad)

    def test_rejects_a_non_string_section(self):
        with self.assertRaises(pr.ProposalRejected):
            pr.validate_sections(full_sections(deliverables=["a", "b"]))

    def test_error_names_every_problem_at_once(self):
        """One round trip should tell the user everything that was wrong."""
        broken = full_sections(executive_summary="tiny",
                               timeline="TODO " * 40)
        del broken["risks"]
        message = ""
        try:
            pr.validate_sections(broken)
        except pr.ProposalRejected as exc:
            message = str(exc)
        self.assertIn("missing", message)
        self.assertIn("too short", message)
        self.assertIn("placeholders", message)

    def test_says_nothing_was_saved(self):
        # So the user does not go looking for a half-written article.
        try:
            pr.validate_sections({})
        except pr.ProposalRejected as exc:
            self.assertIn("Nothing was saved", str(exc))


class TestHeadingStripping(unittest.TestCase):
    """The model re-emits its own heading even when told not to."""

    def test_strips_a_markdown_heading(self):
        cleaned = pr.validate_sections(full_sections(
            executive_summary="## Executive Summary\n\n"
                              + "Real body content here. " * 20))
        self.assertFalse(cleaned["executive_summary"].startswith("#"))
        self.assertTrue(cleaned["executive_summary"].startswith("Real body"))

    def test_strips_a_bold_or_plain_restatement(self):
        for variant in ("Executive Summary:\n\n", "# Executive Summary\n",
                        "### Executive Summary\n\n"):
            cleaned = pr.validate_sections(full_sections(
                executive_summary=variant + "Real body content here. " * 20))
            self.assertTrue(cleaned["executive_summary"].startswith("Real body"),
                            f"failed to strip {variant!r}")

    def test_keeps_body_headings(self):
        """A heading INSIDE the section is the author's, not a restatement."""
        body = "Real content. " * 20 + "\n\n### Phase 1\n\nMore content."
        cleaned = pr.validate_sections(full_sections(timeline=body))
        self.assertIn("### Phase 1", cleaned["timeline"])

    def test_length_is_measured_after_stripping(self):
        """A section that is only its own heading must not pass on heading length."""
        with self.assertRaises(pr.ProposalRejected):
            pr.validate_sections(full_sections(
                risks="## Risks & Mitigations\n\nShort."))


class TestAssembly(unittest.TestCase):
    def setUp(self):
        self.sections = pr.validate_sections(full_sections())
        self.markdown = pr.assemble_markdown(USE_CASE, self.sections, CONTEXT)

    def test_every_section_gets_a_heading(self):
        self.assertEqual(self.markdown.count("## "), 8)

    def test_sections_are_in_the_defined_order(self):
        positions = [self.markdown.index(f"## {heading}")
                     for _key, heading, _g, _m in pr.SECTIONS]
        self.assertEqual(positions, sorted(positions),
                         "sections must render in the order SECTIONS defines")

    def test_marks_the_document_as_generated(self):
        """A generated proposal that reads as a hand-written standard is a liability.

        Someone will quote its numbers; the provenance line is what tells them to
        check first.
        """
        self.assertIn("Generated proposal", self.markdown)
        self.assertIn("Review before sharing", self.markdown)

    def test_states_the_value_provenance(self):
        self.assertIn("come from the value engine", self.markdown)
        self.assertIn("$12.4M", self.markdown)

    def test_includes_effort_and_readiness(self):
        self.assertIn("Effort:", self.markdown)
        self.assertIn("nearly_ready", self.markdown)

    def test_omits_the_value_line_when_unquantified(self):
        markdown = pr.assemble_markdown(USE_CASE, self.sections,
                                        {**CONTEXT, "value": {}})
        self.assertNotIn("Annual value:", markdown)
        self.assertIn("Generated proposal", markdown, "provenance still required")

    def test_ends_with_a_newline(self):
        self.assertTrue(self.markdown.endswith("\n"))


class TestSummary(unittest.TestCase):
    def test_states_value_and_gaps(self):
        summary = pr.summarize(USE_CASE, CONTEXT)
        self.assertIn("Outage Prediction", summary)
        self.assertIn("$12.4M/yr", summary)
        self.assertIn("1 data gap", summary)

    def test_handles_no_value(self):
        summary = pr.summarize(USE_CASE, {**CONTEXT, "value": {}})
        self.assertIn("not yet quantified", summary)

    def test_handles_no_gaps(self):
        summary = pr.summarize(USE_CASE,
                               {**CONTEXT, "domains": {"satisfied": [], "gaps": []}})
        self.assertIn("no data gaps", summary)


class TestContextWarnings(unittest.TestCase):
    """Warnings shown before a generation is paid for."""

    def setUp(self):
        from server.routes.proposals import _context_warnings
        self.warn = _context_warnings

    def test_no_value_model_warns(self):
        text = " ".join(self.warn({**CONTEXT, "value": {}}))
        self.assertIn("no value model", text)

    def test_zero_value_warns(self):
        """FOUND IN PRODUCTION on the live instance.

        A generation use case computed to $0.0M for Eversource because company
        research had correctly set generation fleet capacity to 0 — they are
        wires-only. The data was right, and the proposal would have been a
        confident business case for something worth nothing to that customer.
        The original warning list only checked for a MISSING value, so this
        passed silently.
        """
        zero = {**CONTEXT,
                "value": {"low": 0.0, "mid": 0.0, "high": 0.0},
                "assumptions": ["Generation fleet capacity = 0 MW [high confidence]"]}
        text = " ".join(self.warn(zero))
        self.assertIn("$0M", text)
        self.assertIn("Generation fleet capacity = 0 MW", text,
                      "the warning should name which assumption zeroed it")

    def test_a_real_value_does_not_warn_about_value(self):
        text = " ".join(self.warn(CONTEXT))
        self.assertNotIn("$0M", text)
        self.assertNotIn("no value model", text)

    def test_missing_company_research_warns(self):
        text = " ".join(self.warn({**CONTEXT, "company": {}}))
        self.assertIn("No company research", text)

    def test_no_sources_warns(self):
        text = " ".join(self.warn({**CONTEXT, "sources": []}))
        self.assertIn("No source systems", text)

    def test_uncalibrated_assumptions_warn(self):
        text = " ".join(self.warn(CONTEXT))
        self.assertIn("uncalibrated", text)


class TestRouteWiring(unittest.TestCase):
    def test_intent_is_registered(self):
        from server import confirm as cf
        self.assertIn(cf.INTENT_CREATE_PROPOSAL, cf.VALID_INTENTS)

    def test_intent_has_an_executor(self):
        """An intent with no executor is a confirm card that cannot be applied."""
        from server import confirm as cf
        from server.routes import generate
        self.assertIn(cf.INTENT_CREATE_PROPOSAL, generate._EXECUTORS)

    def test_routes_are_mounted(self):
        import app
        paths = set(app.app.openapi()["paths"])
        self.assertIn("/api/proposals/use-cases/{use_case_id}", paths)
        self.assertIn("/api/proposals/use-cases/{use_case_id}/context", paths)

    def test_generation_is_rate_limited_as_research(self):
        """Eight prose sections at 8000 tokens costs like research, not like a
        short classification call."""
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route)
        self.assertIn('limiter("research")', source)

    def test_generation_goes_through_the_confirm_gate(self):
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route.propose_proposal)
        self.assertIn("issue_token", source,
                      "the model is the author here, so this write needs the gate")

    def test_there_is_no_heuristic_fallback(self):
        """A template-filled proposal would read as authored and say nothing."""
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route.propose_proposal)
        self.assertIn("502", source)
        self.assertIn("Nothing was saved", source)

    def test_regenerate_preserves_the_previous_text(self):
        """Regenerating must not destroy a hand edit made after generation."""
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route.execute_create_proposal)
        self.assertIn("kb_article_versions", source)
        self.assertIn("Superseded", source)

    def test_existing_proposal_returns_409_without_regenerate(self):
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route.propose_proposal)
        self.assertIn("409", source)
        self.assertIn("regenerate=true", source)

    def test_new_proposal_is_linked_to_its_use_case(self):
        """Otherwise it is unfindable from the portfolio side."""
        import inspect

        from server.routes import proposals as route
        source = inspect.getsource(route.execute_create_proposal)
        self.assertIn("kb_links", source)
        self.assertIn("'proposal'", source)

    def test_generated_articles_are_marked(self):
        """A reader must be able to tell generated prose from a human standard."""
        import inspect

        from server.routes import proposals as route
        self.assertIn("generated_by",
                      inspect.getsource(route.execute_create_proposal))


if __name__ == "__main__":
    unittest.main()
