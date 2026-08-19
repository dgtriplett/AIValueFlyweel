#!/usr/bin/env python3
"""Remap generic maturity-roadmap imported assets to canonical catalog assets."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.migrate import cli_json, connect  # noqa: E402
from server.roadmap_asset_resolution import canonical_asset_target  # noqa: E402

IMPORTED_CATEGORIES = ("Imported roadmap dataset", "Imported roadmap prerequisite")
STATUS_RANK = {"not_started": 0, "landed": 1, "curated": 2, "governed": 3}
RANK_STATUS = {rank: status for status, rank in STATUS_RANK.items()}


def stronger(a: str | None, b: str | None) -> str:
    return RANK_STATUS[max(STATUS_RANK.get(a or "not_started", 0),
                           STATUS_RANK.get(b or "not_started", 0))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="DEFAULT", help="Databricks CLI profile")
    parser.add_argument("--project", required=True, help="Lakebase project id")
    parser.add_argument("--db", default="app", help="database name")
    parser.add_argument("--apply", action="store_true", help="write changes")
    parser.add_argument("--drop-unresolved", action="store_true",
                        help="when applying, remove imported assets that do not resolve to a canonical source")
    args = parser.parse_args()

    actor = cli_json(["databricks", "current-user", "me", "-p", args.profile,
                      "-o", "json"])["userName"]
    connection = connect(args.profile, args.project, args.db)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, source_category, module
        FROM data_assets
        WHERE source_category = ANY(%s)
        ORDER BY id
    """, (list(IMPORTED_CATEGORIES),))
    imported = cursor.fetchall()

    remapped = 0
    edges = 0
    statuses = 0
    unresolved: list[tuple[int, str, str]] = []

    for old_id, source_category, label in imported:
        target = canonical_asset_target(label)
        if not target:
            unresolved.append((old_id, source_category, label))
            if args.apply and args.drop_unresolved:
                cursor.execute("DELETE FROM uc_requires_asset WHERE data_asset_id=%s", (old_id,))
                cursor.execute("DELETE FROM account_asset_status WHERE data_asset_id=%s", (old_id,))
                cursor.execute("DELETE FROM data_assets WHERE id=%s", (old_id,))
                connection.commit()
            continue

        cursor.execute("""
            SELECT id FROM data_assets
            WHERE lower(source_category) = lower(%s)
              AND lower(module) = lower(%s)
            LIMIT 1
        """, target)
        row = cursor.fetchone()
        if not row:
            unresolved.append((old_id, source_category, label))
            if args.apply and args.drop_unresolved:
                cursor.execute("DELETE FROM uc_requires_asset WHERE data_asset_id=%s", (old_id,))
                cursor.execute("DELETE FROM account_asset_status WHERE data_asset_id=%s", (old_id,))
                cursor.execute("DELETE FROM data_assets WHERE id=%s", (old_id,))
                connection.commit()
            continue
        new_id = row[0]

        cursor.execute("SELECT count(*) FROM uc_requires_asset WHERE data_asset_id=%s", (old_id,))
        old_edge_count = int(cursor.fetchone()[0])
        cursor.execute("SELECT account_id, ingestion_status FROM account_asset_status WHERE data_asset_id=%s",
                       (old_id,))
        old_status_rows = cursor.fetchall()

        print(f"{old_id}: {source_category} · {label} -> {target[0]} · {target[1]} ({new_id})")
        if not args.apply:
            remapped += 1
            edges += old_edge_count
            statuses += len(old_status_rows)
            continue

        cursor.execute("""
            INSERT INTO uc_requires_asset (use_case_id, data_asset_id, criticality, manual)
            SELECT use_case_id, %s, criticality, manual
            FROM uc_requires_asset
            WHERE data_asset_id=%s
            ON CONFLICT (use_case_id, data_asset_id) DO NOTHING
        """, (new_id, old_id))
        cursor.execute("DELETE FROM uc_requires_asset WHERE data_asset_id=%s", (old_id,))
        edges += old_edge_count

        for account_id, old_status in old_status_rows:
            cursor.execute("""
                SELECT ingestion_status
                FROM account_asset_status
                WHERE account_id=%s AND data_asset_id=%s
            """, (account_id, new_id))
            current = cursor.fetchone()
            merged = stronger(current[0] if current else None, old_status)
            cursor.execute("""
                INSERT INTO account_asset_status
                    (account_id, data_asset_id, ingestion_status, is_user_edited, updated_by, updated_at)
                VALUES (%s,%s,%s,false,%s,now())
                ON CONFLICT (account_id, data_asset_id) DO UPDATE SET
                    ingestion_status=EXCLUDED.ingestion_status,
                    updated_by=EXCLUDED.updated_by,
                    updated_at=now()
            """, (account_id, new_id, merged, actor))
            statuses += 1

        cursor.execute("DELETE FROM account_asset_status WHERE data_asset_id=%s", (old_id,))
        cursor.execute("DELETE FROM data_assets WHERE id=%s", (old_id,))
        connection.commit()
        remapped += 1

    if not args.apply:
        connection.rollback()
    cursor.close()
    connection.close()

    mode = "applied" if args.apply else "dry-run"
    dropped = len(unresolved) if args.apply and args.drop_unresolved else 0
    print(f"\n{mode}: {remapped} imported assets remapped, {edges} edges moved, "
          f"{statuses} statuses merged, {len(unresolved)} unresolved, {dropped} dropped")
    if unresolved:
        print("\nunresolved:")
        for old_id, source_category, label in unresolved[:100]:
            print(f"  {old_id}: {source_category} · {label}")
        if len(unresolved) > 100:
            print(f"  ... {len(unresolved) - 100} more")
        if not (args.apply and args.drop_unresolved):
            sys.exit(2 if args.apply else 1)


if __name__ == "__main__":
    main()
