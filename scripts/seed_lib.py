"""Shared seed library for Grid Atlas.

Both seed_demo.py (populated walkthrough) and seed_clean.py (pristine day-1)
call load(cur, data, clean=...) inside a SINGLE transaction, so a failure never
leaves a half-populated DB. The only difference between modes:

  clean=True  -> reference library only: every data source not_started, every use
                 case not_started/manual with realized cleared, hypothesized value
                 models + reference edges + assumptions present; NO value_records,
                 roadmap_items, funding_requests, comments, or linked assets.
  clean=False -> the above PLUS in-flight demo state (realized values, roadmap,
                 funding, linked Databricks assets) for walkthroughs.

Phase is COMPUTED from prerequisite depth (see server/phase.py logic mirrored
here) rather than taken from the imported data, so it stays consistent.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# psycopg2 is only needed by the local seed CLI (get_conn/ensure_database). The
# running Databricks App reuses this module's PURE helpers (load_data,
# compute_phases, TABLES_FK_ORDER) via server/routes/demo.py and talks to
# Lakebase over asyncpg instead, so psycopg2 is intentionally optional there.
try:
    import psycopg2
except ImportError:  # pragma: no cover - app runtime has no psycopg2
    psycopg2 = None

HERE = Path(__file__).parent
ROOT = HERE.parent
MIGRATIONS_DIR = ROOT / "server" / "migrations"
MIGRATION = MIGRATIONS_DIR / "001_init.sql"
SEED_JSON = HERE / "seed_data.json"


def migration_sql() -> str:
    """Concatenate every migration in lexical order (001_, 002_, ...).

    All migrations are idempotent, so replaying the full set on each seed is
    both safe and the simplest way to keep a seeded DB current. Numbering the
    files is what guarantees 002's ALTER TABLEs land after 001 creates them.
    """
    return "\n".join(p.read_text() for p in sorted(MIGRATIONS_DIR.glob("*.sql")))


TABLES_FK_ORDER = (
    "audit_log", "funding_requests", "comments", "linked_databricks_assets",
    "roadmap_items", "value_records", "uc_enables_uc", "uc_requires_asset",
    "uc_requires_domain", "asset_serves_domain", "data_domains",
    "asset_taxonomy", "data_asset_aliases",
    "benchmark_library", "value_assumptions", "data_asset_lobs",
    "use_cases", "data_assets", "lobs",
)


def cli_json(args):
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"CLI failed: {' '.join(args)}\n{out.stderr}")
    return json.loads(out.stdout)


def get_conn(profile, project, branch, endpoint, database):
    branch_path = f"projects/{project}/branches/{branch}"
    endpoint_path = f"{branch_path}/endpoints/{endpoint}"
    endpoints = cli_json(["databricks", "postgres", "list-endpoints", branch_path,
                          "-p", profile, "-o", "json"])
    host = endpoints[0]["status"]["hosts"]["host"]
    token = cli_json(["databricks", "postgres", "generate-database-credential",
                      endpoint_path, "-p", profile, "-o", "json"])["token"]
    email = cli_json(["databricks", "current-user", "me", "-p", profile, "-o", "json"])["userName"]
    print(f"[seed] host={host} db={database} user={email}")
    return psycopg2.connect(host=host, port=5432, dbname=database, user=email,
                            password=token, sslmode="require")


def ensure_database(profile, project, branch, endpoint, database):
    conn = get_conn(profile, project, branch, endpoint, "postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{database}"')
        print(f"[seed] created database '{database}'")
    cur.close()
    conn.close()


def parse_args(defaults_project="grid-atlas-db", defaults_profile="fe-vm-grid-ops-demo"):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=defaults_profile)
    ap.add_argument("--project", default=defaults_project)
    ap.add_argument("--branch", default="production")
    ap.add_argument("--endpoint", default="primary")
    ap.add_argument("--db", default="app")
    return ap.parse_args()


def load_data():
    if not SEED_JSON.exists():
        print("seed_data.json missing - run build_seed_from_parent.py first", file=sys.stderr)
        sys.exit(1)
    return json.loads(SEED_JSON.read_text())


def compute_phases(enables_by_parent_pairs, uc_ids):
    """Phase from longest upstream prerequisite chain depth (mirrors server/phase.py).
    enables pairs are (from_parent, to_parent) meaning from --enables--> to, i.e.
    'from' is a prerequisite of 'to'. Depth of a UC = longest chain of prereqs.
    Mapping: depth 0 -> 1, depth 1 -> 2, depth >=2 -> 3 (capped at 3)."""
    upstream = {}
    for (frm, to) in enables_by_parent_pairs:
        if frm != to:
            upstream.setdefault(to, []).append(frm)
    memo = {}

    def depth(u, stack):
        if u in memo:
            return memo[u]
        if u in stack:  # cycle guard
            return 0
        stack.add(u)
        preds = upstream.get(u, [])
        d = 0 if not preds else 1 + max((depth(p, stack) for p in preds), default=0)
        stack.discard(u)
        memo[u] = d
        return d

    out = {}
    for u in uc_ids:
        d = depth(u, set())
        out[u] = 1 if d == 0 else 2 if d == 1 else 3
    return out


def load_domains(cur, asset_ids_by_module: dict, uc_ids_by_module_reqs: dict):
    """Seed the semantic data-domain layer (see migrations/002_domains.sql).

    Three steps:
      1. Insert the DOMAINS vocabulary (origin='catalog').
      2. Insert asset_serves_domain from the hand-authored SERVES mapping.
      3. DERIVE uc_requires_domain from the 693 existing module edges: if a use
         case requires module M, and M serves domain D, then the use case
         requires D. Necessity is carried over from the module edge's
         criticality, taking the strongest ('required' beats 'helpful') when
         several modules map onto the same domain.

    Step 3 is what makes the domain layer immediately useful instead of empty on
    day one — the whole 240-use-case portfolio gets domain requirements without
    anyone re-authoring them, and readiness.py's domain path lights up at once.

    Args:
        asset_ids_by_module: (source_category, module) -> data_assets.id
        uc_ids_by_module_reqs: use_case_id -> [(asset_id, criticality), ...]
    """
    from pu_domains import DOMAINS, SERVES

    # 1) vocabulary
    domain_id_by_name = {}
    for name, label, category, description, examples in DOMAINS:
        cur.execute(
            """INSERT INTO data_domains
               (name, label, description, category, example_attributes,
                is_active, origin, is_user_edited)
               VALUES (%s,%s,%s,%s,%s,true,'catalog',false)
               ON CONFLICT (name) DO UPDATE SET
                   label=EXCLUDED.label, description=EXCLUDED.description,
                   category=EXCLUDED.category,
                   example_attributes=EXCLUDED.example_attributes,
                   updated_at=now()
               RETURNING id""",
            (name, label, description, category, examples))
        domain_id_by_name[name] = cur.fetchone()[0]

    # 2) asset -> domain. A pair naming a module that isn't in this install's
    # catalog is skipped rather than fatal: a customer may prune the catalog.
    domains_by_asset: dict[int, set] = {}
    n_serves = 0
    for domain_name, pairs in SERVES.items():
        did = domain_id_by_name.get(domain_name)
        if did is None:
            continue
        for pair in pairs:
            aid = asset_ids_by_module.get(pair)
            if aid is None:
                continue
            cur.execute(
                """INSERT INTO asset_serves_domain
                   (data_asset_id, domain_id, confidence, mapped_by, is_user_edited)
                   VALUES (%s,%s,'high','catalog',false)
                   ON CONFLICT (data_asset_id, domain_id) DO NOTHING""",
                (aid, did))
            domains_by_asset.setdefault(aid, set()).add(did)
            n_serves += 1

    # 3) derive use_case -> domain from the module edges.
    n_reqs = 0
    for uc_id, module_reqs in uc_ids_by_module_reqs.items():
        # domain_id -> strongest necessity seen across the contributing modules
        necessity_by_domain: dict[int, str] = {}
        for asset_id, criticality in module_reqs:
            for did in domains_by_asset.get(asset_id, ()):
                if necessity_by_domain.get(did) == "required":
                    continue  # already the strongest; nothing to upgrade
                necessity_by_domain[did] = criticality
        for did, necessity in necessity_by_domain.items():
            cur.execute(
                """INSERT INTO uc_requires_domain
                   (use_case_id, domain_id, necessity, rationale, mapped_by, manual)
                   VALUES (%s,%s,%s,%s,'catalog',false)
                   ON CONFLICT (use_case_id, domain_id) DO NOTHING""",
                (uc_id, did, necessity,
                 "Derived from the reference catalog's module-level requirements."))
            n_reqs += 1

    return {"domains": len(domain_id_by_name), "asset_serves_domain": n_serves,
            "uc_requires_domain": n_reqs}


def load(cur, data, clean: bool):
    """Load the portfolio into an open cursor's transaction. Idempotent
    (truncates first). Caller commits."""
    cur.execute(migration_sql())
    for t in TABLES_FK_ORDER:
        cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")

    # 1) LOBs
    lob_id = {}
    for name, desc in data["lobs"]:
        cur.execute("INSERT INTO lobs (name, description) VALUES (%s,%s) RETURNING id", (name, desc))
        lob_id[name] = cur.fetchone()[0]

    # 2) data assets — in clean mode force not_started
    asset_id_by_index = {}
    asset_id_by_module = {}   # (source_category, module) -> id, for domain mapping
    for idx, a in enumerate(data["data_assets"]):
        status = "not_started" if clean else a["ingestion_status"]
        cur.execute(
            """INSERT INTO data_assets
               (source_category, vendor, source_system, module, description, sub_vertical,
                ingestion_status, ingest_effort, ingest_cost_low, ingest_cost_high,
                uc_catalog, uc_schema, owning_lob_id, origin, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (a.get("source_category"), None, a["source_system"], a["module"],  # vendor blank by default (customer-added metadata)
             a["description"], a.get("sub_vertical") or "cross", status,
             a.get("ingest_effort"), a.get("ingest_cost_low"), a.get("ingest_cost_high"),
             a["uc_catalog"], a["uc_schema"], lob_id.get(a["owning_lob"]),
             a.get("origin", "catalog"), "seed"))
        aid = cur.fetchone()[0]
        asset_id_by_index[idx] = aid
        # (source_category, module) is the key the domain mapping in pu_domains
        # joins on. Fall back to source_system for rows predating the
        # source_category split.
        asset_id_by_module[(a.get("source_category") or a["source_system"], a["module"])] = aid
        for lob in a["benefiting_lobs"]:
            if lob in lob_id:
                cur.execute("INSERT INTO data_asset_lobs (data_asset_id, lob_id) VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING", (aid, lob_id[lob]))

    # compute phase from enables edges (parent-id space) up front
    uc_parent_ids = [u["parent_id"] for u in data["use_cases"]]
    phase_by_parent = compute_phases(data["enables"], uc_parent_ids)

    # 3) use cases.
    #   CLEAN (shipped default): ALL use cases are loaded INTO the portfolio
    #     (origin='catalog', in_portfolio=true) but PRISTINE — status not_started,
    #     no realized value, no roadmap/funding. A first-time user sees the full
    #     portfolio + full data-asset catalog pre-loaded, ready to work.
    #   DEMO: a realistic subset carries in-flight statuses/value, a few marked
    #     origin='custom' (customer's own additions); the rest stay catalog-only.
    demo_portfolio_parents: set = set()
    demo_custom_parents: set = set()
    if not clean:
        # Pick the in-flight use cases (any non-not_started status), then cap to a
        # curated ~35 by hypothesized value so the demo portfolio reads realistically.
        def _hyp_mid(u):
            comps = (u.get("hypothesized_value_json") or {}).get("components") or []
            return sum((c.get("multiplier") or 0) for c in comps)
        in_flight = [u for u in data["use_cases"] if u["status"] != "not_started"]
        in_flight.sort(key=_hyp_mid, reverse=True)
        chosen = in_flight[:35]
        demo_portfolio_parents = {u["parent_id"] for u in chosen}
        # Flag the 5 highest-value chosen as customer-authored ("their own").
        demo_custom_parents = {u["parent_id"] for u in chosen[:5]}

    uc_id_by_parent = {}
    for u in data["use_cases"]:
        # CLEAN: everything is in the portfolio (pristine). DEMO: only the curated
        # in-flight subset is in the portfolio; the rest stay catalog-only.
        in_portfolio = clean or (u["parent_id"] in demo_portfolio_parents)
        origin = "custom" if u["parent_id"] in demo_custom_parents else "catalog"
        # In clean mode every row is pristine (not_started, no realized value) even
        # though it is in the portfolio. In demo mode a catalog idea only carries its
        # in-flight status/value once it's in the portfolio subset.
        carries_inflight = (not clean) and in_portfolio
        status = u["status"] if carries_inflight else "not_started"
        phase = phase_by_parent.get(u["parent_id"], 1)
        realized_amt = u["realized_value_amount"] if carries_inflight else None
        realized_json = (json.dumps(u["realized_value_json"])
                         if (carries_inflight and u.get("realized_value_json")) else None)
        cur.execute(
            """INSERT INTO use_cases
               (title, description, lob_id, sub_vertical, stage, phase, status, category,
                effort_tshirt, priority_score, risk_tags, compliance_tags,
                hypothesized_value_json, realized_value_amount, realized_value_json,
                status_source, created_by, origin, in_portfolio)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (u["title"], u["description"], lob_id.get(u["lob"]), u["sub_vertical"],
             u["stage"], phase, status, None, u["effort_tshirt"],  # category NULLed — was the parent's stale phase name; phase name is now derived
             u["priority_score"], u["risk_tags"], u["compliance_tags"],
             json.dumps(u["hypothesized_value_json"]), realized_amt, realized_json,
             "manual", "seed", origin, in_portfolio))
        uc_id_by_parent[u["parent_id"]] = cur.fetchone()[0]

    # 4) requires edges
    n_req = 0
    module_reqs_by_uc: dict[int, list] = {}   # for deriving domain requirements
    for (parent_id, aidx, crit) in data["requires"]:
        if parent_id in uc_id_by_parent and aidx in asset_id_by_index:
            uc_id = uc_id_by_parent[parent_id]
            asset_id = asset_id_by_index[aidx]
            cur.execute("INSERT INTO uc_requires_asset (use_case_id, data_asset_id, criticality) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (uc_id, asset_id, crit))
            module_reqs_by_uc.setdefault(uc_id, []).append((asset_id, crit))
            n_req += 1

    # 5) enables edges (reference; detected_by_agent=false)
    n_ena = 0
    for (frm, to) in data["enables"]:
        if frm in uc_id_by_parent and to in uc_id_by_parent and frm != to:
            cur.execute(
                "INSERT INTO uc_enables_uc (from_use_case_id, to_use_case_id, detected_by_agent, rationale) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (uc_id_by_parent[frm], uc_id_by_parent[to], False,
                 "Prerequisite: completing the upstream use case lands data that accelerates this one."))
            n_ena += 1

    # 6) value assumptions (both modes)
    for a in data["value_assumptions"]:
        cur.execute(
            """INSERT INTO value_assumptions (key, label, value, unit, category)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""",
            (a["key"], a["label"], a["value"], a["unit"], a["category"]))

    # 7) benchmark library (reference; both modes) — if present in data
    for b in data.get("benchmarks", []):
        cur.execute(
            """INSERT INTO benchmark_library (use_case_pattern, sub_vertical, metric_type, low, mid, high, unit, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b.get("use_case_pattern"), b.get("sub_vertical"), b.get("metric_type"),
             b.get("low"), b.get("mid"), b.get("high"), b.get("unit"), b.get("notes")))

    # 8) semantic data-domain layer (both modes — it is reference content, and
    # readiness reads it through the same curated/governed rule either way).
    domain_counts = load_domains(cur, asset_id_by_module, module_reqs_by_uc)

    counts = {"lobs": len(lob_id), "assets": len(asset_id_by_index),
              "use_cases": len(uc_id_by_parent), "requires": n_req, "enables": n_ena,
              "value_records": 0, "roadmap": 0, "funding": 0, **domain_counts}

    if clean:
        return counts, uc_id_by_parent, asset_id_by_index, lob_id, phase_by_parent

    # ---- DEMO-ONLY in-flight state (portfolio use cases only) ----
    n_val = n_road = n_fund = 0
    for u in data["use_cases"]:
        # Only the confirmed PORTFOLIO subset carries value records + roadmap items;
        # catalog-only ideas stay pristine until a customer brings them in.
        if u["parent_id"] not in demo_portfolio_parents:
            continue
        ucid = uc_id_by_parent[u["parent_id"]]
        if u["value_mid_mm"]:
            cur.execute(
                """INSERT INTO value_records (use_case_id, kind, metric_type, amount, unit, fiscal_period, confidence, created_by)
                   VALUES (%s,'hypothesized','annual value (mid)',%s,'USD_M','FY27','med','seed')""",
                (ucid, u["value_mid_mm"]))
            n_val += 1
        if u["realized_value_amount"]:
            cur.execute(
                """INSERT INTO value_records (use_case_id, kind, metric_type, amount, unit, fiscal_period, confidence, created_by)
                   VALUES (%s,'realized','realized annual value',%s,'USD','FY26','high','seed')""",
                (ucid, u["realized_value_amount"]))
            n_val += 1
        ph = phase_by_parent.get(u["parent_id"], 1)
        horizon = {1: "now", 2: "next", 3: "later"}.get(ph, "later")
        cur.execute("INSERT INTO roadmap_items (use_case_id, horizon, wave, notes) VALUES (%s,%s,%s,%s)",
                    (ucid, horizon, ph, f"Phase {ph} · {u.get('time_to_value') or ''}"))
        n_road += 1

    for idx, a in enumerate(data["data_assets"]):
        if len(a["benefiting_lobs"]) >= 2 and n_fund < 3:
            req_lob = a["benefiting_lobs"][0]
            co = [lob_id[l] for l in a["benefiting_lobs"][1:] if l in lob_id]
            cur.execute(
                """INSERT INTO funding_requests (data_asset_id, requesting_lob_id, co_funding_lobs, combined_value, status)
                   VALUES (%s,%s,%s,%s,'proposed')""",
                (asset_id_by_index[idx], lob_id[req_lob], co, 4_000_000 + n_fund * 1_500_000))
            n_fund += 1

    live_ucs = [uc_id_by_parent[u["parent_id"]] for u in data["use_cases"]
                if u["parent_id"] in demo_portfolio_parents
                and u["status"] in ("live", "value_realized", "in_progress")][:5]
    demo_jobs = [("job", "181028058559804", "CDC Fleet Intel - Live Simulator"),
                 ("pipeline", "388888035762457", "CDC Fleet Intel - Generate Synthetic Data")]
    for i, ucid in enumerate(live_ucs[:2]):
        at, aid, an = demo_jobs[i % len(demo_jobs)]
        cur.execute("INSERT INTO linked_databricks_assets (use_case_id, asset_type, asset_id, asset_name) "
                    "VALUES (%s,%s,%s,%s)", (ucid, at, aid, an))

    counts.update({"value_records": n_val, "roadmap": n_road, "funding": n_fund})
    return counts, uc_id_by_parent, asset_id_by_index, lob_id, phase_by_parent


def print_verification(cur):
    for t in ("lobs", "data_assets", "use_cases", "uc_requires_asset", "uc_enables_uc",
              "value_assumptions", "value_records", "roadmap_items", "funding_requests",
              "data_domains", "asset_serves_domain", "uc_requires_domain"):
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"    {t:22s} {cur.fetchone()[0]}")
    cur.execute("SELECT origin, in_portfolio, count(*) FROM use_cases GROUP BY 1,2 ORDER BY 1,2")
    for origin, inp, n in cur.fetchall():
        print(f"    use_cases[{origin},in_portfolio={inp}] {n}")
    # Domain-layer coverage: a use case with zero required domains silently falls
    # back to the module path, so surface that count rather than letting it hide.
    cur.execute("""
        SELECT count(*) FROM use_cases uc
        WHERE NOT EXISTS (SELECT 1 FROM uc_requires_domain urd
                          WHERE urd.use_case_id = uc.id AND urd.necessity = 'required')
    """)
    print(f"    use_cases on MODULE path  {cur.fetchone()[0]}")
    cur.execute("""
        SELECT count(*) FROM data_domains dd
        WHERE NOT EXISTS (SELECT 1 FROM asset_serves_domain asd WHERE asd.domain_id = dd.id)
    """)
    print(f"    domains with no source    {cur.fetchone()[0]}")


def run(clean: bool):
    args = parse_args()
    data = load_data()
    ensure_database(args.profile, args.project, args.branch, args.endpoint, args.db)
    conn = get_conn(args.profile, args.project, args.branch, args.endpoint, args.db)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        counts, *_ = load(cur, data, clean=clean)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    mode = "CLEAN day-1" if clean else "FULL DEMO"
    print(f"[seed] {mode}: {counts}")
    print_verification(cur)
    cur.close()
    conn.close()
    print(f"[seed] {mode} state applied.")
