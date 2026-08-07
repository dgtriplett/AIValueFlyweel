#!/usr/bin/env python3
"""Apply Grid Atlas migrations to a Lakebase database.

    python3 scripts/migrate.py --profile <cli-profile> --project <lakebase-project>
    python3 scripts/migrate.py --status          # read-only: what would change
    python3 scripts/migrate.py --grant-app-sp <sp-client-id>

Runs as YOU (the database owner), because the app's service principal holds DML but
not DDL. That split is deliberate — see server/migrator.py — and it means schema
changes are an explicit, auditable step rather than something that happens on a
restart nobody was watching.

Uses psycopg2 (which the seed scripts already require) rather than the app's asyncpg
pool, so this works from a laptop without the app's runtime dependencies.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
MIGRATIONS = ROOT / "server" / "migrations"

sys.path.insert(0, str(ROOT))
from server.migrator import LEDGER_DDL, checksum, discover  # noqa: E402


def cli_json(args: list[str]):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"CLI failed: {' '.join(args)}\n{result.stderr[:400]}")
    return json.loads(result.stdout)


def connect(profile: str, project: str, database: str, branch: str = "production"):
    try:
        import psycopg2
    except ImportError:
        sys.exit("psycopg2 is required: pip install psycopg2-binary")

    branch_path = f"projects/{project}/branches/{branch}"
    endpoints = cli_json(["databricks", "postgres", "list-endpoints", branch_path,
                          "-p", profile, "-o", "json"])
    if not endpoints:
        sys.exit(f"no endpoints for {branch_path} — is the project provisioned?")
    host = endpoints[0]["status"]["hosts"]["host"]
    token = cli_json(["databricks", "postgres", "generate-database-credential",
                      f"{branch_path}/endpoints/primary", "-p", profile,
                      "-o", "json"])["token"]
    user = cli_json(["databricks", "current-user", "me", "-p", profile,
                     "-o", "json"])["userName"]
    print(f"  host {host}\n  db   {database}\n  as   {user}")
    connection = psycopg2.connect(host=host, port=5432, dbname=database, user=user,
                                  password=token, sslmode="require")
    connection.autocommit = False
    return connection


def read_ledger(cursor) -> dict[str, str]:
    try:
        cursor.execute("SELECT filename, checksum FROM schema_migrations")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        cursor.connection.rollback()
        return {}


def show_status(cursor) -> tuple[list[Path], list[dict]]:
    files = discover(MIGRATIONS)
    applied = read_ledger(cursor)
    pending, drifted = [], []
    print(f"\n  {len(files)} migration(s) on disk, {len(applied)} recorded as applied\n")
    for path in files:
        digest = checksum(path.read_text())
        previous = applied.get(path.name)
        if previous is None:
            print(f"    PENDING   {path.name}")
            pending.append(path)
        elif previous != digest:
            print(f"    DRIFTED   {path.name}  (applied {previous}, file {digest})")
            drifted.append({"filename": path.name})
        else:
            print(f"    ok        {path.name}")
    orphaned = sorted(set(applied) - {p.name for p in files})
    for name in orphaned:
        print(f"    ORPHANED  {name}  (recorded but no longer on disk)")
    return pending, drifted


def apply_pending(cursor, pending: list[Path], actor: str) -> int:
    cursor.execute(LEDGER_DDL)
    cursor.connection.commit()
    count = 0
    for path in pending:
        sql = path.read_text()
        print(f"\n  applying {path.name} …")
        try:
            cursor.execute(sql)
            cursor.execute(
                "INSERT INTO schema_migrations (filename, checksum, applied_by) "
                "VALUES (%s,%s,%s) ON CONFLICT (filename) DO UPDATE SET "
                "checksum=EXCLUDED.checksum, applied_at=now(), "
                "applied_by=EXCLUDED.applied_by",
                (path.name, checksum(sql), actor))
            cursor.connection.commit()
            print(f"  ok       {path.name}")
            count += 1
        except Exception as exc:
            cursor.connection.rollback()
            # Stop here: later migrations assume this one landed.
            sys.exit(f"\nFAILED {path.name} (rolled back): {exc}\n"
                     f"Applied before this point: {count}")
    return count


def grant_app_sp(cursor, sp: str, database: str) -> None:
    """Grant the app's service principal DML — never ownership.

    Without ownership the SP cannot run DDL, which is what keeps schema changes to
    this script. Re-run after adding migrations that create tables: the grants below
    cover existing tables, and ALTER DEFAULT PRIVILEGES only covers tables created
    later by the SAME role.
    """
    statements = [
        f'GRANT CONNECT ON DATABASE "{database}" TO "{sp}"',
        f'GRANT USAGE ON SCHEMA public TO "{sp}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{sp}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{sp}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{sp}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{sp}"',
    ]
    print()
    for statement in statements:
        try:
            cursor.execute(statement)
            cursor.connection.commit()
            print(f"  ok   {statement[:82]}")
        except Exception as exc:
            cursor.connection.rollback()
            print(f"  note {statement[:56]} -> {str(exc).strip()[:60]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="DEFAULT", help="Databricks CLI profile")
    parser.add_argument("--project", required=False, help="Lakebase project id")
    parser.add_argument("--db", default="app", help="database name")
    parser.add_argument("--status", action="store_true",
                        help="report state without changing anything")
    parser.add_argument("--allow-drift", action="store_true",
                        help="proceed even though an applied migration changed "
                             "(only when you have confirmed the edit was cosmetic)")
    parser.add_argument("--grant-app-sp", metavar="CLIENT_ID",
                        help="grant the app service principal DML after migrating")
    args = parser.parse_args()

    if not args.project:
        sys.exit("--project is required (the Lakebase project holding app state)")

    print("Grid Atlas — migrations")
    connection = connect(args.profile, args.project, args.db)
    cursor = connection.cursor()
    pending, drifted = show_status(cursor)

    if args.status:
        print(f"\n  {len(pending)} pending, {len(drifted)} drifted")
        return

    if drifted and not args.allow_drift:
        sys.exit(
            "\nSCHEMA DRIFT: the files above changed after being applied, so the "
            "database no longer matches this code.\n"
            "Neither automatic option is safe. Write a NEW migration for the change "
            "(preferred), or re-run with --allow-drift if the edit was cosmetic.")

    if not pending:
        print("\n  nothing to apply")
    else:
        actor = cli_json(["databricks", "current-user", "me", "-p", args.profile,
                          "-o", "json"])["userName"]
        count = apply_pending(cursor, pending, actor)
        print(f"\n  applied {count} migration(s)")

    if args.grant_app_sp:
        grant_app_sp(cursor, args.grant_app_sp, args.db)

    cursor.close()
    connection.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
