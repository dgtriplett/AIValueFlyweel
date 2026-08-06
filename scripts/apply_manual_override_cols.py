"""One-off: add the manual module-override columns as the table OWNER.

The app's service principal isn't the table owner, so its startup migration
can't ALTER these tables. Run this as the workspace user (who owns the tables)
to add the columns; the SP's existing DML grants then cover the new columns.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from seed_lib import get_conn, parse_args  # noqa: E402

STMTS = [
    "ALTER TABLE use_cases ADD COLUMN IF NOT EXISTS requires_locked BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE uc_requires_asset ADD COLUMN IF NOT EXISTS manual BOOLEAN NOT NULL DEFAULT false",
]


def main():
    args = parse_args()
    conn = get_conn(args.profile, args.project, args.branch, args.endpoint, args.db)
    conn.autocommit = True
    with conn.cursor() as cur:
        for s in STMTS:
            cur.execute(s)
            print(f"[ok] {s}")
        # verify
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='use_cases' AND column_name='requires_locked'""")
        print("use_cases.requires_locked present:", bool(cur.fetchone()))
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='uc_requires_asset' AND column_name='manual'""")
        print("uc_requires_asset.manual present:", bool(cur.fetchone()))
    conn.close()
    print("[done] manual-override columns applied")


if __name__ == "__main__":
    main()
