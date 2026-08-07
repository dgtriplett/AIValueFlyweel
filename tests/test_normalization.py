"""Source-system canonicalization.

The motivating failure is concrete: AI enrichment produces ~1,000 distinct labels
for ~50 real systems, and every rollup built on the raw label is then noise. These
tests pin the cascade's behaviour on the exact aliasing patterns that cause it —
vendor prefixes, version suffixes, parentheticals, word-order variation — plus the
non-negotiable invariant that a human mapping is never overwritten.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402,F401
from server import normalization as norm  # noqa: E402

CANONICALS = [
    "Data Historian", "ERP", "EAM/APM", "GIS", "Meter/AMI/MDM", "CIS/Billing",
    "EMS (Transmission)", "ADMS (Distribution)", "DERMS", "Market/ISO Feed",
    "Weather", "CEMS", "LIMS",
]


class TestNormalizeRaw(unittest.TestCase):
    def test_case_and_whitespace_insensitive(self):
        for variant in ("PI Historian", "  pi historian  ", "PI   HISTORIAN"):
            self.assertEqual(norm.normalize_raw(variant), "pi historian")

    def test_handles_empty_and_none(self):
        self.assertEqual(norm.normalize_raw(""), "")
        self.assertEqual(norm.normalize_raw(None), "")


class TestCanonicalKey(unittest.TestCase):
    def test_the_pi_historian_family_collapses(self):
        """The motivating case: labels sharing the discriminating words unify,
        whatever vendor prefix, casing, punctuation, or version they carry."""
        variants = [
            "PI Historian", "OSIsoft PI Historian", "pi historian",
            "PI (Historian)", "PI Historian v2023", "Historian - PI",
            "OSIsoft PI Historian (Production)", "AVEVA PI Historian 2023",
        ]
        keys = {norm.canonical_key(v) for v in variants}
        self.assertEqual(len(keys), 1, f"expected one fingerprint, got {keys}")

    def test_bare_vendor_token_is_not_unified_by_FINGERPRINT(self):
        """"AVEVA PI" shares no discriminating word with "PI Historian".

        The FUZZY stage must not unify them: it works on token overlap, and
        inferring that a bare vendor token implies a capability would be guessing.

        It DOES now resolve, one stage later, from the curated vendor table — see
        tests/test_source_resolution.py. That is not the same thing. The fingerprint
        stage guesses from word overlap; the vendor table states a fact a P&U data
        architect knows ("AVEVA PI is a historian"), carries medium confidence, and
        is correctable in the UI. This test pins the fingerprint behaviour, which is
        still what it always was.
        """
        self.assertNotEqual(norm.canonical_key("AVEVA PI"),
                            norm.canonical_key("PI Historian"))
        # The fuzzy index alone does not contain it.
        index = norm.build_canonical_index(CANONICALS)
        self.assertNotIn(norm.canonical_key("AVEVA PI"), index)

    def test_parenthetical_content_is_discriminating(self):
        """These canonicals differ ONLY inside the parens, so dropping the
        content would collapse transmission and distribution into one system."""
        self.assertNotEqual(norm.canonical_key("EMS (Transmission)"),
                            norm.canonical_key("ADMS (Distribution)"))
        ems, how, _ = norm.resolve_deterministic("EMS (Transmission)", CANONICALS)
        self.assertEqual((ems, how), ("EMS (Transmission)", "exact"))

    def test_word_order_does_not_matter(self):
        self.assertEqual(norm.canonical_key("Outage Management System"),
                         norm.canonical_key("Management Outage"))

    def test_version_suffixes_stripped(self):
        base = norm.canonical_key("Maximo")
        for variant in ("Maximo 7.6", "Maximo v7", "Maximo version 7.6.1", "Maximo r2"):
            self.assertEqual(norm.canonical_key(variant), base, variant)

    def test_distinct_systems_stay_distinct(self):
        """The failure that matters most: over-aggressive stripping merging real
        systems. These must NOT collide."""
        distinct = ["PI Historian", "Outage Management System", "Customer Information System",
                    "SCADA", "GIS", "Weather Forecast Service", "Meter Data Management"]
        keys = [norm.canonical_key(d) for d in distinct]
        self.assertEqual(len(set(keys)), len(distinct), f"collision among {keys}")

    def test_vendor_only_label_still_fingerprints(self):
        """'Maximo' is nothing but a vendor token; stripping everything would
        leave "" and collide with every other noise-only label."""
        self.assertNotEqual(norm.canonical_key("Maximo"), "")
        self.assertNotEqual(norm.canonical_key("Maximo"), norm.canonical_key("Ellipse"))

    def test_pure_noise_yields_empty(self):
        # "the system data" is all stopwords -> no discriminating key.
        self.assertEqual(norm.canonical_key("the system data"), "")

    def test_empty_input(self):
        self.assertEqual(norm.canonical_key(""), "")
        self.assertEqual(norm.canonical_key(None), "")


class TestBuildCanonicalIndex(unittest.TestCase):
    def test_first_canonical_wins_on_collision(self):
        index = norm.build_canonical_index(["Alpha System", "System Alpha"])
        self.assertEqual(set(index.values()), {"Alpha System"})

    def test_empty_keys_excluded(self):
        """A canonical that fingerprints to "" must not create a catch-all bucket."""
        index = norm.build_canonical_index(["the system", "GIS"])
        self.assertNotIn("", index)


class TestResolveDeterministic(unittest.TestCase):
    def setUp(self):
        self.index = norm.build_canonical_index(CANONICALS)

    def test_stage1_exact_match(self):
        canonical, how, conf = norm.resolve_deterministic("Data Historian", CANONICALS)
        self.assertEqual((canonical, how, conf), ("Data Historian", "exact", "high"))

    def test_stage1_is_case_insensitive(self):
        canonical, how, _ = norm.resolve_deterministic("data historian", CANONICALS)
        self.assertEqual((canonical, how), ("Data Historian", "exact"))

    def test_stage2_alias_memo_hit(self):
        alias_map = {"pi historian": "Data Historian"}
        canonical, how, _ = norm.resolve_deterministic(
            "PI Historian", CANONICALS, alias_map=alias_map)
        self.assertEqual((canonical, how), ("Data Historian", "exact"))

    def test_stage3_fuzzy_match(self):
        canonical, how, conf = norm.resolve_deterministic(
            "Historian (Data) v3", CANONICALS, canonical_index=self.index)
        self.assertEqual((canonical, how, conf), ("Data Historian", "normalized", "medium"))

    def test_unresolvable_returns_none(self):
        """Must return None rather than guessing, so it reaches the LLM stage."""
        self.assertEqual(
            norm.resolve_deterministic("Acme Widget Tracker", CANONICALS,
                                       canonical_index=self.index),
            (None, None, None))

    def test_blank_input_unresolved(self):
        self.assertEqual(norm.resolve_deterministic("   ", CANONICALS), (None, None, None))

    def test_index_built_on_demand(self):
        canonical, _, _ = norm.resolve_deterministic("Historian Data", CANONICALS)
        self.assertEqual(canonical, "Data Historian")


class TestLlmPrompt(unittest.TestCase):
    def test_prompt_is_a_closed_vocabulary(self):
        prompt = norm.build_llm_prompt(["Acme Tracker"], CANONICALS)
        self.assertIn("Acme Tracker", prompt)
        for canonical in CANONICALS:
            self.assertIn(canonical, prompt)
        self.assertIn("ONLY names from the canonical list", prompt)
        self.assertIn(norm.OTHER, prompt)

    def test_prompt_numbers_inputs_in_order(self):
        prompt = norm.build_llm_prompt(["A", "B", "C"], CANONICALS)
        self.assertIn("1. A", prompt)
        self.assertIn("3. C", prompt)


class TestParseLlmMappings(unittest.TestCase):
    def test_valid_mapping_accepted(self):
        parsed = {"mappings": [
            {"input": "Acme Tracker", "canonical": "EAM/APM", "confidence": "high"}]}
        out = norm.parse_llm_mappings(parsed, ["Acme Tracker"], CANONICALS)
        self.assertEqual(out, {"Acme Tracker": ("EAM/APM", "high")})

    def test_hallucinated_canonical_becomes_other(self):
        """A model will occasionally invent a plausible name however strict the
        prompt; it must land in review, not in the rollups."""
        parsed = {"mappings": [
            {"input": "Acme Tracker", "canonical": "Snowflake", "confidence": "high"}]}
        out = norm.parse_llm_mappings(parsed, ["Acme Tracker"], CANONICALS)
        self.assertEqual(out, {"Acme Tracker": (norm.OTHER, "low")})

    def test_explicit_other_preserved(self):
        parsed = {"mappings": [
            {"input": "Acme", "canonical": "Other", "confidence": "low"}]}
        self.assertEqual(norm.parse_llm_mappings(parsed, ["Acme"], CANONICALS),
                         {"Acme": (norm.OTHER, "low")})

    def test_unknown_input_ignored(self):
        parsed = {"mappings": [
            {"input": "Never Asked", "canonical": "GIS", "confidence": "high"}]}
        self.assertEqual(norm.parse_llm_mappings(parsed, ["Acme"], CANONICALS), {})

    def test_omitted_input_absent_from_result(self):
        """The caller needs to distinguish "model said Other" from "model didn't
        answer", so a missing label must not be defaulted here."""
        parsed = {"mappings": [
            {"input": "A", "canonical": "GIS", "confidence": "high"}]}
        out = norm.parse_llm_mappings(parsed, ["A", "B"], CANONICALS)
        self.assertIn("A", out)
        self.assertNotIn("B", out)

    def test_bad_confidence_downgraded(self):
        parsed = {"mappings": [
            {"input": "A", "canonical": "GIS", "confidence": "very sure"}]}
        self.assertEqual(norm.parse_llm_mappings(parsed, ["A"], CANONICALS)["A"][1], "low")

    def test_none_and_malformed_payloads(self):
        self.assertEqual(norm.parse_llm_mappings(None, ["A"], CANONICALS), {})
        self.assertEqual(norm.parse_llm_mappings({}, ["A"], CANONICALS), {})
        self.assertEqual(norm.parse_llm_mappings({"mappings": None}, ["A"], CANONICALS), {})
        self.assertEqual(norm.parse_llm_mappings({"mappings": ["junk"]}, ["A"], CANONICALS), {})

    def test_matching_is_case_insensitive_both_sides(self):
        parsed = {"mappings": [
            {"input": "acme tracker", "canonical": "eam/apm", "confidence": "medium"}]}
        out = norm.parse_llm_mappings(parsed, ["Acme Tracker"], CANONICALS)
        self.assertEqual(out, {"Acme Tracker": ("EAM/APM", "medium")})


if __name__ == "__main__":
    unittest.main()
