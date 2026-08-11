"""Ledger Repository（WP-03 Checkpoint C）。

只拥有 SQL：ledger transaction/postings 与 operating cost。绝不 commit、不调用网络。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
        reference_transaction_id: int | None = None,
        account_id: int | None = None,
        envelope_id: int | None = None,
        order_id: int | None = None,
        trade_id: int | None = None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.ledger_transactions "
                "(transaction_key, status, kind, trade_decision_id, execution_id, "
                " portfolio_namespace, reference_transaction_id, account_id, envelope_id, "
                " order_id, trade_id) "
                "VALUES (:k, 'PENDING', :kind, :d, :e, :ns, :ref, :acct, :env, :o, :t) "
                "ON CONFLICT (transaction_key) DO NOTHING RETURNING id"
            ),
            {"k": transaction_key, "kind": kind, "d": trade_decision_id,
             "e": execution_id, "ns": portfolio_namespace, "ref": reference_transaction_id,
             "acct": account_id, "env": envelope_id, "o": order_id, "t": trade_id},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing_result = await session.execute(
            text("SELECT * FROM trading.ledger_transactions WHERE transaction_key=:k FOR UPDATE"),
            {"k": transaction_key},
        )
        rows = _rows(existing_result)
        if not rows:
            raise RuntimeError("ledger_transaction_claim_lost")
        existing = rows[0]
        expected = {
            "kind": kind, "trade_decision_id": trade_decision_id, "execution_id": execution_id,
            "portfolio_namespace": portfolio_namespace,
            "reference_transaction_id": reference_transaction_id,
        }
        for field, value in expected.items():
            if existing[field] != value:
                raise RuntimeError(f"ledger_idempotency_mismatch:{field}")
        return existing["id"]

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

    async def transaction_for_execution(
        self, session: AsyncSession, execution_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.ledger_transactions WHERE execution_id=:e"),
            {"e": execution_id},
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

    async def create_reversal(
        self,
        session: AsyncSession,
        *,
        reference_transaction_id: int,
        transaction_key: str,
        posted_at: datetime,
    ) -> tuple[int, bool]:
        """Create an exact inverse of one POSTED transaction, idempotently."""
        original_result = await session.execute(
            text("SELECT * FROM trading.ledger_transactions WHERE id=:tx FOR SHARE"),
            {"tx": reference_transaction_id},
        )
        original_rows = _rows(original_result)
        if not original_rows or original_rows[0]["status"] != "POSTED":
            raise RuntimeError("ledger_reversal_source_not_posted")
        original = original_rows[0]
        existing = await self.get_transaction(session, transaction_key)
        if existing is not None:
            if (existing["kind"] != "REVERSAL"
                    or existing["reference_transaction_id"] != reference_transaction_id
                    or existing["portfolio_namespace"] != original["portfolio_namespace"]):
                raise RuntimeError("ledger_reversal_idempotency_mismatch")
            if existing["status"] != "POSTED":
                raise RuntimeError("ledger_reversal_pending_conflict")
            return existing["id"], False
        tx_id = await self.insert_transaction(
            session,
            transaction_key=transaction_key,
            kind="REVERSAL",
            trade_decision_id=original["trade_decision_id"],
            execution_id=None,
            portfolio_namespace=original["portfolio_namespace"],
            reference_transaction_id=reference_transaction_id,
        )
        source = await self.postings_for_transaction(session, reference_transaction_id)
        if not source:
            raise RuntimeError("ledger_reversal_source_empty")
        await self.insert_postings(
            session,
            transaction_id=tx_id,
            postings=[
                {
                    "posting_no": row["posting_no"],
                    "asset_type": row["asset_type"],
                    "asset_key": row["asset_key"],
                    "amount": str(-Decimal(str(row["amount"]))),
                    "counterparty": row["counterparty"],
                }
                for row in source
            ],
        )
        if not await self.mark_posted(session, tx_id, posted_at=posted_at):
            raise RuntimeError("ledger_reversal_post_conflict")
        return tx_id, True

    async def system_net(
        self, session: AsyncSession, *, portfolio_namespace: str
    ) -> dict[str, Any]:
        """Return realized system net only when portfolio token postings are closed."""
        trading = (
            await session.execute(
                text(
                    "SELECT COALESCE(sum(p.amount),0) FROM trading.ledger_postings p "
                    "JOIN trading.ledger_transactions t ON t.id=p.transaction_id "
                    "WHERE t.status='POSTED' AND t.portfolio_namespace=:ns "
                    "AND p.asset_type='CASH' AND p.counterparty=:ns"
                ),
                {"ns": portfolio_namespace},
            )
        ).scalar_one()
        operating = (
            await session.execute(
                text(
                    "SELECT COALESCE(sum(c.amount),0) FROM trading.operating_cost_entries c "
                    "LEFT JOIN trading.trade_decisions d ON d.id=c.trade_decision_id "
                    "LEFT JOIN trading.forecast_episodes e "
                    "  ON e.id=COALESCE(c.episode_id,d.episode_id) "
                    "WHERE (d.id IS NOT NULL AND ('shadow-' || d.experiment_variant)=:ns) "
                    "   OR (d.id IS NULL AND e.id IS NOT NULL "
                    "       AND ('shadow-' || e.experiment_variant)=:ns) "
                    "   OR (d.id IS NULL AND e.id IS NULL "
                    "       AND c.allocation_policy->>'portfolio_namespace'=:ns)"
                ),
                {"ns": portfolio_namespace},
            )
        ).scalar_one()
        open_asset_groups = (
            await session.execute(
                text(
                    "SELECT count(*) FROM ("
                    " SELECT p.asset_key FROM trading.ledger_postings p "
                    " JOIN trading.ledger_transactions t ON t.id=p.transaction_id "
                    " WHERE t.status='POSTED' AND t.portfolio_namespace=:ns "
                    "   AND p.asset_type='TOKEN' AND p.counterparty=:ns "
                    " GROUP BY p.asset_key HAVING sum(p.amount)<>0"
                    ") open_tokens"
                ),
                {"ns": portfolio_namespace},
            )
        ).scalar_one()
        trading_cash_flow = Decimal(str(trading))
        operating_cost = Decimal(str(operating))
        realized = int(open_asset_groups) == 0
        return {
            "realized": realized,
            "trading_cash_flow": trading_cash_flow,
            "open_token_asset_groups": int(open_asset_groups),
            "trading_pnl": trading_cash_flow if realized else None,
            "operating_cost": operating_cost,
            "system_net_profit": trading_cash_flow - operating_cost if realized else None,
        }
