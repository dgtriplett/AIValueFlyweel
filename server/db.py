"""Lakebase Postgres connection pool with dual-mode auth and token refresh.

- If ``PGHOST`` is unset, runs in graceful demo mode (queries return empties).
- OAuth tokens expire (~1h); on an auth failure we recreate the pool with a
  fresh token. A background task also proactively refreshes every ~45 minutes.
"""
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

from .config import get_oauth_token

logger = logging.getLogger(__name__)


def _charge_budget() -> None:
    """Count this query against the current request's budget, if it set one.

    Charged HERE rather than in each caller because fetch/execute are the only two
    ways a query reaches the pool, so counting at this choke point measures real
    queries instead of estimating them. Requests that set no budget (almost all of
    them) pay one dict lookup.

    Imported lazily to keep limits.py free of a db dependency, which would
    otherwise be a cycle.
    """
    from .limits import current_budget

    budget = current_budget.get()
    if budget is not None:
        budget.charge()

# asyncpg error classes that indicate the OAuth token is stale/invalid.
_AUTH_ERRORS = (
    asyncpg.InvalidAuthorizationSpecificationError,
    asyncpg.InvalidPasswordError,
)


class DatabaseUnavailable(RuntimeError):
    """Lakebase is configured but unreachable, and the caller must not paper over it.

    Deliberately NOT raised when Lakebase is unconfigured (no PGHOST): there, empty
    reads and no-op writes are the correct, documented behaviour. This is only for
    the case where a real database is expected and cannot be reached, so returning a
    benign empty would be a lie the caller acts on — see `_require_available`.
    """


class DatabasePool:
    """The Lakebase pool, plus an honest account of why there isn't one.

    TWO REASONS THERE IS NO POOL, AND THEY ARE NOT THE SAME
    -------------------------------------------------------
    `PGHOST` unset means nobody configured Lakebase: a local checkout, a demo, a
    first boot before `scripts/deploy.py` has run. Empty reads are the CORRECT
    answer there, and the app is working as intended.

    `PGHOST` set but the pool failing to open means the database this app depends on
    is unreachable — a bad OAuth token, a DNS failure, a Lakebase outage. The rows
    exist; we just cannot see them.

    Collapsing both into one `is_demo_mode` flag made an outage indistinguishable
    from a demo: reads returned `[]`, so every screen rendered "no data yet" over a
    live customer's populated database, and /api/health still said "healthy". That
    is the worst possible failure — silent, total, and reported as fine. So the two
    states are tracked separately and `is_degraded` is what /api/health must read.
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._unconfigured = False
        self._last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def is_demo_mode(self) -> bool:
        """True only when Lakebase is EXPLICITLY unconfigured (no PGHOST).

        Deliberately does NOT cover connection failure. A caller asking "am I in
        demo mode" is asking "is an empty result expected", and during an outage it
        very much is not.
        """
        return self._unconfigured

    @property
    def is_degraded(self) -> bool:
        """True when Lakebase is configured but unreachable — a real outage."""
        return self._last_error is not None and self._pool is None

    @property
    def last_error(self) -> Optional[str]:
        """The most recent pool-creation failure, for /api/health to report."""
        return self._last_error

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        if not os.environ.get("PGHOST"):
            self._unconfigured = True
            self._last_error = None
            return None

        self._unconfigured = False
        if self._pool is not None:
            return self._pool

        async with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                token = get_oauth_token()
                self._pool = await asyncpg.create_pool(
                    host=os.environ["PGHOST"],
                    port=int(os.environ.get("PGPORT", "5432")),
                    database=os.environ.get("PGDATABASE", "app"),
                    user=os.environ["PGUSER"],
                    password=token,
                    ssl="require",
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                )
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                # NOT demo mode: PGHOST is set, so somebody expects real data and is
                # not getting it. Recorded so /api/health can report unhealthy
                # instead of serving empty reads under a green status.
                logger.error(
                    "Lakebase connection failed (%s: %s) — reads will be empty and "
                    "writes will 503 until it recovers",
                    type(exc).__name__, exc)
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._pool = None
        return self._pool

    async def refresh_token(self) -> None:
        """Recreate the pool with a fresh OAuth token."""
        async with self._lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
        await self.get_pool()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_available(self) -> None:
        """Raise if Lakebase is configured but unreachable.

        THE BUG THIS EXISTS TO PREVENT
        -----------------------------
        `get_pool()` returns None for two unrelated reasons, and the query helpers
        used to turn both into a benign `[]` / `None`. Downstream that is not benign
        at all:

          * `default_account_id()` reads `fetchrow() -> None` as "no accounts exist",
            so `scope_clause()` returns the literal `true` and the request reads
            EVERY tenant's rows;
          * `execute()` returning None is indistinguishable from a successful write,
            so a create/update reports 200 having persisted nothing.

        A configured outage must therefore raise, not return a value a caller can
        mistake for data. The explicitly-unconfigured path (no PGHOST) keeps its
        benign empties — there, "no rows" really is the right answer.
        """
        if self.is_degraded:
            raise DatabaseUnavailable(
                "Lakebase is configured (PGHOST is set) but unreachable "
                f"({self._last_error}). Refusing to return an empty result that "
                "would read as 'no data' and silently unscope this request.")

    # -- query helpers ------------------------------------------------------
    async def fetch(self, sql: str, *args):
        _charge_budget()
        pool = await self.get_pool()
        if pool is None:
            self._require_available()
            return []
        try:
            async with pool.acquire() as conn:
                return await conn.fetch(sql, *args)
        except _AUTH_ERRORS:
            await self.refresh_token()
            pool = await self.get_pool()
            if pool is None:
                self._require_available()
                return []
            async with pool.acquire() as conn:
                return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    @asynccontextmanager
    async def transaction(self):
        """One connection, one transaction, for writes that must not half-apply.

        Yields an asyncpg connection, or None only when Lakebase is explicitly
        UNCONFIGURED — so a caller handles None exactly as it handles `execute()`
        returning None. A configured-but-unreachable pool raises instead, because
        yielding None there would let a caller report success for a transaction that
        never ran.

        Needed because `execute()` takes a fresh connection per call, so a
        multi-statement invariant (clear the old default, then set the new one) can
        be interrupted between statements and leave the table with no default at
        all — every unscoped request then resolves to nothing.
        """
        pool = await self.get_pool()
        if pool is None:
            self._require_available()
            yield None
            return
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def execute(self, sql: str, *args):
        _charge_budget()
        pool = await self.get_pool()
        if pool is None:
            self._require_available()
            return None
        try:
            async with pool.acquire() as conn:
                return await conn.execute(sql, *args)
        except _AUTH_ERRORS:
            await self.refresh_token()
            pool = await self.get_pool()
            if pool is None:
                self._require_available()
                return None
            async with pool.acquire() as conn:
                return await conn.execute(sql, *args)


db = DatabasePool()


async def token_refresh_loop(interval_seconds: int = 45 * 60) -> None:
    """Proactively refresh the OAuth token before it expires."""
    while True:
        await asyncio.sleep(interval_seconds)
        if not db.is_demo_mode and os.environ.get("PGHOST"):
            try:
                await db.refresh_token()
                logger.info("OAuth token refreshed")
            except Exception as exc:  # noqa: BLE001
                # Not fatal: the next query's auth-error path recreates the pool.
                logger.warning("token refresh failed (%s: %s) — will retry on the "
                               "next query", type(exc).__name__, exc)
