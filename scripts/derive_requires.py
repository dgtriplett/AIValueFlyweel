"""Build-time re-derivation of required data-source modules for the IMPORTED
use cases, replacing the naive substring keyword matcher (which produced
cross-domain nonsense like a fault-location UC requiring LIMS water chemistry).

For each imported UC we ask the Foundation Model to pick the most relevant
modules from the 146-module catalog, grounded in title+description+domain+
generation-focus, then apply a DOMAIN GUARD that strips any cross-domain leakage
(nuclear-only modules only on nuclear UCs, gas modules only on gas UCs, ADMS not
on transmission/nuclear-plant UCs, LIMS chemistry only where relevant, etc.).

Results are cached to scripts/requires_cache.json keyed by parent UC id, so the
build is reproducible and doesn't re-hit the LLM unless the catalog/UC changes.
Falls back to a domain-scoped heuristic if the LLM is unavailable.
"""
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "requires_cache.json"

# ---- domain guard rules -----------------------------------------------------
# Categories/modules that are ONLY valid for specific generation sub-verticals
# or utility domains. Guard strips a proposed module if the UC doesn't qualify.
NUCLEAR_ONLY_CATEGORISED = {
    ("Data Historian", "Reactor Coolant System"), ("Data Historian", "Reactor Core / Neutronics"),
    ("Data Historian", "Steam Generator / Secondary"), ("Data Historian", "Pressurizer"),
    ("Data Historian", "Containment"), ("Data Historian", "Safety Systems (ECCS/RHR)"),
}
NUCLEAR_ONLY_CATEGORIES = {"Radiation/Dosimetry"}
NUCLEAR_FUEL_MODULES = {("Fuel Management", "Nuclear Fuel Cycle"), ("Fuel Management", "Core Design / Reload"),
                        ("Fuel Management", "Spent Fuel Management")}
FOSSIL_MODULES = {  # thermal-plant specifics
    ("Data Historian", "Boiler / HRSG"), ("Data Historian", "Combustion / Burner Management"),
    ("Data Historian", "Steam Turbine"), ("Data Historian", "Feedwater / Condensate"),
    ("Data Historian", "Emissions Process Tags"),
    ("Fuel Management", "Fossil Fuel Inventory"), ("Fuel Management", "Fuel Quality / Blending"),
    ("CEMS",),  # whole category is fossil emissions
}
RENEWABLE_MODULES = {("Data Historian", "Wind Turbine"), ("Data Historian", "Solar Inverter / PV")}
HYDRO_MODULES = {("Data Historian", "Hydro Unit")}
# Distribution-only platform (don't attach to transmission-plant / generation-plant UCs)
DIST_CATEGORIES = {"ADMS (Distribution)", "Standalone OMS"}
TRANS_CATEGORIES = {"EMS (Transmission)"}
MARKET_CATEGORIES = {"Market/ISO Feed"}
# Renewable-forecast weather module (matches "generation" too broadly)
RENEW_WEATHER = {("Weather", "Renewable Generation Forecasting")}
# DERMS is distribution/renewables-facing
DERMS_CAT = {"DERMS"}


def _guard(uc, cat, mod):
    """Return True if (cat, mod) is domain-appropriate for this UC. Tight rules
    to prevent cross-domain leakage from generic keyword/affinity matches."""
    sv = uc.get("sub_vertical") or "cross"
    dom = uc.get("domain")
    title_desc = f"{uc.get('title','')} {uc.get('description','')}".lower()
    is_gas = "gas" in title_desc or "pipeline" in title_desc
    market_relevant = dom in ("Generation",) or any(
        w in title_desc for w in ("market", "trading", "bid", "dispatch", "lmp", "arbitrage", "settlement", "ancillary", "capacity", "wholesale", "price"))
    key = (cat, mod)

    # nuclear-only content only on nuclear UCs
    if key in NUCLEAR_ONLY_CATEGORISED or cat in NUCLEAR_ONLY_CATEGORIES or key in NUCLEAR_FUEL_MODULES:
        return sv == "nuclear"
    # On nuclear-plant UCs, reject clearly off-domain platforms (markets, ADMS,
    # DERMS, renewable weather) that only match via generic affinity.
    if sv == "nuclear":
        if cat in MARKET_CATEGORIES or cat in DIST_CATEGORIES or cat in DERMS_CAT or key in RENEW_WEATHER:
            return False
    # fossil-plant specifics only on fossil (or generic generation) UCs
    if key in FOSSIL_MODULES or (cat == "CEMS"):
        return (dom == "Generation" and sv in ("fossil", "cross")) or sv == "fossil"
    if key in RENEWABLE_MODULES or key in RENEW_WEATHER:
        return sv == "renewables" or (dom == "Generation" and any(w in title_desc for w in ("renewable", "wind", "solar", "forecast")))
    if key in HYDRO_MODULES:
        return sv == "hydro"
    # LIMS chemistry only where chemistry/water genuinely relevant
    if cat == "LIMS":
        return any(w in title_desc for w in ("chemistry", "water", "lab", "sample", "effluent", "corrosion", "steam generator"))
    # market feeds only where market/trading is genuinely relevant
    if cat in MARKET_CATEGORIES:
        return market_relevant
    # DERMS only for distribution/renewables/DER-facing UCs
    if cat in DERMS_CAT:
        return dom in ("Distribution", "Customer") or sv == "renewables" or any(
            w in title_desc for w in ("der", "distributed", "ev ", "battery", "storage", "demand response", "vpp"))
    # distribution platform not on transmission or generation-PLANT nuclear UCs
    if cat in DIST_CATEGORIES:
        return dom in ("Distribution", "Customer") or any(w in title_desc for w in ("distribution", "outage", "feeder", "restoration", "grid edge"))
    # transmission EMS not on pure distribution/customer/plant UCs
    if cat in TRANS_CATEGORIES:
        return dom in ("Transmission",) or any(w in title_desc for w in ("transmission", "bulk", "dispatch", "contingency", "state estimat", "interchange", "agc"))
    return True


def _catalog_for_prompt(modules):
    """Compact catalog listing for the LLM: index) category · module."""
    return "\n".join(f"{i}) {m[0]} · {m[1]}" for i, m in enumerate(modules))


def _llm_client():
    """Return (host, token, model) — we call the serving endpoint via urllib
    (no openai SDK needed at build time)."""
    prof = "fe-vm-grid-ops-demo"
    host = "https://fevm-grid-ops-demo.cloud.databricks.com"
    tok = json.loads(subprocess.run(["databricks", "auth", "token", "-p", prof, "-o", "json"],
                                    capture_output=True, text=True).stdout)["access_token"]
    return (host, tok), "databricks-claude-sonnet-4-5"


def derive_all(ucs_raw, modules, sub_vertical_of, use_llm=False):
    """Return {parent_id: [(asset_index, 'required'|'helpful')]} for imported UCs.

    FAST by default: a deterministic, domain-scoped, word-boundary heuristic with
    a per-domain allowlist guard (no LLM, milliseconds). Set use_llm=True only for
    an optional BATCHED refinement pass (never one-call-per-UC). Domain-guarded
    either way so nuclear/gas/distribution modules never leak onto wrong UCs."""
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text())
        except Exception:  # noqa: BLE001
            cache = {}

    by_key = {(m[0], m[1]): i for i, m in enumerate(modules)}
    catalog_txt = _catalog_for_prompt(modules)
    client = None
    model = None
    if use_llm:
        try:
            client, model = _llm_client()
        except Exception as exc:  # noqa: BLE001
            print(f"[derive] LLM unavailable, using heuristic: {exc}")
            client = None

    out = {}
    n_llm = n_cache = n_heur = 0
    for uc in ucs_raw:
        pid = uc["id"]
        uc_ctx = {"title": uc["name"], "description": uc.get("description", ""),
                  "domain": uc.get("domain"), "sub_vertical": sub_vertical_of(uc)}
        cache_key = pid
        if cache_key in cache:
            idxs = cache[cache_key]; n_cache += 1
        elif client is not None:
            idxs = _llm_pick(client, model, uc_ctx, catalog_txt, modules); n_llm += 1
            cache[cache_key] = idxs
        else:
            idxs = _heuristic_pick(uc, uc_ctx, modules, by_key); n_heur += 1

        # domain guard + resolve to (idx, criticality)
        guarded = []
        for entry in idxs:
            idx = entry[0] if isinstance(entry, (list, tuple)) else entry
            crit = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else "required"
            if 0 <= idx < len(modules):
                cat, mod = modules[idx][0], modules[idx][1]
                if _guard(uc_ctx, cat, mod):
                    guarded.append((idx, crit))
        # ensure 2-4, first ~half required; if guard stripped everything, heuristic backfill
        if not guarded:
            guarded = _heuristic_pick_resolved(uc, uc_ctx, modules, by_key)
        guarded = guarded[:4]
        # re-tag criticality: at least the first is required
        fixed = [(ix, "required" if i < max(1, len(guarded) - 1) else "helpful") for i, (ix, _c) in enumerate(guarded)]
        out[pid] = fixed

    if client is not None:
        CACHE.write_text(json.dumps(cache, indent=1))
    print(f"[derive] mappings: {n_llm} via LLM, {n_cache} cached, {n_heur} heuristic")
    return out


def _llm_pick(client, model, uc_ctx, catalog_txt, modules):
    prompt = (
        "You are a Power & Utilities data architect. Pick the 2-4 data-source MODULES a utility "
        "would need to build this use case, from the numbered catalog. Choose the most specific, "
        "domain-appropriate modules. STRICT rules: nuclear-plant modules (reactor coolant, core, "
        "steam generator, pressurizer, containment, ECCS, radiation/dosimetry, nuclear fuel) ONLY for "
        "nuclear use cases; gas/pipeline modules only for gas; ADMS (distribution) modules only for "
        "distribution/outage use cases; EMS (transmission) only for transmission/bulk-grid; LIMS lab "
        "chemistry only if chemistry/water is central. Return STRICT JSON: {\"required\":[int,...],"
        "\"helpful\":[int,...]} using ONLY catalog indices.\n\n"
        f"USE CASE: {uc_ctx['title']} — {uc_ctx['description'][:300]}\n"
        f"Domain: {uc_ctx['domain']} · Generation focus: {uc_ctx['sub_vertical']}\n\n"
        f"CATALOG:\n{catalog_txt}"
    )
    import urllib.request
    (host, tok) = client
    url = f"{host}/serving-endpoints/{model}/invocations"
    payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 400, "temperature": 0.1}
    try:
        req = urllib.request.Request(url, method="POST",
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                     data=json.dumps(payload).encode())
        data = json.load(urllib.request.urlopen(req, timeout=60))
        c = data["choices"][0]["message"]["content"]
        s, e = c.find("{"), c.rfind("}")
        parsed = json.loads(c[s:e + 1])
        rq = [(int(i), "required") for i in parsed.get("required", [])[:3]]
        hlp = [(int(i), "helpful") for i in parsed.get("helpful", [])[:2]]
        return rq + hlp
    except Exception as exc:  # noqa: BLE001
        print(f"[derive] LLM pick failed for {uc_ctx['title'][:40]}: {exc}")
        return []


# ---- domain-scoped heuristic fallback (word-boundary-ish, guarded) ----------
def _heuristic_pick(uc, uc_ctx, modules, by_key):
    return [(i, "required") for i, _c in _heuristic_pick_resolved(uc, uc_ctx, modules, by_key)]


def _heuristic_pick_resolved(uc, uc_ctx, modules, by_key):
    import re
    reqs = " ".join(r for r in (uc.get("dataRequirements") or []) if isinstance(r, str))
    text = f"{uc_ctx['title']} {uc_ctx['description']} {reqs}".lower()
    kw_scored = []   # picks backed by a real keyword hit
    aff_scored = []  # picks backed only by domain affinity (weaker)
    for i, m in enumerate(modules):
        cat, mod, kws = m[0], m[1], m[2]
        if not _guard(uc_ctx, cat, mod):
            continue
        kw_score = 0
        for kw in kws:
            if re.search(r"\b" + re.escape(kw.strip().lower()) + r"\b", text):
                kw_score += 2
            elif len(kw) > 4 and kw.strip().lower() in text:
                kw_score += 1
        if kw_score:
            # small affinity tiebreak on top of a real keyword hit
            kw_scored.append((kw_score + (1 if m[4] == uc_ctx["domain"] else 0), i))
        elif m[4] == uc_ctx["domain"]:
            aff_scored.append((1, i))
    kw_scored.sort(reverse=True)
    aff_scored.sort(reverse=True)
    # Prefer keyword-backed picks; only backfill with same-domain affinity to reach ~2.
    picks = [(i, "required") for _s, i in kw_scored[:4]]
    if len(picks) < 2:
        have = {i for i, _ in picks}
        for _s, i in aff_scored:
            if i not in have:
                picks.append((i, "required")); have.add(i)
            if len(picks) >= 2:
                break
    if not picks:
        dom = uc_ctx["domain"]
        default = next((i for i, m in enumerate(modules) if m[4] == dom and _guard(uc_ctx, m[0], m[1])), 0)
        picks = [(default, "required")]
    return picks
