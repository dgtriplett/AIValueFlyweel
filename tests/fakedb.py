"""A minimal stand-in for `server.db.db` for tests.

`readiness.py` (and friends) call `db.fetch(sql, *args)` and treat the result as
a sequence of mapping-like rows. FakeDB matches queries by substring so a test
can say "when the query mentions uc_requires_domain, return these rows" without
reimplementing SQL.

This is deliberately dumber than a real database. The goal is to exercise the
*Python* branching in readiness scoring — path selection, necessity folding,
classify() thresholds — not to re-test Postgres.
"""
from __future__ import annotations

import asyncio


class Row(dict):
    """dict that also supports the attribute-ish access asyncpg records allow.

    asyncpg Records support `r["col"]`, which is what the app code uses, so a
    dict subclass is sufficient. Keeping a named type makes test intent clearer.
    """


class FakeDB:
    """Substring-routed fake. Later-registered patterns win on ties.

    Usage:
        db = FakeDB()
        db.on("uc_requires_domain", [Row(use_case_id=1, ...)])
        db.on("FROM use_cases uc", [Row(...)])
    """

    def __init__(self, *, has_pool: bool = False) -> None:
        self._routes: list[tuple[str, list]] = []
        self.queries: list[str] = []
        # `has_pool=True` makes the fake claim a live connection, which is what
        # code paths gated on "is Lakebase reachable" check. Left False by default
        # so unit tests see the demo-mode branch unless they opt in.
        self._has_pool = has_pool
        self.is_demo_mode = not has_pool

    def on(self, substring: str, rows: list) -> "FakeDB":
        self._routes.append((substring, rows))
        return self

    async def get_pool(self):
        """Stand in for the asyncpg pool. Returns a truthy sentinel, or None to
        mimic an unconfigured Lakebase."""
        return object() if self._has_pool else None

    # -- interface used by the app -----------------------------------------
    async def fetch(self, sql: str, *args):
        self.queries.append(sql)
        best: list | None = None
        best_len = -1
        for needle, rows in self._routes:
            # Longest matching pattern wins, so a specific route beats a generic
            # one regardless of registration order.
            if needle in sql and len(needle) > best_len:
                best, best_len = rows, len(needle)
        return best if best is not None else []

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def execute(self, sql: str, *args):
        self.queries.append(sql)
        return "OK"


def run(coro):
    """Run a coroutine to completion (tests are sync; the app is async)."""
    return asyncio.run(coro)
