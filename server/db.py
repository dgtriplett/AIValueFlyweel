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

# Failures that mean "the connection is not usable", as opposed to "the query was
# wrong". These become DatabaseUnavailable -> 503, because the caller cannot tell from
# an opaque 500 whether their write applied.
#
# The distinction matters in both directions. Too narrow (auth only, as it was) and a
# broken pool raises a raw OSError that bypasses the 503 handler with is_degraded
# still False, so /api/health keeps saying healthy through an outage. Too broad
# (`except Exception`) and a 42P01 undefined-table becomes a 503, which would break
# the legitimate pre-migration path and every route that handles its own DB errors.
#
# OSError covers the socket-level cases (ECONNRESET, EPIPE, DNS) and is asyncpg's own
# base for connection loss; asyncpg.PostgresConnectionError covers the protocol-level
# ones the driver classifies itself; TimeoutError covers command_timeout and pool
# acquire timeouts, which are not OSErrors.
_CONNECTION_ERRORS = (
    OSError,
    asyncio.TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
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
        """Recreate the pool with a fresh OAuth token.

        WHY close() IS BEST-EFFORT
        -------------------------
        `Pool.close()` really can raise — asyncpg propagates errors from it — and the
        old code awaited it bare, OUTSIDE any handler. An OSError from closing a
        already-dead socket therefore escaped raw, with the OLD pool still installed
        and `is_degraded` False: a 500 instead of a 503, and a later call happily
        reusing a pool that cannot work while /api/health reported healthy.

        The insight is that a failing close is not the problem to report — we are
        discarding this pool either way, and the reason we got here is that the
        database is already misbehaving. So the close failure is logged and swallowed,
        and `self._pool` is cleared REGARDLESS, so a broken pool is never left behind.
        What propagates is the state of the REPLACEMENT attempt: `get_pool()` records
        the real cause and `_require_available()` turns it into DatabaseUnavailable.
        """
        async with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:  # noqa: BLE001
                # Deliberately broad: this is teardown of an object we are discarding.
                # Any failure here must not mask the outage we are trying to recover
                # from, and must not stop the replacement attempt below.
                logger.warning(
                    "closing the old Lakebase pool failed (%s: %s) — discarding it "
                    "anyway", type(exc).__name__, exc)
        # Rebuild. If this fails, get_pool() has recorded the cause and is_degraded is
        # true, so raise rather than returning as though the refresh had worked —
        # otherwise the caller retries against a pool that does not exist.
        await self.get_pool()
        self._require_available()

    async def close(self) -> None:
        """Shut the pool down at app exit. Best-effort, and always clears the ref."""
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:  # noqa: BLE001
                # Shutdown path: a failing close must not prevent a clean exit.
                logger.warning("closing the Lakebase pool failed (%s: %s)",
                               type(exc).__name__, exc)

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

    def _mark_faulted(self, exc: BaseException, context: str) -> None:
        """Discard the suspect pool and record the fault, WITHOUT raising.

        The two things every connection-level fault must do regardless of when it
        happens: stop the suspect pool being reused, and make `/api/health` tell the
        truth. Separated from `_connection_failed` because whether the CALLER should
        also see a 503 depends on timing — see the release path in `transaction()`,
        where the write has already been decided and a 503 would be a different lie.
        """
        self._last_error = f"{type(exc).__name__}: {exc}"
        self._pool = None      # force a fresh pool on the next call
        logger.error("Lakebase fault during %s (%s: %s) — discarding the pool and "
                     "reporting unhealthy", context, type(exc).__name__, exc)

    def _connection_failed(self, exc: Exception) -> "DatabaseUnavailable":
        """Record a connection-level failure and convert it to DatabaseUnavailable.

        WHY THIS IS NEEDED BEYOND `_require_available`
        ----------------------------------------------
        `_require_available` only fires when there is NO pool, which covers a failed
        pool CREATION. But a pool that opened successfully and later breaks — the
        database restarts, the network drops, `acquire()` times out — raised its raw
        OSError straight through. That produced a 500 with `is_degraded` still False,
        bypassing the DatabaseUnavailable handler entirely, so:

          * the response was an opaque server error rather than an honest 503,
          * /api/health still reported healthy, because nothing had recorded a failure.

        Only authentication errors were being caught here, and an expired token is not
        the only way a live pool stops working. So ANY connection-level failure now
        records `_last_error` (making `is_degraded` true, which /api/health reads) and
        re-raises as DatabaseUnavailable.

        Query-level errors — a syntax error, a constraint violation, an undefined table
        — are deliberately NOT routed here. Those are bugs or expected conditions the
        caller handles, not outages, and turning a 42P01 into a 503 would break the
        pre-migration path along with every route that catches its own DB errors.
        """
        self._mark_faulted(exc, "a query")
        return DatabaseUnavailable(
            "Lakebase is configured (PGHOST is set) but the connection failed "
            f"({self._last_error}). No data was read or written.")

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
            try:
                async with pool.acquire() as conn:
                    return await conn.fetch(sql, *args)
            except (_AUTH_ERRORS + _CONNECTION_ERRORS) as exc:
                # A PERSISTENT auth failure is an outage, not a routine expiry: the
                # refresh above already gave it a fresh token. Without _AUTH_ERRORS
                # here the second failure escaped raw as a 500.
                raise self._connection_failed(exc) from exc
        except _CONNECTION_ERRORS as exc:
            raise self._connection_failed(exc) from exc

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

        AUTH FAILURES GET THE SAME RETRY AS fetch()/execute()
        ----------------------------------------------------
        This used to catch only `_CONNECTION_ERRORS`, so an expired or rotated OAuth
        token — `InvalidPasswordError` from `acquire()` — propagated raw: a 500 that
        bypassed the DatabaseUnavailable handler with `is_degraded` still False. The
        account `make_default` switch runs through here, so a token rotation could
        500 that operation while /api/health stayed green.

        The retry-once-then-503 shape is deliberately identical to fetch()/execute():
        one token refresh, one retry, and only a PERSISTENT auth failure becomes
        DatabaseUnavailable. Converting on the first auth error would make
        transaction() give up faster than the other two and turn a routine token
        expiry into a user-visible 503.

        The retry can only cover ACQUIRING the connection, not the caller's body —
        once `yield` has handed a connection over, the caller's statements may have
        partially run and replaying them is not safe. An auth error raised by the
        body therefore surfaces as DatabaseUnavailable without a retry, which is the
        honest outcome: the transaction rolled back and nothing was applied.
        """
        pool = await self.get_pool()
        if pool is None:
            self._require_available()
            yield None
            return

        # Acquire with the same refresh-and-retry the other helpers use. Kept separate
        # from the caller's body below so a retry never re-runs their statements.
        try:
            conn_ctx = pool.acquire()
            conn = await conn_ctx.__aenter__()
        except _AUTH_ERRORS:
            await self.refresh_token()
            pool = await self.get_pool()
            if pool is None:
                self._require_available()
                yield None
                return
            try:
                conn_ctx = pool.acquire()
                conn = await conn_ctx.__aenter__()
            except (_AUTH_ERRORS + _CONNECTION_ERRORS) as exc:
                # The refresh did not help, so this is a real outage rather than a
                # routine expiry.
                raise self._connection_failed(exc) from exc
        except _CONNECTION_ERRORS as exc:
            raise self._connection_failed(exc) from exc

        try:
            async with conn.transaction():
                yield conn
        except (_AUTH_ERRORS + _CONNECTION_ERRORS) as exc:
            raise self._connection_failed(exc) from exc
        finally:
            # RELEASE IS THE ONE PATH THAT MUST NOT BECOME A 503.
            #
            # By the time __aexit__ runs the body has already executed and, on the
            # success path, COMMITTED. Raising DatabaseUnavailable here would tell the
            # caller their write failed when it may well have succeeded — a different
            # lie, and just as damaging as the one the earlier rounds fixed. Nor may
            # it replace a body exception: a caller whose UPDATE hit a 23505 needs to
            # see the 23505, not a connection error from teardown.
            #
            # But logging alone (what this used to do) left the SUSPECT POOL INSTALLED
            # and is_degraded False, so the next request drew a connection from a pool
            # that had just faulted at the socket layer, and /api/health called it
            # healthy. Both halves of that are wrong independently of the request's
            # outcome.
            #
            # So: discard the pool and record the fault — the next call rebuilds and
            # health reports honestly — while the completed request's result stands.
            # Only connection/auth-level faults qualify; a query error surfacing at
            # commit (a deferred constraint, say) is an application error, so it
            # propagates untouched and must NOT discard the pool or flip degraded.
            try:
                await conn_ctx.__aexit__(None, None, None)
            except (_AUTH_ERRORS + _CONNECTION_ERRORS) as exc:
                self._mark_faulted(exc, "releasing a transaction connection")
            except Exception as exc:  # noqa: BLE001
                # A query-level or unexpected error from teardown. Not an outage, so
                # the pool stays and degraded is untouched — but it is still logged,
                # because silently dropping it is how this path went unexamined.
                logger.warning(
                    "releasing the Lakebase connection failed with a non-connection "
                    "error (%s: %s) — keeping the pool", type(exc).__name__, exc)

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
            try:
                async with pool.acquire() as conn:
                    return await conn.execute(sql, *args)
            except (_AUTH_ERRORS + _CONNECTION_ERRORS) as exc:
                # As in fetch(): the token was just refreshed, so a second auth
                # failure is a real outage rather than an expiry.
                raise self._connection_failed(exc) from exc
        except _CONNECTION_ERRORS as exc:
            # Especially important for a write: without this the caller sees a 500 and
            # cannot tell whether the statement was applied. A 503 says plainly that
            # it was not.
            raise self._connection_failed(exc) from exc


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
