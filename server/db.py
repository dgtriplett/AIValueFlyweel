"""Lakebase Postgres connection pool with dual-mode auth and token refresh.

- If ``PGHOST`` is unset, runs in graceful demo mode (queries return empties).
- OAuth tokens expire (~1h); on an auth failure we recreate the pool with a
  fresh token. A background task also proactively refreshes every ~45 minutes.
"""
import os
import asyncio
import logging
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


class DatabasePool:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._demo_mode = False
        self._lock = asyncio.Lock()

    @property
    def is_demo_mode(self) -> bool:
        return self._demo_mode

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        if not os.environ.get("PGHOST"):
            self._demo_mode = True
            return None

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
                self._demo_mode = False
            except Exception as exc:  # noqa: BLE001
                # Demo mode is a legitimate state (no Lakebase configured), but
                # arriving here with PGHOST set means a real failure the operator
                # needs to see, so log at warning with the cause.
                logger.warning(
                    "Lakebase connection failed (%s: %s) — entering demo mode; "
                    "reads return empty and writes are no-ops",
                    type(exc).__name__, exc)
                self._demo_mode = True
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

    # -- query helpers ------------------------------------------------------
    async def fetch(self, sql: str, *args):
        _charge_budget()
        pool = await self.get_pool()
        if pool is None:
            return []
        try:
            async with pool.acquire() as conn:
                return await conn.fetch(sql, *args)
        except _AUTH_ERRORS:
            await self.refresh_token()
            pool = await self.get_pool()
            if pool is None:
                return []
            async with pool.acquire() as conn:
                return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def execute(self, sql: str, *args):
        _charge_budget()
        pool = await self.get_pool()
        if pool is None:
            return None
        try:
            async with pool.acquire() as conn:
                return await conn.execute(sql, *args)
        except _AUTH_ERRORS:
            await self.refresh_token()
            pool = await self.get_pool()
            if pool is None:
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
