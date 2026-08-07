"""Migration application with versioning and drift detection.

THE PROBLEM THIS FIXES
----------------------
Startup previously concatenated every `*.sql` file and executed the lot on every
boot. That works only while every statement stays idempotent — and the first time
someone writes a migration that isn't (a backfill `UPDATE`, an `INSERT` of seed
rows, a `DROP` of a superseded column), replaying it silently corrupts or destroys
customer data on the next restart. Nothing warns you, because the previous run
"succeeded" too.

It also can't tell you what state a customer's database is actually in, which is the
first question in any support conversation.

WHAT IT DOES INSTEAD
--------------------
  - `schema_migrations` records every applied file with a content checksum.
  - Each file is applied AT MOST ONCE, in lexical order, inside a transaction.
  - If an already-applied file's contents have CHANGED, that is drift: the database
    no longer matches the code. It reports loudly and refuses to guess, because both
    possible guesses are bad — re-applying may destroy data, ignoring it leaves the
    schema silently wrong.
  - The app SP holds DML but not DDL, so an unapplied migration is EXPECTED at
    runtime and is reported as a state to fix, never as a crash.

WHY NOT ALEMBIC
---------------
Alembic is the right answer for a service that owns its database. Here the schema is
owned by the install step (run as the Lakebase owner), the app runs as a
DDL-less principal, and migrations are plain idempotent SQL files a customer can
read before running. A dependency plus a revision graph plus an env.py would add
moving parts without changing what actually happens: apply these files, once, in
order. ~150 lines that a customer can audit beats a framework they cannot.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Bootstrap DDL. Not itself a numbered migration: it has to exist before the ledger
# can be consulted, so it is idempotent and applied unconditionally.
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by  TEXT,
    duration_ms INT
)
"""

# Filenames must sort into the intended order, because later migrations ALTER what
# earlier ones create. A three-digit prefix makes that ordering explicit and lexical.
FILENAME_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be applied, or the database has drifted."""


def checksum(sql: str) -> str:
    """Content hash, insensitive to trailing-whitespace and line-ending churn.

    Normalizing means a reformat or a CRLF checkout does not read as drift, while a
    real change to a statement does.
    """
    normalized = "\n".join(line.rstrip() for line in sql.replace("\r\n", "\n").split("\n"))
    return hashlib.sha256(normalized.strip().encode("utf-8")).hexdigest()[:16]


def discover(migrations_dir: Path) -> list[Path]:
    """Migration files in application order, rejecting unorderable names."""
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory not found: {migrations_dir}")
    files = sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())
    bad = [p.name for p in files if not FILENAME_PATTERN.match(p.name)]
    if bad:
        raise MigrationError(
            f"migration filenames must look like 001_name.sql (got: {bad}). "
            "Order is lexical and load-bearing — later migrations ALTER tables "
            "earlier ones create.")
    return files


async def plan(db, migrations_dir: Path) -> dict:
    """What would be applied, and whether the database has drifted. Read-only.

    Safe to call when the ledger does not exist yet (a fresh or pre-versioning
    database): everything simply reads as pending.
    """
    files = discover(migrations_dir)
    applied: dict[str, str] = {}
    ledger_exists = True
    try:
        rows = await db.fetch("SELECT filename, checksum FROM schema_migrations")
        applied = {r["filename"]: r["checksum"] for r in rows}
    except Exception:  # noqa: BLE001 - absent ledger is a normal first-run state
        ledger_exists = False

    pending, drifted, current = [], [], []
    for path in files:
        digest = checksum(path.read_text())
        previous = applied.get(path.name)
        if previous is None:
            pending.append(path.name)
        elif previous != digest:
            drifted.append({"filename": path.name,
                            "applied_checksum": previous,
                            "file_checksum": digest})
        else:
            current.append(path.name)

    # A file recorded in the ledger but missing from disk means someone deleted a
    # migration. Harmless to the running database, but it means the code no longer
    # describes how that schema came to be.
    orphaned = sorted(set(applied) - {p.name for p in files})

    return {
        "ledger_exists": ledger_exists,
        "total": len(files),
        "current": current,
        "pending": pending,
        "drifted": drifted,
        "orphaned": orphaned,
        "up_to_date": not pending and not drifted,
    }


async def apply(db, migrations_dir: Path, *, actor: str = "install",
                allow_drift: bool = False) -> dict:
    """Apply pending migrations. Requires DDL privilege (the DB owner).

    Each file runs in its own transaction and is recorded in the same transaction,
    so a failure leaves neither a half-applied file nor a lying ledger.

    Drift aborts before anything is applied unless `allow_drift`, because the safe
    action genuinely depends on what changed — and the operator, not this function,
    is the one who knows.
    """
    import time

    state = await plan(db, migrations_dir)

    if state["drifted"] and not allow_drift:
        names = ", ".join(d["filename"] for d in state["drifted"])
        raise MigrationError(
            f"Migration drift: {names} changed after being applied. The database no "
            "longer matches the code.\n"
            "Neither automatic option is safe — re-applying may destroy data, "
            "ignoring it leaves the schema silently wrong. Either write a NEW "
            "migration for the change (preferred), or re-run with allow_drift=True "
            "if you have confirmed the edit was cosmetic.")

    # The ledger itself, applied unconditionally and idempotently.
    await db.execute(LEDGER_DDL)

    applied: list[dict] = []
    for name in state["pending"]:
        path = migrations_dir / name
        sql = path.read_text()
        digest = checksum(sql)
        started = time.monotonic()
        try:
            pool = await db.get_pool()
            if pool is None:
                raise MigrationError("no database connection")
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations "
                        "(filename, checksum, applied_by, duration_ms) "
                        "VALUES ($1,$2,$3,$4) "
                        "ON CONFLICT (filename) DO UPDATE SET "
                        "checksum=EXCLUDED.checksum, applied_at=now(), "
                        "applied_by=EXCLUDED.applied_by",
                        name, digest, actor,
                        int((time.monotonic() - started) * 1000))
        except Exception as exc:
            # Stop at the first failure: later migrations assume earlier ones
            # succeeded, so continuing would compound the problem.
            raise MigrationError(
                f"{name} failed and was rolled back: {exc}\n"
                f"Applied before this point: {[a['filename'] for a in applied] or 'none'}"
            ) from exc
        applied.append({"filename": name,
                        "duration_ms": int((time.monotonic() - started) * 1000)})
        logger.info("applied migration %s", name)

    return {"applied": applied, "applied_count": len(applied),
            "already_current": len(state["current"]),
            "drifted": state["drifted"], "orphaned": state["orphaned"]}


async def startup_check(db, migrations_dir: Path) -> dict:
    """Read-only check for app startup.

    The running app authenticates as a service principal with DML but NOT DDL, so it
    cannot and must not apply migrations — that is the install step's job, run as the
    database owner. This reports the state so the app can log a precise, actionable
    line and serve, instead of either crashing or silently running against a schema
    it does not match.
    """
    try:
        state = await plan(db, migrations_dir)
    except MigrationError as exc:
        logger.warning("migration check skipped: %s", exc)
        return {"ok": False, "error": str(exc)}

    if state["up_to_date"]:
        logger.info("schema up to date (%d migrations)", state["total"])
        return {"ok": True, **state}

    if state["drifted"]:
        logger.error(
            "SCHEMA DRIFT: %s changed after being applied — the database does not "
            "match this code. Endpoints touching those tables may fail.",
            ", ".join(d["filename"] for d in state["drifted"]))
    if state["pending"]:
        logger.warning(
            "%d migration(s) not applied: %s. Run: python3 scripts/migrate.py "
            "--profile <profile> --project <lakebase-project>",
            len(state["pending"]), ", ".join(state["pending"]))
    return {"ok": False, **state}
