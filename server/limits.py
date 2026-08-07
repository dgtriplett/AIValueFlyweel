"""Per-actor rate limits and per-request query budgets.

WHAT THIS PROTECTS AGAINST
--------------------------
The expensive endpoints in this app are expensive in ways the caller does not see:

  - Every LLM endpoint (chat, research, generation, classification) costs real
    money per call, and the chat loop can make up to MAX_TOOL_ROUNDS model calls
    plus a database query per tool call in a SINGLE request.
  - `/ingestion/bootstrap` and `/inventory/artifacts/sync` sweep system tables
    through a shared SQL warehouse. A few concurrent runs are enough to make the
    warehouse the bottleneck for everyone else using it, including the customer's
    own workloads.
  - `/live/sync` and `/live/sync-genie` write across the whole portfolio.

None of this is an attack scenario. It is a workshop with fifteen people clicking
"Research this company" at once, or a leaning-on-refresh reflex, or a UI bug
retrying in a loop. The failure mode without limits is a warehouse queue everyone
notices and a token bill nobody predicted.

WHY IN-PROCESS AND NOT IN LAKEBASE
----------------------------------
A counter in Postgres would be shared across workers and exact. It would also add
a database round-trip to every request in order to protect the database — and the
limits here are generous enough that per-worker accounting is fine: with N workers
the effective ceiling is N x the limit, which still bounds the problem by a small
constant. Correctness costs more than it buys.

The consequence is honest and documented: these are guard rails against accidental
load, not a quota system, and not a security control.

WHY A TOKEN BUCKET
------------------
A fixed window lets someone spend the whole allowance in the last second of one
window and again in the first second of the next — a 2x burst at the boundary. A
bucket refills continuously, so a burst is bounded by the bucket size and the
sustained rate is what you configured. It also permits a genuine short burst,
which matters here: opening the discovery tab legitimately fires several requests
at once.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Set RATE_LIMITS=off to disable entirely. Present for a demo where a presenter is
# clicking fast on purpose and a 429 would be worse than the load.
LIMITS_ENABLED = (os.environ.get("RATE_LIMITS", "on").strip().lower()
                  not in ("off", "false", "0", "no"))


@dataclass
class Bucket:
    """One actor's allowance for one limit class."""

    capacity: float
    refill_per_second: float
    tokens: float
    last_refill: float

    def take(self, now: float, cost: float = 1.0) -> tuple[bool, float]:
        """Try to spend `cost`. Returns (allowed, retry_after_seconds)."""
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity,
                              self.tokens + elapsed * self.refill_per_second)
            self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        deficit = cost - self.tokens
        return False, deficit / self.refill_per_second


@dataclass
class Limit:
    """A named limit class: `burst` requests at once, `per_minute` sustained."""

    name: str
    burst: int
    per_minute: float
    # Actor -> bucket. Bounded by _MAX_TRACKED_ACTORS below.
    buckets: dict[str, Bucket] = field(default_factory=dict)

    def check(self, actor: str, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        bucket = self.buckets.get(actor)
        if bucket is None:
            if len(self.buckets) >= _MAX_TRACKED_ACTORS:
                _evict_stale(self.buckets, now)
            bucket = Bucket(capacity=float(self.burst),
                            refill_per_second=self.per_minute / 60.0,
                            tokens=float(self.burst), last_refill=now)
            self.buckets[actor] = bucket
        return bucket.take(now, cost)


# A bound on the actor table so a pathological caller varying its identity header
# cannot grow it without limit. Far above any real user count for this app.
_MAX_TRACKED_ACTORS = 2000
# Buckets idle this long are full anyway, so dropping them changes no decision.
_STALE_AFTER_SECONDS = 3600


def _evict_stale(buckets: dict[str, Bucket], now: float) -> None:
    for actor in [a for a, b in buckets.items()
                  if now - b.last_refill > _STALE_AFTER_SECONDS]:
        del buckets[actor]
    if len(buckets) >= _MAX_TRACKED_ACTORS:
        # Still full of active actors: drop the least recently used. Better to
        # under-limit someone than to grow memory without bound.
        for actor in sorted(buckets, key=lambda a: buckets[a].last_refill)[:200]:
            del buckets[actor]


# --- Limit classes ---------------------------------------------------------
# Numbers chosen from what the UI legitimately does, then roughly doubled. The
# goal is that no honest user ever sees a 429.

LIMITS: dict[str, Limit] = {
    # A conversational turn. Up to MAX_TOOL_ROUNDS model calls each.
    "chat": Limit("chat", burst=6, per_minute=20),
    # Company research: the most expensive single call in the app (two large
    # prompts, web-scale reasoning, 34 assumptions).
    "research": Limit("research", burst=3, per_minute=6),
    # Use-case generation and taxonomy classification.
    "generate": Limit("generate", burst=4, per_minute=12),
    # Warehouse sweeps. Deliberately tight: these are minutes-long, and a second
    # concurrent run by the same actor is almost always a double-click.
    "sweep": Limit("sweep", burst=2, per_minute=4),
    # Ordinary writes. High enough to be invisible; catches a retry loop.
    "write": Limit("write", burst=30, per_minute=120),
    # Confirm attempts. Tokens are 256-bit so guessing is not the concern —
    # this bounds a client stuck retrying a consumed token.
    "confirm": Limit("confirm", burst=10, per_minute=60),
}


def actor_key(request: Request) -> str:
    """Identify the caller for accounting.

    Prefers the Databricks Apps identity headers, which are set by the platform
    and cannot be forged by the browser. Falls back to the peer address; a shared
    NAT would then share an allowance, which is acceptable for guard rails and is
    why the limits are generous.
    """
    for header in ("x-forwarded-email", "x-forwarded-user"):
        value = request.headers.get(header)
        if value:
            return value.strip().lower()
    # X-Forwarded-For is client-controllable, so it is used only as a last resort
    # and only its first hop.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host}" if client else "anonymous"


def enforce(limit_name: str, request: Request, *, cost: float = 1.0) -> None:
    """Raise 429 if `request`'s actor has exhausted `limit_name`.

    Call at the TOP of a handler, before any expensive work.
    """
    if not LIMITS_ENABLED:
        return
    limit = LIMITS.get(limit_name)
    if limit is None:  # pragma: no cover - programming error, fail open
        logger.warning("unknown rate limit %r — allowing request", limit_name)
        return

    actor = actor_key(request)
    with _LOCK:
        allowed, retry_after = limit.check(actor, cost)
    if allowed:
        return

    wait = max(1, int(retry_after + 0.999))
    logger.warning("rate limit %s exhausted by %s — retry in %ss",
                   limit_name, actor, wait)
    raise HTTPException(
        429,
        detail=f"Too many {limit_name} requests. This is a guard rail on an "
               f"expensive operation, not a quota — wait {wait}s and try again.",
        headers={"Retry-After": str(wait)},
    )


# One lock for all buckets. Contention is irrelevant (the critical section is a
# few float operations) and it keeps the invariant simple: no two coroutines
# mutate a bucket at once. The handlers are async but run on one event-loop
# thread, so this is really a guard against a threaded worker configuration.
_LOCK = Lock()


def limiter(limit_name: str, *, cost: float = 1.0):
    """FastAPI dependency form: `dependencies=[Depends(limiter("chat"))]`.

    Preferred over an in-body call when the whole endpoint is rate-limited, since
    it runs before the body is parsed or validated.
    """
    def dependency(request: Request) -> None:
        enforce(limit_name, request, cost=cost)
    return dependency


def reset() -> None:
    """Clear all buckets. For tests, and for a demo reset."""
    with _LOCK:
        for limit in LIMITS.values():
            limit.buckets.clear()


def snapshot() -> dict:
    """Current state, for /api/health and debugging a 429 report."""
    with _LOCK:
        return {
            "enabled": LIMITS_ENABLED,
            "limits": {
                name: {"burst": limit.burst, "per_minute": limit.per_minute,
                       "tracked_actors": len(limit.buckets)}
                for name, limit in LIMITS.items()
            },
        }


# --- Per-request query budget ----------------------------------------------

class QueryBudget:
    """Bounds how many database queries ONE request may issue.

    The chat loop is the reason this exists. Its cost is driven by the model's
    choices: MAX_TOOL_ROUNDS bounds the model calls, but a single round can carry
    several tool calls, each running its own queries — so a turn's database load
    has no fixed ceiling. That is fine until a model starts looping over a large
    list, at which point one request quietly monopolizes the connection pool and
    every other user's request waits on it.

    Exceeding the budget is a bug or a pathological model turn, not user error, so
    it raises rather than truncating: a partial answer presented as complete is
    worse than a clear failure.
    """

    def __init__(self, limit: int, label: str = "request"):
        self.limit = limit
        self.label = label
        self.used = 0

    def charge(self, count: int = 1) -> None:
        self.used += count
        if self.used > self.limit:
            raise BudgetExceeded(
                f"{self.label} exceeded its query budget "
                f"({self.used} > {self.limit}). This is a safety bound on one "
                "request's database load; try a narrower question.")

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class BudgetExceeded(RuntimeError):
    """A single request tried to issue more queries than its budget allows."""


# The budget in force for the current request, or None. A ContextVar for the same
# reason the request id is one: under async concurrency a module global would have
# one request's queries charged to another's budget.
current_budget: ContextVar[QueryBudget | None] = ContextVar(
    "current_budget", default=None)


@contextmanager
def query_budget(limit: int, label: str = "request"):
    """Charge every db query inside this block against one budget.

    Usage:
        with query_budget(CHAT_QUERY_BUDGET, "This conversation turn") as budget:
            ...
            logger.info("used %d queries", budget.used)
    """
    budget = QueryBudget(limit, label)
    token = current_budget.set(budget)
    try:
        yield budget
    finally:
        current_budget.reset(token)


# One chat turn: MAX_TOOL_ROUNDS (7) rounds, a handful of tool calls each, a few
# queries per tool, plus history load and turn saves. 120 is comfortably above a
# legitimate turn and well below "monopolizing the pool".
CHAT_QUERY_BUDGET = 120
