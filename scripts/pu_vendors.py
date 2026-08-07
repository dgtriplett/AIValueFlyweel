"""Vendor and product aliases for P&U source systems.

WHY THIS EXISTS
---------------
`normalization.py` resolves a discovered schema name against the canonical
vocabulary in three deterministic stages before falling back to an LLM call. Tested
against realistic Unity Catalog names, only 20% resolved deterministically — because
the vocabulary it was given is the nineteen source-CATEGORY names ("ERP", "GIS",
"Data Historian"), and nobody names a schema that.

They name it after the product. A customer's estate contains `sap_pm`, `maximo`,
`osisoft_pi`, `sap_isu`, `smallworld`, `wonderware`, `cc_and_b`. Every one of those
missed, went to the LLM stage, cost a model call, and came back needing review.

This table closes that gap deterministically. It is the knowledge a P&U data
architect has in their head: which product means which category and module.

WHAT BELONGS HERE
-----------------
Vendor names, product names, and the abbreviations that actually appear in schema
and database names. NOT generic words — those live in the per-module keyword lists
in pu_catalog.py, and putting "outage" here would mean any schema mentioning outages
resolves to one specific product.

WHY (category, module) AND NOT JUST A CATEGORY
----------------------------------------------
"maximo" is more specific than "EAM/APM": it tells you the module is Work Management,
not Condition Monitoring. Resolving to the module means an ingested schema attributes
to the right data asset, which is what makes a domain flip to satisfied. Resolving
only to the category leaves the attribution step guessing.

A `None` module means the product spans several modules and only the category is
safe to assert — `sap` alone is ERP, but which ERP module is unknowable from the name.

MAINTAINING THIS
----------------
Every entry is a claim about the world that can be wrong. Wrong ones are visible:
the source-mapping screen in the console shows what resolved to what, and a human
correction there is permanent and overrides this table. So the cost of a bad entry is
one correction, not a silent error.
"""
from __future__ import annotations

# alias -> (source_category, module or None)
#
# Keys are lowercase and matched against normalized tokens, so "SAP_ECC_PM" and
# "sap pm" both reach the "sap pm" entry. Multi-word keys match when every one of
# their words is present, which lets "sap pm" beat a bare "sap".
VENDOR_ALIASES: dict[str, tuple[str, str | None]] = {
    # --- ERP -----------------------------------------------------------------
    "sap": ("ERP", None),
    "sap ecc": ("ERP", None),
    "s4hana": ("ERP", None),
    "s4": ("ERP", None),
    "sap pm": ("ERP", "Plant Maintenance / EAM"),
    "sap mm": ("ERP", "Materials Management"),
    "sap fico": ("ERP", "Finance & Controlling"),
    "sap fi": ("ERP", "Finance & Controlling"),
    "sap co": ("ERP", "Finance & Controlling"),
    "sap aa": ("ERP", "Asset Accounting"),
    "sap ps": ("ERP", "Project System"),
    "sap hcm": ("ERP", "Human Capital Management"),
    "sap successfactors": ("ERP", "Human Capital Management"),
    "successfactors": ("ERP", "Human Capital Management"),
    "peoplesoft": ("ERP", "Human Capital Management"),
    "workday": ("ERP", "Human Capital Management"),
    "jde": ("ERP", None),
    "jd edwards": ("ERP", None),
    "oracle ebs": ("ERP", None),
    "ebs": ("ERP", None),
    "oracle fusion": ("ERP", None),
    "coupa": ("ERP", "Materials Management"),
    "ariba": ("ERP", "Materials Management"),

    # --- EAM / APM -----------------------------------------------------------
    # Maximo is the most common non-SAP EAM at utilities.
    "maximo": ("EAM/APM", "Work Management"),
    "ibm maximo": ("EAM/APM", "Work Management"),
    "mas": ("EAM/APM", "Work Management"),
    "ellipse": ("EAM/APM", "Work Management"),
    "abb ellipse": ("EAM/APM", "Work Management"),
    "hxgn eam": ("EAM/APM", "Work Management"),
    "infor eam": ("EAM/APM", "Work Management"),
    "avantis": ("EAM/APM", "Work Management"),
    "cascade": ("EAM/APM", "Asset Register"),
    "copperleaf": ("EAM/APM", "Asset Health / APM"),
    "c55": ("EAM/APM", "Asset Health / APM"),
    "bentley apm": ("EAM/APM", "Asset Health / APM"),
    "aveva apm": ("EAM/APM", "Asset Health / APM"),
    "meridium": ("EAM/APM", "Asset Health / APM"),
    "ge apm": ("EAM/APM", "Asset Health / APM"),
    "predix": ("EAM/APM", "Asset Health / APM"),
    "salesforce fsl": ("EAM/APM", "Mobile Work Orders"),
    "clicksoftware": ("EAM/APM", "Scheduling & Dispatch"),
    "clevest": ("EAM/APM", "Mobile Work Orders"),

    # --- Data historians -----------------------------------------------------
    "pi": ("Data Historian", None),
    "osisoft": ("Data Historian", None),
    "osisoft pi": ("Data Historian", None),
    "pi af": ("Data Historian", None),
    "pi server": ("Data Historian", None),
    "aveva pi": ("Data Historian", None),
    "wonderware": ("Data Historian", None),
    "insql": ("Data Historian", None),
    "ip21": ("Data Historian", None),
    "aspen ip21": ("Data Historian", None),
    "infoplus": ("Data Historian", None),
    "ovation": ("Data Historian", None),
    "emerson ovation": ("Data Historian", None),
    "ppa": ("Data Historian", None),
    "canary": ("Data Historian", None),
    "ge proficy": ("Data Historian", None),
    "proficy": ("Data Historian", None),
    "ihistorian": ("Data Historian", None),
    "factorytalk": ("Data Historian", None),
    "ecostruxure historian": ("Data Historian", None),

    # --- ADMS / OMS / DMS (distribution) -------------------------------------
    "adms": ("ADMS (Distribution)", None),
    "oms": ("ADMS (Distribution)", "OMS (Outage Management)"),
    "dms": ("ADMS (Distribution)", None),
    "schneider adms": ("ADMS (Distribution)", None),
    "ecostruxure adms": ("ADMS (Distribution)", None),
    "telvent": ("ADMS (Distribution)", None),
    "poweron": ("ADMS (Distribution)", None),
    "ge poweron": ("ADMS (Distribution)", None),
    "ge adms": ("ADMS (Distribution)", None),
    "oracle nms": ("ADMS (Distribution)", "OMS (Outage Management)"),
    "oracle dms": ("ADMS (Distribution)", None),
    "cgi": ("ADMS (Distribution)", "OMS (Outage Management)"),
    "cgi pragma": ("ADMS (Distribution)", "OMS (Outage Management)"),
    "milsoft": ("ADMS (Distribution)", "OMS (Outage Management)"),
    "futura": ("ADMS (Distribution)", None),
    "survalent": ("ADMS (Distribution)", None),
    "efacec": ("ADMS (Distribution)", None),
    "advanced control systems": ("ADMS (Distribution)", None),
    "acs prism": ("ADMS (Distribution)", None),
    "cyme": ("ADMS (Distribution)", "Network Analysis / Load Flow"),
    "synergi": ("ADMS (Distribution)", "Network Analysis / Load Flow"),
    "windmil": ("ADMS (Distribution)", "Network Analysis / Load Flow"),

    # --- EMS / SCADA (transmission) -----------------------------------------
    "ems": ("EMS (Transmission)", None),
    "scada": ("EMS (Transmission)", "T-SCADA"),
    "habitat": ("EMS (Transmission)", None),
    "e terra": ("EMS (Transmission)", None),
    "eterra": ("EMS (Transmission)", None),
    "eterrahabitat": ("EMS (Transmission)", None),
    "spectrum power": ("EMS (Transmission)", None),
    "siemens spectrum": ("EMS (Transmission)", None),
    "osi monarch": ("EMS (Transmission)", None),
    "monarch": ("EMS (Transmission)", None),
    "open systems international": ("EMS (Transmission)", None),
    "pss e": ("EMS (Transmission)", "State Estimator / Power Flow"),
    "psse": ("EMS (Transmission)", "State Estimator / Power Flow"),
    "powerworld": ("EMS (Transmission)", "State Estimator / Power Flow"),
    "aspen oneliner": ("EMS (Transmission)", "Protection & Relay Settings"),
    "oneliner": ("EMS (Transmission)", "Protection & Relay Settings"),
    "cape": ("EMS (Transmission)", "Protection & Relay Settings"),

    # --- Meter / AMI / MDM ---------------------------------------------------
    "ami": ("Meter/AMI/MDM", "Interval Usage Collection"),
    "mdm": ("Meter/AMI/MDM", "MDM Repository"),
    "mdms": ("Meter/AMI/MDM", "MDM Repository"),
    "itron": ("Meter/AMI/MDM", None),
    "openway": ("Meter/AMI/MDM", "Interval Usage Collection"),
    "landis gyr": ("Meter/AMI/MDM", None),
    "landisgyr": ("Meter/AMI/MDM", None),
    "gridstream": ("Meter/AMI/MDM", "Interval Usage Collection"),
    "sensus": ("Meter/AMI/MDM", None),
    "flexnet": ("Meter/AMI/MDM", "Interval Usage Collection"),
    "aclara": ("Meter/AMI/MDM", None),
    "honeywell elster": ("Meter/AMI/MDM", None),
    "elster": ("Meter/AMI/MDM", None),
    "tantalus": ("Meter/AMI/MDM", None),
    "oracle mdm": ("Meter/AMI/MDM", "MDM Repository"),
    "smart grid gateway": ("Meter/AMI/MDM", "Head-End System"),
    "headend": ("Meter/AMI/MDM", "Head-End System"),
    "head end": ("Meter/AMI/MDM", "Head-End System"),

    # --- CIS / Billing -------------------------------------------------------
    "cis": ("CIS/Billing", None),
    "sap isu": ("CIS/Billing", None),
    "isu": ("CIS/Billing", None),
    "sap is u": ("CIS/Billing", None),
    "ccs": ("CIS/Billing", None),
    "cc b": ("CIS/Billing", None),
    "ccb": ("CIS/Billing", None),
    "oracle ccb": ("CIS/Billing", None),
    "customer care and billing": ("CIS/Billing", None),
    "cc and b": ("CIS/Billing", None),
    "banner": ("CIS/Billing", None),
    "sctcs": ("CIS/Billing", None),
    "northstar": ("CIS/Billing", None),
    "hansen": ("CIS/Billing", None),
    "gentrack": ("CIS/Billing", None),
    "junifer": ("CIS/Billing", None),
    "kubra": ("CIS/Billing", "Payments & Collections"),
    "paymentus": ("CIS/Billing", "Payments & Collections"),

    # --- GIS -----------------------------------------------------------------
    "gis": ("GIS", None),
    "esri": ("GIS", None),
    "arcgis": ("GIS", None),
    "arcfm": ("GIS", None),
    "utility network": ("GIS", "Utility Network (ArcGIS)"),
    "smallworld": ("GIS", "Electric Network Model / Connectivity"),
    "ge smallworld": ("GIS", "Electric Network Model / Connectivity"),
    "intergraph": ("GIS", None),
    "gtechnology": ("GIS", None),
    "schneider arcfm": ("GIS", None),
    "field maps": ("GIS", "Mobile GIS / Field Maps"),
    "collector": ("GIS", "Mobile GIS / Field Maps"),

    # --- DERMS / DER ---------------------------------------------------------
    "derms": ("DERMS", None),
    "autogrid": ("DERMS", None),
    "enbala": ("DERMS", None),
    "generac concerto": ("DERMS", None),
    "concerto": ("DERMS", None),
    "virtual peaker": ("DERMS", None),
    "uplight": ("DERMS", None),
    "enel x": ("DERMS", None),
    "opus one": ("DERMS", None),
    "smarter grid solutions": ("DERMS", None),

    # --- Market / ISO --------------------------------------------------------
    "pjm": ("Market/ISO Feed", None),
    "miso": ("Market/ISO Feed", None),
    "caiso": ("Market/ISO Feed", None),
    "ercot": ("Market/ISO Feed", None),
    "spp": ("Market/ISO Feed", None),
    "nyiso": ("Market/ISO Feed", None),
    "isone": ("Market/ISO Feed", None),
    "iso ne": ("Market/ISO Feed", None),
    "aeso": ("Market/ISO Feed", None),
    "ieso": ("Market/ISO Feed", None),
    "dataminer": ("Market/ISO Feed", None),
    "oasis": ("Market/ISO Feed", None),
    "allegro": ("Market/ISO Feed", "ETRM / Position"),
    "openlink": ("Market/ISO Feed", "ETRM / Position"),
    "endur": ("Market/ISO Feed", "ETRM / Position"),
    "etrm": ("Market/ISO Feed", "ETRM / Position"),

    # --- PMU / synchrophasor -------------------------------------------------
    "pmu": ("PMU/Synchrophasor", "Phasor Measurement"),
    "synchrophasor": ("PMU/Synchrophasor", "Phasor Measurement"),
    "pdc": ("PMU/Synchrophasor", None),
    "openpdc": ("PMU/Synchrophasor", None),
    "phasorpoint": ("PMU/Synchrophasor", None),
    "epg": ("PMU/Synchrophasor", None),

    # --- Weather -------------------------------------------------------------
    "noaa": ("Weather", "NWP Forecasts"),
    "nws": ("Weather", "NWP Forecasts"),
    "ecmwf": ("Weather", "NWP Forecasts"),
    "hrrr": ("Weather", "NWP Forecasts"),
    "gfs": ("Weather", "NWP Forecasts"),
    "dtn": ("Weather", None),
    "schneider weather": ("Weather", None),
    "vaisala": ("Weather", "Lightning Detection"),
    "earth networks": ("Weather", "Lightning Detection"),
    "atmospheric g2": ("Weather", None),
    "weather company": ("Weather", None),

    # --- Environmental / nuclear --------------------------------------------
    "cems": ("CEMS", None),
    "lims": ("LIMS", "Sample Management"),
    "labware": ("LIMS", "Sample Management"),
    "starlims": ("LIMS", "Sample Management"),
    "sample manager": ("LIMS", "Sample Management"),
    "dosimetry": ("Radiation/Dosimetry", "Personnel Dosimetry"),
    "landauer": ("Radiation/Dosimetry", "Personnel Dosimetry"),
    "mirion": ("Radiation/Dosimetry", None),
    "thermo dosimetry": ("Radiation/Dosimetry", None),

    # --- Fuel ----------------------------------------------------------------
    "fuel management": ("Fuel Management", None),
    "coal yard": ("Fuel Management", None),
    "gas nomination": ("Fuel Management", None),
    "nomination": ("Fuel Management", None),
    "pipeline nomination": ("Fuel Management", None),

    # --- Document / content --------------------------------------------------
    "sharepoint": ("Document/Content Mgmt", None),
    "documentum": ("Document/Content Mgmt", None),
    "opentext": ("Document/Content Mgmt", None),
    "filenet": ("Document/Content Mgmt", None),
    "hyland": ("Document/Content Mgmt", None),
    "asite": ("Document/Content Mgmt", None),
    "bentley projectwise": ("Document/Content Mgmt", None),
    "projectwise": ("Document/Content Mgmt", None),
}

# Words that must never resolve on their own. Each is a real vendor token but is also
# a common English or infrastructure word, so matching it alone would attribute
# unrelated schemas — "prod_oracle_reporting" is not a CIS.
AMBIGUOUS_ALONE = frozenset({
    "oracle", "ge", "abb", "siemens", "schneider", "emerson", "honeywell",
    "itron", "bentley", "infor", "aveva", "hansen", "banner", "cascade",
    "collector", "monarch", "canary", "concerto", "oasis", "spp", "epg",
})


def alias_lookup(tokens: set[str]) -> tuple[str, str | None, str] | None:
    """Resolve a token set to (category, module, matched_alias), or None.

    Multi-word aliases are tried first and longest-first, so "sap pm" wins over
    "sap" and resolves to a module rather than just a category. Single-word aliases
    listed in AMBIGUOUS_ALONE are skipped: they are only meaningful as part of a
    longer phrase.
    """
    best: tuple[str, str | None, str] | None = None
    best_words = 0
    for alias, (category, module) in VENDOR_ALIASES.items():
        words = alias.split()
        if len(words) == 1 and alias in AMBIGUOUS_ALONE:
            continue
        if not set(words) <= tokens:
            continue
        # Prefer the most specific match: more words, then a named module.
        score = len(words) * 2 + (1 if module else 0)
        if score > best_words:
            best, best_words = (category, module, alias), score
    return best
