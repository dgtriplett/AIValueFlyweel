"""Build AI Value Flywheel seed_data.json from the parent app's P&U content.

Source (read-only, exported): data-ai-maturity-assessment
  - backend/industries/pu/use_cases_seed.json  (198 use cases, valueModel)
  - frontend/src/data/businessValue.ts          (34 global assumptions)

Mapping into AI Value Flywheel's schema:
  domain (6)            -> lobs
  use case              -> use_cases (phase, status, category, effort, priority,
                                      hypothesized_value_json = valueModel)
  dependencies (UC ids) -> uc_enables_uc  (dep --enables--> uc)
  dataRequirements      -> canonical data_assets + uc_requires_asset (keyword map)
  valueModel.components -> parameterized value; global assumptions -> value_assumptions
  businessValue[0..] + valueModel low/high -> value_records + realized for 'live' UCs

Deterministic (seeded). Emits scripts/seed_data.json consumed by seed_demo.py / seed_clean.py.
"""
import json
import random
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).parent))
from pu_catalog import MODULES, EXTRA_USE_CASES  # noqa: E402  authoritative catalog
from pathlib import Path

SEED = 20260706
random.seed(SEED)

import os as _os

HERE = Path(__file__).parent
PARENT = Path("/tmp/dama")
UC_JSON = PARENT / "backend/industries/pu/use_cases_seed.json"
BV_TS = PARENT / "frontend/src/data/businessValue.ts"
OUT = HERE / "seed_data.json"

# ---------------------------------------------------------------------------
# 1) LOBs = parent's 6 domains (fixed order from meta.json)
# ---------------------------------------------------------------------------
DOMAIN_ORDER = ["Generation", "Transmission", "Distribution", "Customer",
                "Regulatory", "Corporate Services"]
DOMAIN_DESC = {
    "Generation": "Fossil, hydro, renewable and nuclear generation operations and optimization.",
    "Transmission": "Bulk power transmission operations, reliability and NERC compliance.",
    "Distribution": "Distribution grid operations, reliability, DER and outage management.",
    "Customer": "Customer experience, billing, contact center and demand-side programs.",
    "Regulatory": "Regulatory, compliance, rate cases and reporting (NRC, FERC, EPA, NERC).",
    "Corporate Services": "Finance, HR, supply chain, safety, ESG and corporate strategy.",
}
LOBS = [(d, DOMAIN_DESC[d]) for d in DOMAIN_ORDER]

# ---------------------------------------------------------------------------
# 2) Global value assumptions (34) — parsed from businessValue.ts + curated meta
# ---------------------------------------------------------------------------
_ASSUMPTION_META = {
    # key: (label, unit, category)
    "customerCount": ("Customers served", "count", "Utility profile"),
    "annualRevenueMM": ("Annual revenue", "$M", "Utility profile"),
    "omBudgetMM": ("Annual O&M budget", "$M", "Utility profile"),
    "generationFleetMW": ("Generation fleet capacity", "MW", "Utility profile"),
    "tdLineMiles": ("T&D line miles", "miles", "Utility profile"),
    "transformerCount": ("Distribution transformers", "count", "Utility profile"),
    "fieldCrewRate": ("Field crew loaded rate", "$/hr", "Labor & cost rates"),
    "engineerRate": ("Engineer loaded rate", "$/hr", "Labor & cost rates"),
    "dataScienceRate": ("Data scientist loaded rate", "$/hr", "Labor & cost rates"),
    "avgResidentialRevenue": ("Avg residential revenue", "$/yr", "Revenue & customer"),
    "avgCommercialRevenue": ("Avg commercial revenue", "$/yr", "Revenue & customer"),
    "customerChurnPct": ("Annual customer churn", "%", "Revenue & customer"),
    "transformerReplaceCost": ("Transformer replacement cost", "$", "Grid & operations"),
    "avgStormCostMM": ("Avg major storm cost", "$M", "Grid & operations"),
    "stormsPerYear": ("Major storms per year", "count", "Grid & operations"),
    "saidiMinuteValueMM": ("Value per SAIDI minute", "$M", "Grid & operations"),
    "currentSAIDI": ("Current SAIDI", "min", "Grid & operations"),
    "fuelCostPerMMBTU": ("Fuel cost", "$/MMBtu", "Fuel & generation"),
    "annualFuelSpendMM": ("Annual fuel spend", "$M", "Fuel & generation"),
    "capacityPriceMWDay": ("Capacity market price", "$/MW-day", "Fuel & generation"),
    "badDebtPct": ("Bad debt", "% revenue", "Financial"),
    "capitalBudgetMM": ("Annual capital budget", "$M", "Financial"),
    "procurementSpendMM": ("Annual procurement spend", "$M", "Financial"),
    "oshaIncidentCostK": ("Cost per OSHA recordable", "$K", "Safety & workforce"),
    "oshaRecordablesPerYear": ("OSHA recordables per year", "count", "Safety & workforce"),
    "avgReplacementCostK": ("Cost to replace employee", "$K", "Safety & workforce"),
    "workforceSize": ("Total headcount", "count", "Safety & workforce"),
    "retirementEligiblePct": ("Retirement-eligible in 5yr", "%", "Safety & workforce"),
    "rateCasePrepCostMM": ("Rate case prep cost", "$M", "Regulatory"),
    "regulatoryPenaltyRiskMM": ("Annual penalty risk", "$M", "Regulatory"),
    "callsPerYear": ("Annual inbound calls", "count", "Call center"),
    "costPerCall": ("Cost per call", "$", "Call center"),
    "transmissionCapexMM": ("Annual transmission capex", "$M", "Scaling"),
    "distributionCapexMM": ("Annual distribution capex", "$M", "Scaling"),
}


def parse_assumptions() -> dict[str, float]:
    t = BV_TS.read_text()
    m = re.search(r"DEFAULT_ASSUMPTIONS[^{]*\{(.*?)\n\}", t, re.S)
    body = m.group(1)
    out = {}
    for line in body.splitlines():
        lm = re.match(r"\s*([a-zA-Z_]\w*):\s*([0-9_\.eE\-]+)", line)
        if lm:
            out[lm.group(1)] = float(lm.group(2).replace("_", ""))
    return out


# ---------------------------------------------------------------------------
# 3) Canonical data assets — authoritative module catalog (scripts/pu_catalog.py)
#    tuple shape: (source_category, module, keywords, sub_vertical, domain, effort)
# ---------------------------------------------------------------------------
CANONICAL_ASSETS = MODULES
_OLD_UNUSED = [
    # --- ERP (vendor-neutral modules) ---
    ("ERP", "Plant Maintenance", "SAP", ["work order", "maintenance record", "equipment master", "cmms", "notification"], "cross", "Corporate Services"),
    ("ERP", "Materials Management", "SAP", ["inventory", "spare part", "materials", "vendor master", "goods movement"], "cross", "Corporate Services"),
    ("ERP", "Finance & Controlling", "SAP", ["financial", "cost center", "ledger", "actuals", "revenue", "gl "], "cross", "Corporate Services"),
    ("ERP", "Projects", "SAP", ["project", "wbs", "capital project", "outage project"], "cross", "Corporate Services"),
    ("ERP", "HR / HCM", "SAP", ["workforce", "employee", "headcount", "certification", "timesheet", "labor", "retirement"], "cross", "Corporate Services"),
    ("ERP", "Procurement", "SAP", ["procurement", "purchase order", "sourcing"], "cross", "Corporate Services"),
    ("ERP", "Quality Management", "SAP", ["inspection lot", "quality notification", "supplier quality"], "cross", "Corporate Services"),
    # --- EAM/APM (Asset & Work Mgmt) ---
    ("EAM/APM (Asset & Work Mgmt)", "Asset Health & Criticality", "IBM Maximo", ["asset health", "criticality", "reliability strategy", "apm", "eam"], "cross", "Corporate Services"),
    ("EAM/APM (Asset & Work Mgmt)", "Reliability Library", "GE APM", ["epri", "failure mode", "reliability data", "preventive maintenance template"], "cross", "Generation"),
    ("EAM/APM (Asset & Work Mgmt)", "Vibration Routes & Online", "", ["vibration", "machinery vibration", "rotating equipment"], "cross", "Generation"),
    # --- Data Historian (tag groups by unit) ---
    ("Data Historian", "Steam Turbine Tag Group", "OSIsoft PI", ["turbine", "bearing vibration", "casing temp", "thrust"], "fossil", "Generation"),
    ("Data Historian", "Generator Tag Group", "OSIsoft PI", ["generator", "stator", "rotor", "excitation", "hydrogen purity"], "fossil", "Generation"),
    ("Data Historian", "Boiler / HRSG Tag Group", "OSIsoft PI", ["boiler", "hrsg", "superheater", "drum level"], "fossil", "Generation"),
    ("Data Historian", "Reactor Coolant Tag Group", "OSIsoft PI", ["reactor coolant", "rcs", "primary loop"], "nuclear", "Generation"),
    ("Data Historian", "Feedwater Tag Group", "OSIsoft PI", ["feedwater", "condensate", "heater level"], "nuclear", "Generation"),
    ("Data Historian", "Balance-of-Plant Tag Group", "OSIsoft PI", ["balance of plant", "bop", "auxiliaries", "cooling water"], "cross", "Generation"),
    ("Data Historian", "Wind Turbine Tag Group", "OSIsoft PI", ["wind turbine", "gearbox", "pitch", "yaw", "nacelle", "power curve"], "renewables", "Generation"),
    ("Data Historian", "Solar Inverter Tag Group", "OSIsoft PI", ["solar", "inverter", "pv string", "irradiance"], "renewables", "Generation"),
    ("Data Historian", "Hydro Unit Tag Group", "OSIsoft PI", ["hydro", "wicket gate", "head", "unit mw"], "hydro", "Generation"),
    ("Data Historian", "Transformer Monitoring Tag Group", "GE Proficy", ["transformer monitor", "top-oil", "dga online", "winding temp"], "cross", "Transmission"),
    ("Data Historian", "Combustion & Event Frames", "GE Proficy", ["proficy", "event frame", "combined-cycle", "batch record", "combustion", "combustion dynamics", "emissions tuning"], "fossil", "Generation"),
    # --- SCADA/EMS ---
    ("SCADA/EMS", "State Estimator Output", "GE", ["scada", "ems", "state estimator", "real-time telemetry", "topology"], "cross", "Transmission"),
    ("SCADA/EMS", "Generation AGC Signals", "GE", ["agc", "automatic generation control", "regulation", "setpoint"], "cross", "Transmission"),
    # --- ADMS/OMS ---
    ("ADMS/OMS", "Distribution Management", "Schneider", ["adms", "dms", "distribution management", "volt/var", "fault location"], "cross", "Distribution"),
    ("ADMS/OMS", "Outage Events", "Oracle", ["oms", "outage", "restoration", "interruption", "saidi", "saifi", "crew status"], "cross", "Distribution"),
    # --- DERMS ---
    ("DERMS", "DER Dispatch Signals", "", ["derms", "der", "distributed energy", "aggregation", "ev charging", "battery"], "renewables", "Distribution"),
    ("DERMS", "Demand Response & Load Programs", "", ["demand response", "load control", "demand-side", "energy efficiency program"], "cross", "Customer"),
    # --- GIS ---
    ("GIS", "Asset Network Model", "ESRI", ["gis", "geospatial", "network model", "topology", "asset location", "connectivity"], "cross", "Distribution"),
    ("GIS", "Vegetation & Imagery", "", ["vegetation", "lidar", "satellite", "imagery", "drone", "uas"], "cross", "Distribution"),
    # --- PMU / Synchrophasor & sensors ---
    ("PMU/Synchrophasor", "Phasor Measurement Stream", "", ["synchrophasor", "pmu", "phasor", "oscillation", "stability"], "cross", "Transmission"),
    ("Grid Sensors", "Protection Relay & Fault Records", "", ["protection relay", "relay event", "fault record", "digital fault recorder", "line sensor", "conductor", "sag", "dynamic line rating"], "cross", "Transmission"),
    # --- Meter/AMI (MDM) & Customer/Billing (CIS) ---
    ("Meter/AMI (MDM)", "Smart Meter Interval Reads", "Itron", ["ami", "smart meter", "interval data", "meter read", "consumption"], "cross", "Customer"),
    ("Customer/Billing (CIS)", "Billing & Accounts", "Oracle", ["billing", "customer account", "meter-to-cash", "arrears", "bad debt", "is-u", "utilities billing"], "cross", "Customer"),
    ("Customer/Billing (CIS)", "CRM & Contact Center", "Salesforce", ["crm", "call", "contact center", "customer preference", "demographic", "churn"], "cross", "Customer"),
    # --- Market/ISO Feed (ISOs as instances/modules) ---
    ("Market/ISO Feed", "PJM", "PJM", ["pjm", "lmp", "ancillary", "capacity market", "day-ahead"], "cross", "Generation"),
    ("Market/ISO Feed", "ERCOT", "ERCOT", ["ercot", "ordc", "real-time market", "nodal"], "cross", "Generation"),
    ("Market/ISO Feed", "MISO", "MISO", ["miso", "market clears", "transmission constraint"], "cross", "Generation"),
    ("Market/ISO Feed", "CAISO", "CAISO", ["caiso", "oasis", "flexible ramp", "curtailment"], "renewables", "Generation"),
    ("Market/ISO Feed", "Bid/Offer & Settlement", "", ["bid", "offer", "settlement statement", "position management", "market rules"], "cross", "Generation"),
    # --- Weather ---
    ("Weather", "Numerical Weather Prediction", "NOAA", ["noaa", "hrrr", "gfs", "numerical weather", "weather grid"], "renewables", "Generation"),
    ("Weather", "Site Forecast", "DTN", ["dtn", "site weather", "wind forecast", "irradiance forecast", "temperature", "precipitation"], "renewables", "Generation"),
    ("Weather", "Lightning & Storm Feed", "Vaisala", ["lightning", "storm track", "storm feed"], "cross", "Distribution"),
    # --- Emissions / Lab / Radiation ---
    ("Emissions Monitoring (CEMS)", "Continuous Emissions Monitoring", "", ["cems", "emission", "nox", "so2", "co2", "opacity", "part 75"], "fossil", "Regulatory"),
    ("Lab (LIMS)", "Chemistry Lab Results", "LabWare", ["lims", "lab", "chemistry", "sample", "water quality"], "cross", "Generation"),
    ("Radiation/Dosimetry", "Personnel Dose Records", "Mirion", ["dosimetry", "dose", "alara", "cumulative dose"], "nuclear", "Generation"),
    ("Radiation/Dosimetry", "Area & Effluent Monitors", "Mirion", ["radiation monitor", "effluent", "area monitor", "radioactive"], "nuclear", "Regulatory"),
    # --- Fuel Management ---
    ("Fuel Management", "Fossil Fuel & Contracts", "", ["fuel schedule", "coal", "gas nomination", "fuel contract", "fuel availability"], "fossil", "Generation"),
    ("Fuel Management", "Nuclear Fuel Cycle", "", ["fuel assembly", "burnup", "enrichment", "core load", "fuel cycle"], "nuclear", "Generation"),
    # --- Document/Content Mgmt & Regulatory ---
    ("Document/Content Mgmt", "Regulatory Document Corpus", "", ["nrc", "generic letter", "bulletin", "license basis", "inspection report", "procedure qualification"], "nuclear", "Regulatory"),
    ("Document/Content Mgmt", "Rate Case & Commitments", "", ["rate case", "regulatory commitment", "allowed return", "tariff", "filing", "penalty"], "cross", "Regulatory"),
    ("Safety/EHS", "Incidents & Observations", "", ["safety", "osha", "incident", "ehs", "near miss", "recordable"], "cross", "Corporate Services"),
    ("Supply Chain", "Logistics & Suppliers", "", ["supply chain", "logistics", "supplier", "shipment", "lead time"], "cross", "Corporate Services"),
]


def map_assets_for_uc(uc: dict) -> list[tuple[int, str]]:
    """Return list of (asset_index, criticality) for a use case."""
    reqs = [r.lower() for r in (uc.get("dataRequirements") or []) if isinstance(r, str)]
    text = " ".join(reqs)
    matched = []
    for idx, (_cat, _mod, kws, _sv, _owner, _eff) in enumerate(CANONICAL_ASSETS):
        if any(kw in text for kw in kws):
            matched.append(idx)
    # dedupe, cap 2..5, first two required rest helpful
    matched = matched[:5]
    if not matched:
        # fallback: attach SCADA + historian so every UC has some requirement
        matched = [0, 1]
    out = []
    for i, idx in enumerate(matched):
        out.append((idx, "required" if i < max(2, len(matched) - 1) else "helpful"))
    return out


# ---------------------------------------------------------------------------
# 4) Field mappings
# ---------------------------------------------------------------------------
def complexity_to_effort(c) -> str:
    return {1: "S", 2: "M", 3: "L", 4: "XL"}.get(int(c or 3), "M")


_NUCLEAR_TERMS = ("reactor", "refuel", "dosimetry", "alara", "radiation", "spent fuel",
                  "burnup", "fuel cycle", "containment", "scram", "rcs", "nrc ", "criticality safety",
                  "enrichment", "core loading")
_RENEW_TERMS = ("solar", "wind", "renewable", "photovoltaic", "pv ", "der ", "distributed energy", "battery storage", "inverter")
_HYDRO_TERMS = ("hydro", "hydroelectric", "reservoir", "wicket gate")
_FOSSIL_TERMS = ("boiler", "hrsg", "combustion", "coal", "gas turbine", "combined-cycle", "steam turbine", "heat rate", "emissions")


def sub_vertical_for(uc: dict) -> str:
    """Generation focus. Only 'nuclear' when the use case is GENUINELY
    nuclear-specific (its own title/description names nuclear systems/processes),
    NOT merely because a utilityType/tag mentions nuclear. Generic/cross-cutting
    use cases -> 'cross' (All)."""
    text = f"{uc.get('name','')} {uc.get('description','')} {' '.join(uc.get('tags') or [])}".lower()
    if any(t in text for t in _NUCLEAR_TERMS):
        return "nuclear"
    if any(t in text for t in _RENEW_TERMS):
        return "renewables"
    if any(t in text for t in _HYDRO_TERMS):
        return "hydro"
    if uc.get("domain") == "Generation" and any(t in text for t in _FOSSIL_TERMS):
        return "fossil"
    # Generation UCs without a specific fuel signal are cross-fleet
    return "cross"


# realistic project-status spread (independent of phase). Deterministic per index.
def status_for(i: int, phase: int) -> str:
    r = random.random()
    if r < 0.62:
        return "not_started"
    if r < 0.78:
        return "scoping"
    if r < 0.90:
        return "in_progress"
    if r < 0.96:
        return "live"
    return "value_realized"


def stage_from_status(status: str) -> str:
    return {
        "not_started": "U1", "scoping": "U2", "in_progress": "U3",
        "live": "U5", "value_realized": "U6",
    }.get(status, "U1")


def compute_uc_value(vm: dict, assumptions: dict) -> tuple[float, float, float]:
    """Return (low, mid, high) annual $M from valueModel.components."""
    low = high = 0.0
    for c in (vm or {}).get("components", []) or []:
        v = float(c.get("multiplier", 0))
        for k in c.get("assumptionKeys", []):
            v *= float(assumptions.get(k, 0))
        low += v * float(c.get("lowCoeff", 1))
        high += v * float(c.get("highCoeff", 1))
    return round(low, 2), round((low + high) / 2, 2), round(high, 2)


def build():
    ucs_raw = json.loads(UC_JSON.read_text())
    assumptions = parse_assumptions()

    lob_index = {name: i + 1 for i, (name, _) in enumerate(LOBS)}

    _cost = {"S": (50_000, 150_000), "M": (150_000, 400_000), "L": (400_000, 1_000_000), "XL": (1_000_000, 2_500_000)}

    # data assets (authoritative module catalog) — generic category drives; vendor blank by default
    data_assets = []
    asset_index_by_key = {}  # (category, module) -> 0-based index for explicit requires
    for idx, (cat, mod, _kw, sv, owner_domain, eff) in enumerate(CANONICAL_ASSETS):
        clo, chi = _cost.get(eff, _cost["M"])
        asset_index_by_key[(cat, mod)] = idx
        data_assets.append({
            "source_category": cat, "vendor": None,  # vendor blank by default (customer-added metadata)
            "source_system": cat,  # legacy display mirrors the generic category
            "module": mod,
            "description": f"{cat} — {mod}.",
            "sub_vertical": sv,
            "ingestion_status": "not_started",  # set below by prevalence
            "ingest_effort": eff, "ingest_cost_low": clo, "ingest_cost_high": chi,
            "uc_catalog": "gridvalue", "uc_schema": cat.split("/")[0].split(" ")[0].lower(),
            "origin": "catalog",
            "owning_lob": owner_domain, "benefiting_lobs": [],
        })

    # build use cases + edges
    use_cases = []
    id_to_index = {}  # parent id -> 1-based our index
    for i, uc in enumerate(ucs_raw):
        id_to_index[uc["id"]] = i + 1

    # P1 FIX: re-derive imported-UC module requires with a FAST deterministic
    # domain-scoped heuristic (word-boundary + per-domain allowlist guard) that
    # kills the old substring cross-domain leakage. Runs in milliseconds — no
    # per-UC LLM loop (set SEED_USE_LLM=1 only for an optional batched refine).
    from derive_requires import derive_all
    imported_requires = derive_all(ucs_raw, CANONICAL_ASSETS, sub_vertical_for,
                                   use_llm=bool(_os.environ.get("SEED_USE_LLM")))

    asset_usage = [0] * len(CANONICAL_ASSETS)  # count how many UCs use each asset
    asset_lob_benefit = [set() for _ in CANONICAL_ASSETS]

    for i, uc in enumerate(ucs_raw):
        phase = int(uc.get("phase") or 1)
        status = status_for(i, phase)
        sv = sub_vertical_for(uc)
        vm = uc.get("valueModel") or {}
        low, mid, high = compute_uc_value(vm, assumptions)

        # store the structured valueModel so the backend can re-quantify
        hyp = {
            "driver": (uc.get("businessValue") or [""])[0][:140],
            "components": vm.get("components", []),
            "roiMonths": vm.get("roiMonths"),
            "low_mm": low, "mid_mm": mid, "high_mm": high,
        }

        # Realized (parameterized): for delivered UCs, seed an "actual" multiplier
        # per component = hypothesized multiplier x attainment (deterministic).
        realized = None            # legacy scalar ($ absolute) kept for value_records
        realized_value_json = None
        if status in ("live", "value_realized"):
            attain = 0.72 + (hash((uc["id"], "att")) % 30) / 100.0  # 0.72..1.01
            if status == "live":
                attain *= 0.7  # live = partial ramp
            comps = []
            for c in vm.get("components", []):
                comps.append({
                    "name": c.get("name"),
                    "calculationDisplay": c.get("calculationDisplay"),
                    "multiplier": round(float(c.get("multiplier", 0)) * attain, 12),
                    "assumptionKeys": c.get("assumptionKeys", []),
                    "hypMultiplier": c.get("multiplier"),
                    "attainment": round(attain, 3),
                })
            realized_value_json = {"driver": "Realized (actuals)", "components": comps}
            # scalar $ (mid basis) for a value_records row
            rv = 0.0
            for c in comps:
                v = float(c["multiplier"])
                for k in c["assumptionKeys"]:
                    v *= float(assumptions.get(k, 0))
                rv += v
            realized = round(rv * 1_000_000, 0)

        lob = uc.get("domain")
        risk_tags = [t for t in (uc.get("tags") or []) if t][:4]
        compliance = []
        low_txt = (uc.get("description", "") + " " + " ".join(uc.get("tags") or [])).lower()
        for tag, kws in [("NRC", ["nuclear", "nrc"]), ("NERC", ["nerc", "cip", "transmission", "reliability"]),
                         ("FERC", ["ferc", "market", "wholesale"]), ("EPA", ["emission", "epa", "environmental"])]:
            if any(k in low_txt for k in kws):
                compliance.append(tag)

        use_cases.append({
            "parent_id": uc["id"],
            "title": uc["name"],
            "description": uc.get("description", ""),
            "lob": lob,
            "sub_vertical": sv,
            "phase": phase,
            "status": status,
            "stage": stage_from_status(status),
            "category": uc.get("category"),
            "effort_tshirt": complexity_to_effort(uc.get("complexity")),
            "priority_score": round(float(uc.get("winRate") or 0.5) * 100, 1),
            "risk_tags": risk_tags,
            "compliance_tags": compliance,
            "hypothesized_value_json": hyp,
            "realized_value_amount": realized,
            "realized_value_json": realized_value_json,
            "business_value": uc.get("businessValue") or [],
            "time_to_value": uc.get("timeToValue"),
            "value_low_mm": low, "value_mid_mm": mid, "value_high_mm": high,
        })

        # requires edges (LLM-derived, domain-guarded)
        for (aidx, crit) in imported_requires.get(uc["id"], []):
            asset_usage[aidx] += 1
            if lob:
                asset_lob_benefit[aidx].add(lob)

    # ----- EXTRA authored use cases (accurate explicit module requires) -----
    extra_requires = []  # (uc_id, asset_index, crit) with explicit module mapping
    extra_enables = []   # (from_id, to_id)
    all_ids = set(id_to_index) | {e["id"] for e in EXTRA_USE_CASES}
    for e in EXTRA_USE_CASES:
        comps = e["value_components"]
        low = high = 0.0
        for c in comps:
            v = float(c["multiplier"])
            for k in c["assumptionKeys"]:
                v *= float(assumptions.get(k, 0))
            low += v * float(c.get("lowCoeff", 0.7))
            high += v * float(c.get("highCoeff", 1.3))
        mid = round((low + high) / 2, 2)
        hyp = {"driver": e["description"][:140], "components": comps, "roiMonths": None,
               "low_mm": round(low, 2), "mid_mm": mid, "high_mm": round(high, 2)}
        use_cases.append({
            "parent_id": e["id"], "title": e["title"], "description": e["description"],
            "lob": e["domain"], "sub_vertical": e["sub_vertical"], "phase": 1,
            "status": "not_started", "stage": "U1", "category": None,
            "effort_tshirt": e["effort"], "priority_score": round(e.get("win_rate", 0.5) * 100, 1),
            "risk_tags": e.get("risk_tags", []), "compliance_tags": e.get("compliance", []),
            "hypothesized_value_json": hyp, "realized_value_amount": None, "realized_value_json": None,
            "business_value": [e["description"]], "time_to_value": e.get("time_to_value"),
            "value_low_mm": round(low, 2), "value_mid_mm": mid, "value_high_mm": round(high, 2),
        })
        for (cat, mod, crit) in e.get("requires", []):
            aidx = asset_index_by_key.get((cat, mod))
            if aidx is None:
                raise ValueError(f"EXTRA UC {e['id']} requires unknown module {(cat, mod)}")
            extra_requires.append((e["id"], aidx, crit))
            asset_usage[aidx] += 1
            asset_lob_benefit[aidx].add(e["domain"])
        for target in e.get("enables", []):
            if target in all_ids:
                extra_enables.append((e["id"], target))

    # enables edges: parent dependencies are prerequisites -> dep enables uc
    enables = []
    for i, uc in enumerate(ucs_raw):
        for dep in (uc.get("dependencies") or []):
            if dep in id_to_index:
                enables.append((dep, uc["id"]))  # dep --enables--> uc (by parent id)
    enables.extend(extra_enables)

    # set data-asset ingestion_status by prevalence (most-used = more mature),
    # owning + benefiting LOBs
    order = sorted(range(len(CANONICAL_ASSETS)), key=lambda x: -asset_usage[x])
    for rank, aidx in enumerate(order):
        frac = rank / max(len(order) - 1, 1)
        if frac < 0.30:
            st = "governed"
        elif frac < 0.55:
            st = "curated"
        elif frac < 0.80:
            st = "landed"
        else:
            st = "not_started"
        data_assets[aidx]["ingestion_status"] = st
        benefit = sorted(asset_lob_benefit[aidx])
        # keep the catalog's owning domain; benefiting = domains of UCs that use it
        data_assets[aidx]["benefiting_lobs"] = [b for b in benefit
                                                if b != data_assets[aidx]["owning_lob"]][:4]

    # value assumptions rows
    value_assumptions = []
    for k, v in assumptions.items():
        label, unit, cat = _ASSUMPTION_META.get(k, (k, "", "Other"))
        value_assumptions.append({"key": k, "label": label, "value": v, "unit": unit, "category": cat})

    # requires edges resolved to (uc_id, asset_index, crit): imported UCs by
    # LLM-derived domain-guarded map, EXTRA UCs by explicit (category, module).
    requires = []
    for uc in ucs_raw:
        for (aidx, crit) in imported_requires.get(uc["id"], []):
            requires.append((uc["id"], aidx, crit))
    requires.extend(extra_requires)

    return {
        "seed": SEED,
        "lobs": LOBS,
        "data_assets": data_assets,
        "use_cases": use_cases,
        "requires": requires,          # (parent_uc_id, asset_index_0based, criticality)
        "enables": enables,            # (from_parent_id, to_parent_id)
        "value_assumptions": value_assumptions,
        "id_to_index": id_to_index,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=2))
    print(f"Wrote {OUT}")
    print(f"  LOBs:              {len(data['lobs'])}")
    print(f"  Data assets:       {len(data['data_assets'])}")
    print(f"  Use cases:         {len(data['use_cases'])}")
    print(f"  requires edges:    {len(data['requires'])}")
    print(f"  enables edges:     {len(data['enables'])}")
    print(f"  value_assumptions: {len(data['value_assumptions'])}")
    from collections import Counter
    print("  status spread:", dict(Counter(u["status"] for u in data["use_cases"])))
    print("  phase spread: ", dict(Counter(u["phase"] for u in data["use_cases"])))
    print("  lob spread:   ", dict(Counter(u["lob"] for u in data["use_cases"])))
    tot = sum(u["value_mid_mm"] for u in data["use_cases"])
    print(f"  total mid value:   ${tot:,.0f}M")
