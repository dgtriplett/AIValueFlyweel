"""Resolve maturity-roadmap free-text data needs to canonical Value Flywheel assets."""
from __future__ import annotations

import re

from .normalization import _tokens
from .pu_tables import keyword_lookup


def clean_label(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _compact_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _has_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _has_all(text: str, *needles: str) -> bool:
    return all(needle in text for needle in needles)


_ROADMAP_ASSET_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("erp/ap", "ap data", "accounts payable", "invoice", "invoices",
      "gl posting", "general ledger", "ledger"),
     "ERP", "Finance & Controlling"),
    (("hris", "workday", "successfactors", "active employees", "employee role",
      "employee roles", "termination", "hire", "training completion"),
     "ERP", "Human Capital Management"),
    (("program enrollment", "program management", "dsm", "rebate",
      "energy efficiency program", "discount issuance", "outreach campaign",
      "incentive payout"),
     "CIS/Billing", "Programs / DSM Enrollment"),
    (("service class", "rate code", "rate schedule", "tariff"),
     "CIS/Billing", "Rate Schedules / Tariffs"),
    (("customer count", "customer mix", "end-use appliance", "end use appliance",
      "customer demographic", "demographics", "service address", "customer complaint"),
     "CIS/Billing", "CRM / Contact Center"),
    (("purchase order", "purchase orders", "requisition", "requisitions",
      "procurement", "sourcing", "supplier", "contract", "contracts",
      "terms data", "vendor master", "spend classification", "benchmark"),
     "ERP", "Procurement / Sourcing"),
    (("inventory level", "inventory levels", "inventory turnover", "warehouse",
      "stock", "storeroom"),
     "ERP", "Inventory / Warehouse"),
    (("material", "materials", "spare part", "spare parts", "goods movement"),
     "ERP", "Materials Management"),
    (("employee", "employees", "workforce", "labor", "timesheet", "payroll",
      "training", "certification", "retirement", "crew deployment", "crew system"),
     "ERP", "Human Capital Management"),
    (("work order", "work orders", "maintenance record", "corrective maintenance",
      "preventive maintenance", "mobile workforce"),
     "EAM/APM", "Work Order Management"),
    (("asset registry", "asset hierarchy", "asset master", "asset condition",
      "asset health", "equipment master"),
     "EAM/APM", "Asset Registry & Hierarchy"),
    (("predictive maintenance", "condition monitoring", "pdm", "sensor health"),
     "EAM/APM", "Condition Monitoring / PdM"),
    (("leak survey", "picarro", "aerial", "walking", "close interval survey",
      "close-interval survey", "ili", "in line inspection", "in-line inspection"),
     "EAM/APM", "Inspection / Rounds"),
    (("cathodic", "rectifier", "coating survey", "coating surveys",
      "pipe attribute", "pipe attributes", "smys", "maop", "class location",
      "regulator station", "equipment specifications", "design parameters"),
     "EAM/APM", "Asset Registry & Hierarchy"),
    (("sensor history", "validated sensor", "vibration sensor", "accelerometer"),
     "EAM/APM", "Condition Monitoring / PdM"),
    (("dcs", "historian", "osisoft", "wonderware", "predix", "deltav",
      "pi af sdk"),
     "Data Historian", "Balance of Plant"),
    (("scada", "telemetry", "rtu", "supervisory control", "historian tags"),
     "Standalone SCADA", "Real-Time Telemetry & Control"),
    (("ems daily", "line mw", "line rating", "line ratings", "substation alarm",
      "alarm log"),
     "EMS (Transmission)", "T-SCADA"),
    (("distribution scada", "d scada", "d-scada", "feeder telemetry"),
     "ADMS (Distribution)", "D-SCADA"),
    (("outage", "restoration", "trouble call", "saidi", "saifi", "gads"),
     "ADMS (Distribution)", "OMS (Outage Management)"),
    (("ami", "smart meter", "interval usage", "interval data", "meter read",
      "meter reading", "meter reads", "consumption", "ami device"),
     "Meter/AMI/MDM", "Interval Usage Collection"),
    (("gis", "geospatial", "asset location", "spatial", "connectivity",
      "network model", "network topology"),
     "GIS", "Electric Network Model / Connectivity"),
    (("vegetation", "right of way", "right-of-way", "lidar", "imagery"),
     "GIS", "Vegetation Management"),
    (("mdm", "meter data management", "vee"),
     "Meter/AMI/MDM", "MDM Repository"),
    (("meter event", "meter events", "last gasp", "tamper"),
     "Meter/AMI/MDM", "Meter Events / Alarms"),
    (("meter voltage", "voltage telemetry", "power quality"),
     "Meter/AMI/MDM", "Voltage / VAR Telemetry"),
    (("cis", "customer account", "customer accounts", "premise", "account history"),
     "CIS/Billing", "Customer Accounts"),
    (("billing", "payments", "collections", "arrears", "dunning"),
     "CIS/Billing", "Billing Engine / Determinants"),
    (("service order", "field service", "move in", "move out"),
     "CIS/Billing", "Service Orders / Field Service"),
    (("weather", "storm", "lightning", "forecast", "noaa", "hrrr",
      "soil condition", "frost depth", "climate data"),
     "Weather", "Storm / Severe Weather Tracking"),
    (("generation output", "plant performance", "daily plant", "unit mwh"),
     "Data Historian", "Balance of Plant"),
    (("fuel input", "fuel supply", "fuel quality", "hydrogen availability"),
     "Fuel Management", "Fuel Quality / Blending"),
    (("day ahead", "day-ahead", "real time lmp", "real-time lmp", "iso market",
      "settlement", "settlements"),
     "Market/ISO Feed", "Settlements / Invoicing"),
    (("compliance reporting", "regulatory approval", "regulatory framework",
      "procedure", "procedures", "record retention"),
     "Document/Content Mgmt", "Regulatory Document Mgmt"),
    (("consequence modeling", "economic impact", "financial impact"),
     "ERP", "Finance & Controlling"),
)


def canonical_asset_target(label: str) -> tuple[str, str] | None:
    """Map maturity roadmap free text to the shipped Value Flywheel asset catalog."""
    clean = clean_label(label)
    if not clean:
        return None
    text = _compact_label(clean)
    original = clean.lower()

    if _has_any(text, "erp financial", "financial extract"):
        return "ERP", "Finance & Controlling"
    if _has_all(text, "customer", "data", "warehouse"):
        return "CIS/Billing", "Customer Accounts"
    if _has_any(text, "daily plant ops", "plant ops report"):
        return "Data Historian", "Balance of Plant"
    if _has_all(text, "ems", "extract"):
        return "EMS (Transmission)", "T-SCADA"
    if _has_any(text, "hydrogen availability", "hydrogen"):
        return "Fuel Management", "Fuel Quality / Blending"
    if _has_any(text, "scada") and _has_any(text, "historian", "telemetry", "pressure", "flow"):
        return "Standalone SCADA", "Real-Time Telemetry & Control"

    for needles, category, module in _ROADMAP_ASSET_RULES:
        normalized_needles = tuple(needle.replace("-", " ") for needle in needles)
        if _has_any(text, *normalized_needles) or _has_any(original, *needles):
            return category, module

    if _has_all(text, "sap", "erp"):
        return "ERP", "Finance & Controlling"
    if _has_all(text, "contract", "management"):
        return "ERP", "Procurement / Sourcing"
    if _has_all(text, "customer", "program"):
        return "CIS/Billing", "Programs / DSM Enrollment"
    if _has_all(text, "customer", "contact"):
        return "CIS/Billing", "CRM / Contact Center"
    if _has_all(text, "fuel", "contract"):
        return "Fuel Management", "Fuel Contracts / Procurement"
    if _has_all(text, "gas", "nomination"):
        return "Fuel Management", "Gas Nominations"

    hit = keyword_lookup(_tokens(clean))
    if hit:
        return hit[0], hit[1]
    return None
