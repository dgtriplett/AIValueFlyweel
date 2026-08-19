"""Account-owned portfolio membership over the shared use-case catalog."""
from __future__ import annotations

from . import accounts
from .db import db


async def account_id() -> int | None:
    return await accounts.current()


async def portfolio_condition(alias: str = "uc", *, param_index: int = 1) -> tuple[str, list]:
    """SQL condition for the current account's confirmed portfolio.

    Falls back to the legacy global `in_portfolio` column before migration 014 has
    been applied, so an app can still run while reporting a pending migration.
    """
    current = await account_id()
    if current is None:
        return f"{alias}.in_portfolio = true", []
    return (
        f"EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
        f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id)",
        [current],
    )


async def select_membership_expression(alias: str = "uc", *, param_index: int = 1) -> tuple[str, list]:
    current = await account_id()
    if current is None:
        return f"{alias}.in_portfolio", []
    return (
        f"EXISTS (SELECT 1 FROM account_portfolio_use_cases ap "
        f"WHERE ap.account_id = ${param_index} AND ap.use_case_id = {alias}.id)",
        [current],
    )


async def set_membership(use_case_id: int, in_portfolio: bool, *, actor: str, source: str = "manual") -> None:
    current = await account_id()
    if current is None:
        await db.execute(
            "UPDATE use_cases SET in_portfolio=$1, updated_at=now() WHERE id=$2",
            in_portfolio, use_case_id,
        )
        return
    if in_portfolio:
        await db.execute("""
            INSERT INTO account_portfolio_use_cases
                (account_id, use_case_id, source, created_by)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (account_id, use_case_id) DO UPDATE SET
                source=EXCLUDED.source,
                created_by=EXCLUDED.created_by,
                created_at=now()
        """, current, use_case_id, source, actor)
    else:
        await db.execute(
            "DELETE FROM account_portfolio_use_cases WHERE account_id=$1 AND use_case_id=$2",
            current, use_case_id,
        )


async def add_many(use_case_ids: list[int], *, actor: str, source: str = "manual") -> int:
    ids = sorted({int(x) for x in use_case_ids})
    if not ids:
        return 0
    current = await account_id()
    if current is None:
        await db.execute(
            "UPDATE use_cases SET in_portfolio=true, updated_at=now() WHERE id = ANY($1::int[])",
            ids,
        )
        return len(ids)
    await db.execute("""
        INSERT INTO account_portfolio_use_cases
            (account_id, use_case_id, source, created_by)
        SELECT $1, unnest($2::int[]), $3, $4
        ON CONFLICT (account_id, use_case_id) DO UPDATE SET
            source=EXCLUDED.source,
            created_by=EXCLUDED.created_by,
            created_at=now()
    """, current, ids, source, actor)
    return len(ids)
