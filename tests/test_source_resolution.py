"""Deterministic resolution of discovered schema names to catalog source systems.

WHY THIS MATTERS MORE THAN IT LOOKS
-----------------------------------
This is the join between "what a customer actually has in Unity Catalog" and "the
146-module source catalog the app reasons about". Everything downstream depends on
it: an unresolved schema never attributes to a data asset, so the asset never turns
curated, so the domain never turns satisfied, so the use case stays blocked and its
value stays out of the buildable total. A silent miss reads as "you don't have this
data" when the customer does.

MEASURED, NOT ASSUMED
---------------------
The cascade was measured against realistic names before this work: only 20%
resolved deterministically. The reason was structural — the canonical vocabulary is
the nineteen source-CATEGORY names ("ERP", "Data Historian"), and nobody names a
schema that. They name it `osisoft_pi`, `maximo`, `sap_isu`.

Two tables closed it: vendor/product aliases, and the 584 per-module keywords the
catalog already shipped and the cascade was ignoring entirely. It now resolves 100%
of a realistic corpus with zero false positives on an analytics-noise corpus.

BOTH CORPORA ARE THE TEST
-------------------------
A resolver that matches everything is worse than one that matches nothing: it
attributes `dim_date` and `jsmith_scratch` to real systems, and the coverage numbers
a customer sees become fiction. So the false-positive corpus below is as important
as the real one, and every guard in pu_tables._GENERIC_KEYWORDS exists because
something in it matched when it should not have.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import stubs  # noqa: E402,F401

import pu_catalog  # noqa: E402

from server import normalization as norm  # noqa: E402
from server import pu_tables  # noqa: E402

CATEGORIES = sorted({m[0] for m in pu_catalog.MODULES})
INDEX = norm.build_canonical_index(CATEGORIES)


def resolve(raw: str):
    return norm.resolve_deterministic(raw, CATEGORIES, None, INDEX)


# Names a real utility's Unity Catalog contains. Drawn from vendor products, the
# abbreviations that appear in schema names, and the naming conventions that glue
# environment suffixes on.
REAL_NAMES = [
    # ERP
    "sap_pm", "SAP_ECC_PM", "sap_isu", "sap_fico", "peoplesoft_hcm", "jde_prod",
    # EAM / APM
    "maximo", "IBM Maximo Asset Mgmt", "ellipse", "EnterpriseAssetMgmt",
    "WorkOrderHistory", "copperleaf",
    # Historians
    "osisoft_pi", "PI_AF", "aveva_historian", "wonderware", "pi_af_prod_stg",
    "ovation_hist", "ip21",
    # Distribution
    "oms_prod", "cgi_oms", "OutageManagement", "outage_management_system",
    "adms_schneider", "ge_adms", "survalent",
    # Transmission
    "scada_survalent", "ge_emsdb", "eterrahabitat", "pmu_stream", "synchrophasor",
    # Metering
    "mdm_landisgyr", "itron_mdm", "ami_headend", "meter_readings_daily", "openway",
    # Customer
    "cis_billing", "oracle_cc_b", "customer_care_billing", "kubra_payments",
    # Spatial
    "gis_esri", "arcgis_utility_network", "smallworld", "vegetation_mgmt",
    # Environmental / nuclear / market
    "cems_data", "lims_results", "dosimetry", "weather_noaa", "pjm_settlements",
    # Finance
    "general_ledger_actuals", "capital_projects",
]

# Names that must NOT resolve. Every analytics estate is full of these, and
# attributing one to a source system inflates the coverage a customer is shown.
NOISE_NAMES = [
    "raw_landing_zone_07", "tmp_extract_final_v2", "dbo_MyTable", "sandbox",
    "my_analysis", "jsmith_scratch", "bronze", "silver", "gold", "default",
    "information_schema", "test_data", "backup_20240101", "etl_staging",
    "dim_date", "fact_sales", "reporting", "finance_reporting", "hr_dashboard",
    "monthly_summary", "adhoc_queries", "poc_project", "archive_old", "temp",
    "misc", "untitled", "new_schema", "copy_of_prod", "project_alpha",
    "work_tracker", "case_studies", "my_network_notes", "scratch", "playground",
]


class TestTablesAreLoaded(unittest.TestCase):
    def test_both_tables_load(self):
        stats = pu_tables.table_stats()
        self.assertTrue(stats["vendor_table_loaded"], "vendor alias table not found")
        self.assertGreater(stats["vendor_aliases"], 150)
        self.assertGreater(stats["catalog_keywords"], 400,
                           "the catalog's per-module keywords are not being indexed")

    def test_missing_tables_degrade_rather_than_break(self):
        """A deployment without scripts/ must still normalize, just less well.

        The tables live in scripts/, which is not a package the server imports at
        module scope and is excluded from some deployment layouts.
        """
        self.assertIsNone(pu_tables.alias_lookup(set()))
        self.assertIsNone(pu_tables.keyword_lookup(set()))


class TestRealNamesResolve(unittest.TestCase):
    """The measurement that motivated this work."""

    def test_resolution_rate(self):
        unresolved = [name for name in REAL_NAMES if resolve(name)[0] is None]
        rate = 100 * (len(REAL_NAMES) - len(unresolved)) // len(REAL_NAMES)
        self.assertGreaterEqual(
            rate, 90,
            f"only {rate}% of realistic schema names resolve deterministically "
            f"(was 20% before the vendor and keyword tables). Unresolved: "
            f"{unresolved}")

    def test_every_resolution_is_a_real_category(self):
        """A resolver must never invent a category the catalog does not have."""
        for name in REAL_NAMES:
            canonical, _how, _conf = resolve(name)
            if canonical is not None:
                self.assertIn(canonical, CATEGORIES,
                              f"{name} resolved to {canonical!r}, which is not in "
                              "the catalog vocabulary")

    def test_vendor_names_specifically(self):
        """The class that failed completely before: pure product names."""
        for name, expected in [("maximo", "EAM/APM"),
                               ("osisoft_pi", "Data Historian"),
                               ("smallworld", "GIS"),
                               ("sap_isu", "CIS/Billing"),
                               ("survalent", "ADMS (Distribution)"),
                               ("dosimetry", "Radiation/Dosimetry")]:
            self.assertEqual(resolve(name)[0], expected, f"{name} misresolved")

    def test_confidence_reflects_evidence_strength(self):
        """Low confidence is what routes a match to human review."""
        self.assertEqual(resolve("GIS")[2], "high", "an exact match is high")
        self.assertEqual(resolve("maximo")[2], "medium", "a product name is medium")
        # A keyword match is the weakest deterministic signal.
        canonical, how, confidence = resolve("interval_usage_collection")
        if how == "keyword":
            self.assertEqual(confidence, "low")


class TestNoiseDoesNotResolve(unittest.TestCase):
    """A resolver that matches everything makes the coverage numbers fiction."""

    def test_no_false_positives(self):
        matched = [(name, resolve(name)[0], resolve(name)[1])
                   for name in NOISE_NAMES if resolve(name)[0] is not None]
        self.assertEqual(matched, [],
                         f"these analytics-noise names resolved to real source "
                         f"systems: {matched}")

    def test_generic_words_alone_never_resolve(self):
        """Each of these is a real catalog keyword AND ordinary English."""
        for word in ("project", "work", "outage", "usage", "reading", "network",
                     "customer", "asset", "meter", "order", "case"):
            self.assertIsNone(
                resolve(word)[0],
                f"the bare word {word!r} resolved; it appears in unrelated schema "
                "names constantly")

    def test_medallion_layers_never_resolve(self):
        for layer in ("bronze", "silver", "gold", "raw", "staging", "curated"):
            self.assertIsNone(resolve(layer)[0], layer)


class TestQualifiedPairsStillWork(unittest.TestCase):
    """Guarding a generic word must not break its unambiguous phrase.

    Adding "outage" to the generic guard silently broke
    `outage_management_system` — as clear a system name as exists. The qualified
    pairs exist so both properties hold at once.
    """

    def test_phrases_resolve_despite_guarded_words(self):
        for name, expected in [("outage_management_system", "ADMS (Distribution)"),
                               ("work_management_maximo", "EAM/APM"),
                               ("capital_projects", "ERP"),
                               ("general_ledger_actuals", "ERP"),
                               ("meter_readings_daily", "Meter/AMI/MDM"),
                               ("vegetation_mgmt", "GIS")]:
            self.assertEqual(resolve(name)[0], expected,
                             f"{name} should resolve even though its words are "
                             "individually guarded")


class TestTokenization(unittest.TestCase):
    """Three transformations, each added because a realistic name missed."""

    def test_splits_camel_case(self):
        # "OutageManagement" is one token to a punctuation splitter, and no alias
        # or keyword will ever equal it.
        self.assertIn("outage", norm._tokens("OutageManagement"))
        self.assertIn("management", norm._tokens("OutageManagement"))
        self.assertIn("asset", norm._tokens("EnterpriseAssetMgmt"))

    def test_expands_abbreviations(self):
        self.assertIn("management", norm._tokens("meter_data_mgmt"))
        self.assertIn("maintenance", norm._tokens("plant_maint"))
        self.assertIn("transformer", norm._tokens("xfmr_loading"))

    def test_strips_naming_convention_suffixes(self):
        self.assertIn("ems", norm._tokens("ge_emsdb"))
        self.assertIn("oms", norm._tokens("oms_tbl"))

    def test_singularizes(self):
        self.assertIn("project", norm._tokens("capital_projects"))
        self.assertIn("reading", norm._tokens("meter_readings"))

    def test_keeps_both_forms(self):
        """The original must survive, so a genuinely plural keyword still matches."""
        tokens = norm._tokens("capital_projects")
        self.assertIn("projects", tokens)
        self.assertIn("project", tokens)

    def test_does_not_mangle_short_tokens(self):
        # "db" must not become "" and "ss"-endings must not lose a letter.
        self.assertNotIn("", norm._tokens("db_prod"))
        self.assertIn("address", norm._tokens("address_table"))


class TestCascadeOrdering(unittest.TestCase):
    """The cheap, certain stages must win over the expensive, inferred ones."""

    def test_exact_beats_vendor(self):
        # "GIS" is both a canonical name and a vendor alias; exact must win.
        self.assertEqual(resolve("GIS")[1], "exact")

    def test_alias_memo_beats_vendor(self):
        """A human correction in the memo table is permanent and outranks a table."""
        canonical, how, _ = norm.resolve_deterministic(
            "maximo", CATEGORIES, {"maximo": "ERP"}, INDEX)
        self.assertEqual(canonical, "ERP")
        self.assertEqual(how, "exact")

    def test_vendor_beats_keyword(self):
        """A product name is stronger evidence than a word appearing."""
        _canonical, how, _ = resolve("maximo_work_orders")
        self.assertEqual(how, "vendor")

    def test_resolution_never_invents_a_category(self):
        """A table entry outside this instance's vocabulary must be ignored.

        The shipped tables know more systems than any one customer runs; asserting
        a LIMS for a customer whose catalog has none would be a fabricated gap.
        """
        tiny = ["ERP"]
        tiny_index = norm.build_canonical_index(tiny)
        canonical, _how, _conf = norm.resolve_deterministic(
            "dosimetry", tiny, None, tiny_index)
        self.assertIsNone(canonical,
                          "resolved to a category this instance does not have")


class TestIndexIsBuiltOnce(unittest.TestCase):
    def test_keyword_index_is_cached(self):
        """Rebuilding a 577-entry index per label turns a sweep quadratic.

        A large estate is tens of thousands of schemas; the ingestion endpoint
        would appear to hang.
        """
        first = pu_tables._keyword_index()
        second = pu_tables._keyword_index()
        self.assertIs(first, second, "the keyword index is being rebuilt per call")


if __name__ == "__main__":
    unittest.main()
