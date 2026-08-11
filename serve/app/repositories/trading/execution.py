"""Shadow execution Repository（WP-03 Checkpoint C）。

只拥有 SQL：execution 生命周期、position projection（乐观锁）、position lots。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class ExecutionRepository:
    """shadow execution SQL；不持有状态。"""

    async def insert_execution(
        self,
        session: AsyncSession,
        *,
        execution_key: str,
        economic_action_intent_id: int,
        action_set_leg_id: int,
        contract_spec_id: int,
        token_id: int,
        fill_role: str,
        quantity: Any,
        portfolio_namespace: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.executions "
                "(execution_key, economic_action_intent_id, action_set_leg_id, contract_spec_id, "
                " token_id, fill_role, quantity, portfolio_namespace) "
                "VALUES (:k, :i, :leg, :cs, :tk, :fr, :q, :ns) "
                "ON CONFLICT (execution_key) DO NOTHING RETURNING id"
            ),
            {"k": execution_key, "i": economic_action_intent_id, "leg": action_set_leg_id,
             "cs": contract_spec_id, "tk": token_id, "fr": fill_role, "q": quantity, "ns": portfolio_namespace},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await session.execute(
            text("SELECT id FROM trading.executions WHERE execution_key=:k"),
            {"k": execution_key},
        )
        return existing.scalar_one()

    async def terminalize_execution(
        self,
        session: AsyncSession,
        execution_id: int,
        *,
        status: str,
        filled_quantity: Any,
        vwap: Any,
        fee: Any,
        unfilled_reason: str | None,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.executions SET status=:s, filled_quantity=:fq, vwap=:vw, "
                "fee=:fee, unfilled_reason=:ur WHERE id=:e AND status='PENDING'"
            ),
            {"e": execution_id, "s": status, "fq": filled_quantity, "vw": vwap,
             "fee": fee, "ur": unfilled_reason},
        )
        return result.rowcount == 1

    async def get_execution(
        self, session: AsyncSession, execution_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.executions WHERE id=:e"),
            {"e": execution_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_position_lot(
        self,
        session: AsyncSession,
        *,
        execution_id: int,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
        quantity: Any,
        entry_vwap: Any,
        fill_role: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.position_lots "
                "(execution_id, portfolio_namespace, contract_spec_id, token_id, quantity, "
                " entry_vwap, fill_role) VALUES (:e, :ns, :cs, :tk, :q, :vw, :fr)"
            ),
            {"e": execution_id, "ns": portfolio_namespace, "cs": contract_spec_id,
             "tk": token_id, "q": quantity, "vw": entry_vwap, "fr": fill_role},
        )

    async def get_position(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.positions "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def upsert_position(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
        market_id: int | None,
        component_id: int | None,
        quantity: Any,
        cost_basis: Any,
    ) -> None:
        """乐观锁 upsert：不存在则插入；存在则按 version 条件更新（不覆盖并发写）。"""
        existing = await self.get_position(
            session, portfolio_namespace=portfolio_namespace,
            contract_spec_id=contract_spec_id, token_id=token_id,
        )
        if existing is None:
            await session.execute(
                text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace, contract_spec_id, token_id, market_id, component_id, "
                    " quantity, cost_basis) "
                    "VALUES (:ns, :cs, :tk, :m, :cmp, :q, :cb)"
                ),
                {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id,
                 "m": market_id, "cmp": component_id, "q": quantity, "cb": cost_basis},
            )
            return
        await session.execute(
            text(
                "UPDATE trading.positions SET quantity=:q, cost_basis=:cb, version=version+1 "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk "
                "AND version=:old_version"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id,
             "q": quantity, "cb": cost_basis, "old_version": existing["version"]},
        )

    async def positions_for_namespace(
        self, session: AsyncSession, portfolio_namespace: str
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.positions WHERE portfolio_namespace=:ns "
                "ORDER BY contract_spec_id, token_id"
            ),
            {"ns": portfolio_namespace},
        )
        return _rows(result)
