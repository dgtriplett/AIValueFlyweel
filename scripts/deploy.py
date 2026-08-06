#!/usr/bin/env python3
"""One-shot deploy for Grid Atlas.

    python3 scripts/deploy.py                 # interactive
    python3 scripts/deploy.py --yes ...        # scripted / CI

Does everything an install needs, in dependency order:

  1. Check prerequisites (CLI version, auth, Node when building).
  2. Collect settings, defaulting to whatever you used last time.
  3. Rewrite app.yaml + databricks.yml so the two stay in sync.
  4. Build the React SPA (skippable — the built dist/ is committed).
  5. `databricks bundle deploy`, creating the app.
  6. Resolve the app's auto-created service principal and run the Unity Catalog
     GRANTs it needs.
  7. Seed Lakebase with the reference library.
  8. Start the app and print its URL.

DESIGN NOTES
------------
Idempotent: re-running is safe, and every prompt pre-fills with your last answer
(cached in .deploy-cache.json, which is gitignored).

Fails loudly and early. Each step checks its own prerequisites and stops with an
actionable message rather than letting a later step fail obscurely — a missing
GRANT surfacing as a 403 inside the running app is exactly the experience this
script exists to prevent.

`--dry-run` prints every command without running any, which is the honest way to
review what a script is about to do to your workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
CACHE_FILE = ROOT / ".deploy-cache.json"

APP_NAME = "grid-atlas"
BUNDLE_RESOURCE = "grid_atlas"
MIN_CLI_VERSION = (0, 239)

# ANSI, disabled when not a TTY or when NO_COLOR is set (respecting no-color.org).
_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t): return _c("1", t)
def red(t): return _c("31", t)
def green(t): return _c("32", t)
def yellow(t): return _c("33", t)
def dim(t): return _c("2", t)


def step(n: int, total: int, title: str) -> None:
    print(f"\n{bold(f'[{n}/{total}]')} {bold(title)}")


def fail(message: str, hint: str | None = None) -> "None":
    print(f"\n{red('FAILED')} {message}", file=sys.stderr)
    if hint:
        print(f"        {hint}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
DRY_RUN = False


def run(args: list[str], *, capture: bool = False, check: bool = True,
        cwd: Path | None = None, quiet: bool = False) -> subprocess.CompletedProcess:
    printable = " ".join(args)
    if DRY_RUN:
        print(f"  {dim('would run:')} {printable}")
        return subprocess.CompletedProcess(args, 0, "", "")
    if not quiet:
        print(f"  {dim('$')} {dim(printable)}")
    result = subprocess.run(
        args, capture_output=capture, text=True, cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"command failed: {printable}", detail[:900] if detail else None)
    return result


def run_json(args: list[str], *, check: bool = True):
    result = run(args, capture=True, check=check, quiet=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Prompts + cache
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(values: dict) -> None:
    if DRY_RUN:
        return
    try:
        CACHE_FILE.write_text(json.dumps(values, indent=2) + "\n")
    except OSError:
        pass  # a cache we can't write is a convenience lost, not an error


def ask(prompt: str, default: str | None, *, assume_yes: bool,
        required: bool = True) -> str:
    if assume_yes:
        if required and not default:
            fail(f"--yes was given but {prompt!r} has no value.",
                 "Pass it as a flag, or run without --yes to be prompted.")
        return default or ""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"  {prompt}{suffix}: ").strip() or (default or "")
        if answer or not required:
            return answer
        print(f"  {yellow('This one is required.')}")


def confirm(prompt: str, *, assume_yes: bool, default: bool = True) -> bool:
    if assume_yes:
        return True
    hint = "Y/n" if default else "y/N"
    answer = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Step 1 — prerequisites
# ---------------------------------------------------------------------------
def check_cli_version() -> None:
    if not shutil.which("databricks"):
        fail("the Databricks CLI is not on PATH.",
             "Install it: https://docs.databricks.com/dev-tools/cli/install.html")
    # `capture=True` is required for stdout; in a dry run `run` returns a stub
    # with empty output, so skip the check rather than warn misleadingly.
    if DRY_RUN:
        print(f"  {dim('skipping the CLI version check (dry run)')}")
        return
    result = run(["databricks", "--version"], capture=True, check=False, quiet=True)
    # Output is like "Databricks CLI v0.298.0" — anchor on the v-prefixed semver
    # so a version embedded elsewhere in the banner can't be picked up instead.
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout or "")
    if not match:
        print(f"  {yellow('warning:')} could not parse the CLI version; continuing.")
        return
    version = (int(match.group(1)), int(match.group(2)))
    if version < MIN_CLI_VERSION:
        fail(f"Databricks CLI {version[0]}.{version[1]} is too old "
             f"(need >= {MIN_CLI_VERSION[0]}.{MIN_CLI_VERSION[1]}).",
             "Upgrade: brew upgrade databricks  (or re-run the installer)")
    print(f"  {green('ok')} Databricks CLI {version[0]}.{version[1]}")


def check_auth(profile: str) -> dict:
    me = run_json(["databricks", "current-user", "me", "-p", profile, "-o", "json"],
                  check=False)
    if not me:
        # A dry run is for reviewing what WOULD happen, so an unauthenticated
        # profile is a warning there rather than a hard stop — otherwise you have
        # to log in before you can read the plan.
        if DRY_RUN:
            print(f"  {yellow('warning:')} profile {profile!r} is not authenticated "
                  "(fine for a dry run; log in before deploying for real).")
            return {}
        fail(f"profile {profile!r} is not authenticated.",
             f"Run: databricks auth login --profile {profile}")
    user = me.get("userName") or me.get("displayName") or "unknown"
    print(f"  {green('ok')} authenticated as {user}")
    return me


# ---------------------------------------------------------------------------
# Step 3 — write config
# ---------------------------------------------------------------------------
def set_yaml_env(text: str, name: str, value: str) -> str:
    """Set an `env:` entry's `value:` in app.yaml, leaving comments intact.

    A targeted regex rather than a YAML round-trip: app.yaml's comments explain
    what each setting is for, and every Python YAML emitter would discard them.
    Anchored on the specific `- name: X` block so it can't touch anything else.
    """
    pattern = re.compile(
        rf"(-\s*name:\s*{re.escape(name)}\s*\n(?:\s*#[^\n]*\n)*\s*value:\s*)(\"[^\"]*\"|'[^']*'|[^\n]*)",
        re.MULTILINE)
    replacement = rf'\g<1>"{value}"'
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    print(f"  {yellow('warning:')} {name} not found in app.yaml — leaving as-is.")
    return text



def write_config(settings: dict) -> None:
    app_yaml = ROOT / "app.yaml"
    text = app_yaml.read_text()
    for name, key in (
        ("PGHOST", "pghost"), ("PGUSER", "pguser"), ("PGDATABASE", "pgdatabase"),
        ("SERVING_ENDPOINT", "serving_endpoint"),
        ("ATLAS_CATALOG", "atlas_catalog"), ("ATLAS_SCHEMA", "atlas_schema"),
        ("GENIE_MIRROR_CATALOG", "genie_mirror_catalog"),
        ("GENIE_MIRROR_SCHEMA", "genie_mirror_schema"),
        ("GENIE_SPACE_ID", "genie_space_id"), ("DEMO_MODE", "demo_mode"),
    ):
        if settings.get(key) is not None:
            text = set_yaml_env(text, name, str(settings[key]))
    # The warehouse id lives under the resource block, not env.
    text = re.sub(r"(sql_warehouse:\s*\n\s*id:\s*)(\"[^\"]*\"|'[^']*'|[^\n]*)",
                  rf'\g<1>"{settings["warehouse_id"]}"', text, count=1)
    if not DRY_RUN:
        app_yaml.write_text(text)
    print(f"  {green('ok')} app.yaml updated")

    bundle = ROOT / "databricks.yml"
    btext = bundle.read_text()
    for key, value in (("atlas_catalog", settings["atlas_catalog"]),
                       ("atlas_schema", settings["atlas_schema"]),
                       ("serving_endpoint", settings["serving_endpoint"]),
                       ("genie_mirror_catalog", settings["genie_mirror_catalog"]),
                       ("genie_mirror_schema", settings["genie_mirror_schema"]),
                       ("lakebase_project", settings["lakebase_project"]),
                       ("demo_mode", settings["demo_mode"])):
        # Each variable's `default:` is nested under its own key, so anchor on the
        # variable name and replace the default line that follows it.
        btext = re.sub(
            rf"(  {re.escape(key)}:\n(?:    [^\n]*\n)*?    default:\s*)([^\n]*)",
            rf"\g<1>{value}", btext, count=1)
    if not DRY_RUN:
        bundle.write_text(btext)
    print(f"  {green('ok')} databricks.yml updated")


# ---------------------------------------------------------------------------
# Step 4 — frontend
# ---------------------------------------------------------------------------
def build_frontend(skip: bool) -> None:
    dist = ROOT / "frontend" / "dist"
    src = ROOT / "frontend" / "src"
    if skip:
        print(f"  {dim('skipped (--skip-build)')}")
        return
    if not src.exists():
        # The built bundle is committed precisely because the TS source isn't in
        # this repo; building is impossible and unnecessary.
        print(f"  {dim('no frontend/src — serving the committed dist/ bundle')}")
        if not dist.exists():
            fail("neither frontend/src nor frontend/dist exists.",
                 "The app has no UI to serve. Restore frontend/dist from git.")
        return
    if not shutil.which("npm"):
        fail("npm is not on PATH but frontend/src exists.",
             "Install Node 18+, or pass --skip-build to serve the committed dist/.")
    frontend = ROOT / "frontend"
    run(["npm", "ci"], cwd=frontend)
    run(["npm", "run", "build"], cwd=frontend)
    print(f"  {green('ok')} frontend built")


# ---------------------------------------------------------------------------
# Step 5/6 — deploy + grants
# ---------------------------------------------------------------------------
def bundle_deploy(profile: str, target: str, settings: dict) -> None:
    run(["databricks", "bundle", "deploy", "-t", target, "-p", profile,
         f"--var=warehouse_id={settings['warehouse_id']}",
         f"--var=atlas_catalog={settings['atlas_catalog']}",
         f"--var=atlas_schema={settings['atlas_schema']}",
         f"--var=serving_endpoint={settings['serving_endpoint']}",
         f"--var=genie_mirror_catalog={settings['genie_mirror_catalog']}",
         f"--var=genie_mirror_schema={settings['genie_mirror_schema']}",
         f"--var=lakebase_project={settings['lakebase_project']}",
         f"--var=demo_mode={settings['demo_mode']}"])
    print(f"  {green('ok')} bundle deployed")


def resolve_app(profile: str, app_name: str) -> dict | None:
    """Find the deployed app and its auto-created service principal."""
    app = run_json(["databricks", "apps", "get", app_name, "-p", profile, "-o", "json"],
                   check=False)
    if not app:
        return None
    return app


def app_service_principal(app: dict) -> str | None:
    """Pull the SP identifier out of an `apps get` payload.

    The field has moved between CLI versions, so check the known spellings rather
    than pinning one and breaking on upgrade.
    """
    for key in ("service_principal_client_id", "service_principal_id",
                "service_principal_name"):
        value = app.get(key)
        if value:
            return str(value)
    return None


def run_grants(profile: str, warehouse_id: str, settings: dict, sp: str) -> None:
    """Grant the app's service principal what it needs in Unity Catalog.

    Only the discovery and Genie-mirror schemas: those are the two places the app
    writes. Warehouse CAN_USE and endpoint CAN_QUERY come from the bundle's
    resource bindings, so they are not repeated here.
    """
    statements = []
    for catalog, schema in (
        (settings["atlas_catalog"], settings["atlas_schema"]),
        (settings["genie_mirror_catalog"], settings["genie_mirror_schema"]),
    ):
        if not catalog:
            continue
        statements += [
            f"GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `{catalog}` TO `{sp}`",
            f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
            f"GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE "
            f"ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
        ]
    # Optional: enables auto-detection of landed sources from lineage.
    statements.append(f"GRANT SELECT ON SCHEMA system.access TO `{sp}`")

    failures = []
    for statement in statements:
        result = run(["databricks", "api", "post", "/api/2.0/sql/statements",
                      "-p", profile, "--json",
                      json.dumps({"warehouse_id": warehouse_id,
                                  "statement": statement, "wait_timeout": "30s"})],
                     capture=True, check=False, quiet=True)
        ok = result.returncode == 0 and '"state":"SUCCEEDED"' in (
            result.stdout or "").replace(" ", "")
        if ok:
            print(f"  {green('ok')} {statement[:78]}")
        else:
            failures.append(statement)
            print(f"  {yellow('skipped')} {statement[:78]}")

    if failures:
        print(f"\n  {yellow('Some GRANTs did not apply.')} You are probably not a "
              "metastore admin.")
        print("  Hand these to someone who is (the app's Setup page shows them too):\n")
        for statement in failures:
            print(f"    {statement};")


# ---------------------------------------------------------------------------
# Step 7 — Lakebase
# ---------------------------------------------------------------------------
def lakebase_host(profile: str, project: str, branch: str = "production",
                  endpoint: str = "primary") -> str | None:
    endpoints = run_json(
        ["databricks", "postgres", "list-endpoints",
         f"projects/{project}/branches/{branch}", "-p", profile, "-o", "json"],
        check=False)
    if not endpoints:
        return None
    try:
        return endpoints[0]["status"]["hosts"]["host"]
    except (KeyError, IndexError, TypeError):
        return None


def seed(profile: str, project: str, database: str, demo: bool) -> None:
    script = "seed_demo.py" if demo else "seed_clean.py"
    if not (HERE / script).exists():
        fail(f"scripts/{script} is missing.")
    run([sys.executable, str(HERE / script), "--profile", profile,
         "--project", project, "--db", database])
    print(f"  {green('ok')} Lakebase seeded ({'demo' if demo else 'clean day-1'})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global DRY_RUN

    parser = argparse.ArgumentParser(
        description="Deploy Grid Atlas into a Databricks workspace.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true",
                        help="non-interactive; use flags and cached values")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every command without running anything")
    parser.add_argument("--target", default=None, help="bundle target (dev/prod)")
    parser.add_argument("--profile", default=None, help="Databricks CLI profile")
    parser.add_argument("--warehouse-id", default=None)
    parser.add_argument("--atlas-catalog", default=None,
                        help="catalog for the discovery layer")
    parser.add_argument("--atlas-schema", default=None)
    parser.add_argument("--serving-endpoint", default=None)
    parser.add_argument("--genie-mirror-catalog", default=None)
    parser.add_argument("--genie-mirror-schema", default=None)
    parser.add_argument("--lakebase-project", default=None)
    parser.add_argument("--lakebase-host", default=None,
                        help="Lakebase endpoint host (auto-detected if omitted)")
    parser.add_argument("--pg-database", default=None)
    parser.add_argument("--demo-mode", choices=["on", "off"], default=None,
                        help="ship the removable Demo Mode toggle (default off)")
    parser.add_argument("--skip-build", action="store_true",
                        help="don't rebuild the frontend")
    parser.add_argument("--skip-grants", action="store_true",
                        help="don't run the Unity Catalog GRANTs")
    parser.add_argument("--skip-seed", action="store_true",
                        help="don't seed Lakebase")
    parser.add_argument("--seed-demo", action="store_true",
                        help="seed the populated walkthrough state instead of clean day-1")
    parser.add_argument("--skip-start", action="store_true",
                        help="deploy without starting the app")
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    cache = load_cache()
    total = 8

    print(bold("\nGrid Atlas — deploy"))
    if DRY_RUN:
        print(yellow("dry run: nothing will be changed"))

    # -- 1. prerequisites --
    step(1, total, "Prerequisites")
    check_cli_version()

    # -- 2. settings --
    step(2, total, "Settings")

    def value(flag, key, prompt, default=None, required=True):
        supplied = getattr(args, flag, None)
        if supplied:
            return supplied
        return ask(prompt, cache.get(key, default), assume_yes=args.yes,
                   required=required)

    profile = value("profile", "profile", "Databricks CLI profile", "DEFAULT")
    me = check_auth(profile)
    target = value("target", "target", "Bundle target (dev/prod)", "prod")
    warehouse_id = value("warehouse_id", "warehouse_id", "SQL warehouse ID")
    atlas_catalog = value("atlas_catalog", "atlas_catalog",
                          "Unity Catalog for discovery (blank to disable)",
                          "main", required=False)
    atlas_schema = value("atlas_schema", "atlas_schema", "Discovery schema",
                         "grid_atlas_discovery", required=False)
    serving_endpoint = value("serving_endpoint", "serving_endpoint",
                             "Foundation Model endpoint",
                             "databricks-claude-sonnet-4-5")
    genie_mirror_catalog = value("genie_mirror_catalog", "genie_mirror_catalog",
                                 "Catalog for the Genie mirror",
                                 atlas_catalog or "main", required=False)
    genie_mirror_schema = value("genie_mirror_schema", "genie_mirror_schema",
                                "Schema for the Genie mirror", "grid_atlas",
                                required=False)
    lakebase_project = value("lakebase_project", "lakebase_project",
                             "Lakebase project id", "grid-atlas-db")
    pg_database = value("pg_database", "pg_database", "Lakebase database", "app")
    demo_mode = args.demo_mode or cache.get("demo_mode", "off")

    settings = {
        "profile": profile, "target": target, "warehouse_id": warehouse_id,
        "atlas_catalog": atlas_catalog, "atlas_schema": atlas_schema,
        "serving_endpoint": serving_endpoint,
        "genie_mirror_catalog": genie_mirror_catalog,
        "genie_mirror_schema": genie_mirror_schema,
        "lakebase_project": lakebase_project, "pg_database": pg_database,
        "genie_space_id": cache.get("genie_space_id", ""),
        "demo_mode": demo_mode,
    }

    # Lakebase host: auto-detect so the operator doesn't have to look it up.
    host = args.lakebase_host or cache.get("pghost", "")
    if not host and not DRY_RUN:
        print(f"  {dim('resolving the Lakebase endpoint host...')}")
        host = lakebase_host(profile, lakebase_project) or ""
        if host:
            print(f"  {green('ok')} {host}")
        else:
            print(f"  {yellow('could not auto-detect')} — provision the Lakebase "
                  f"project {lakebase_project!r} first (see INSTALL.md step 1).")
            host = ask("Lakebase endpoint host", None, assume_yes=args.yes,
                       required=False)
    settings["pghost"] = host
    settings["pgdatabase"] = pg_database
    # PGUSER is the app's service principal client id, known only after the first
    # deploy creates it. Left blank now and filled in at step 6.
    settings["pguser"] = cache.get("pguser", "")

    save_cache({**cache, **settings})

    # -- 3. config --
    step(3, total, "Write configuration")
    write_config(settings)

    # -- 4. frontend --
    step(4, total, "Frontend")
    build_frontend(args.skip_build)

    # -- 5. deploy --
    step(5, total, f"Deploy bundle (target: {target})")
    bundle_deploy(profile, target, settings)

    # -- 6. grants --
    step(6, total, "Service principal + Unity Catalog grants")
    app_name = APP_NAME if target == "prod" else f"{APP_NAME}-{target}"
    app = resolve_app(profile, app_name) if not DRY_RUN else {"name": app_name}
    if app is None:
        print(f"  {yellow('could not read the app')} — is it named {app_name!r}?")
        sp = None
    else:
        sp = app_service_principal(app)
    if sp:
        print(f"  service principal: {sp}")
        # The app authenticates to Postgres AS its service principal, so PGUSER
        # must be that id. This is the one setting nobody can supply up front.
        if sp != settings.get("pguser"):
            settings["pguser"] = sp
            write_config(settings)
            save_cache({**load_cache(), **settings})
            print(f"  {green('ok')} PGUSER set to the app service principal")
            print(f"  {dim('re-deploying so the app picks up PGUSER...')}")
            bundle_deploy(profile, target, settings)
    else:
        print(f"  {yellow('service principal unknown')} — set PGUSER in app.yaml by "
              "hand (Apps UI shows the id), then re-deploy.")

    if args.skip_grants:
        print(f"  {dim('grants skipped (--skip-grants)')}")
    elif sp:
        run_grants(profile, warehouse_id, settings, sp)
    else:
        print(f"  {dim('grants skipped — no service principal resolved')}")

    # -- 7. seed --
    step(7, total, "Seed Lakebase")
    if args.skip_seed:
        print(f"  {dim('skipped (--skip-seed)')}")
    elif not settings["pghost"]:
        print(f"  {yellow('skipped')} — no Lakebase host. Provision it, then run:")
        print(f"    python3 scripts/seed_clean.py --profile {profile} "
              f"--project {lakebase_project} --db {pg_database}")
    else:
        seed(profile, lakebase_project, pg_database, args.seed_demo)

    # -- 8. start --
    step(8, total, "Start the app")
    if args.skip_start:
        print(f"  {dim('skipped (--skip-start)')}")
    else:
        run(["databricks", "bundle", "run", BUNDLE_RESOURCE, "-t", target,
             "-p", profile], check=False)

    url = (app or {}).get("url") if isinstance(app, dict) else None
    print(f"\n{green(bold('Done.'))}")
    if url:
        print(f"  App:   {url}")
    print(f"  Check: open the app and go to Setup — every dependency is probed "
          f"there,\n         with the exact GRANT statements for anything missing.")
    if DRY_RUN:
        print(f"\n{yellow('That was a dry run; nothing was changed.')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
