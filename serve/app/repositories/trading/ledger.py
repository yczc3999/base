"""Ledger Repository（WP-03 Checkpoint C）。

只拥有 SQL：ledger transaction/postings 与 operating cost。绝不 commit、不调用网络。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class LedgerRepository:
    """ledger SQL；不持有状态。"""

    async def insert_transaction(
        self,
        session: AsyncSession,
        *,
        transaction_key: str,
        kind: str,
        trade_decision_id: int | None,
        execution_id: int | None,
        portfolio_namespace: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.ledger_transactions "
                "(transaction_key, status, kind, trade_decision_id, execution_id, "
                " portfolio_namespace) "
                "VALUES (:k, 'PENDING', :kind, :d, :e, :ns) "
                "ON CONFLICT (transaction_key) DO NOTHING RETURNING id"
            ),
            {"k": transaction_key, "kind": kind, "d": trade_decision_id,
             "e": execution_id, "ns": portfolio_namespace},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await session.execute(
            text("SELECT id FROM trading.ledger_transactions WHERE transaction_key=:k"),
            {"k": transaction_key},
        )
        return existing.scalar_one()

    async def insert_postings(
        self,
        session: AsyncSession,
        *,
        transaction_id: int,
        postings: list[dict[str, Any]],
    ) -> None:
        """批量插入 postings；由 deferred trigger 校验每 asset 归零且 ≥2 条。"""
        if not postings:
            return
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "INSERT INTO trading.ledger_postings "
                "(transaction_id, posting_no, asset_type, asset_key, amount, counterparty) "
                "SELECT :tx, posting_no, asset_type, asset_key, amount, counterparty "
                "FROM jsonb_to_recordset(:rows) AS x("
                " posting_no int, asset_type text, asset_key text, amount numeric, counterparty text)"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"tx": transaction_id, "rows": postings},
        )
        if result.rowcount not in (-1, len(postings)):
            raise RuntimeError("ledger_postings_partial")

    async def mark_posted(
        self, session: AsyncSession, transaction_id: int, *, posted_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.ledger_transactions SET status='POSTED', posted_at=:t "
                "WHERE id=:tx AND status='PENDING'"
            ),
            {"tx": transaction_id, "t": posted_at},
        )
        return result.rowcount == 1

    async def get_transaction(
        self, session: AsyncSession, transaction_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.ledger_transactions WHERE transaction_key=:k"),
            {"k": transaction_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def postings_for_transaction(
        self, session: AsyncSession, transaction_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text("SELECT * FROM trading.ledger_postings WHERE transaction_id=:t ORDER BY posting_no"),
            {"t": transaction_id},
        )
        return _rows(result)

    async def insert_operating_cost(
        self,
        session: AsyncSession,
        *,
        cost_key: str,
        cost_kind: str,
        amount: Any,
        release_manifest_id: int | None,
        episode_id: int | None,
        trade_decision_id: int | None,
        period_start: datetime | None,
        period_end: datetime | None,
        allocation_policy: dict,
    ) -> int:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "INSERT INTO trading.operating_cost_entries "
                "(cost_key, cost_kind, amount, release_manifest_id, episode_id, trade_decision_id, "
                " period_start, period_end, allocation_policy) "
                "VALUES (:k, :kind, :amt, :rel, :ep, :d, :ps, :pe, :ap) RETURNING id"
            ).bindparams(bindparam("ap", type_=JSONB())),
            {
                "k": cost_key, "kind": cost_kind, "amt": amount, "rel": release_manifest_id,
                "ep": episode_id, "d": trade_decision_id, "ps": period_start, "pe": period_end,
                "ap": allocation_policy,
            },
        )
        return result.scalar_one()
