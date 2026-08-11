"""Shadow execution Repository（WP-03 Checkpoint C）。

只拥有 SQL：execution 生命周期、position projection（乐观锁）、position lots。
绝不 commit、不调用网络、不做业务判断。
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
        quote_checkpoint_id: int,
    ) -> dict[str, Any]:
        """Claim an execution key and exact-match an existing retry.

        The returned mapping contains ``inserted``.  A reused key with different immutable
        material is rejected instead of being rebound to the old execution.
        """
        result = await session.execute(
            text(
                "INSERT INTO trading.executions "
                "(execution_key, economic_action_intent_id, action_set_leg_id, contract_spec_id, "
                " token_id, fill_role, quantity, portfolio_namespace, quote_checkpoint_id) "
                "VALUES (:k, :i, :leg, :cs, :tk, :fr, :q, :ns, :checkpoint) "
                "ON CONFLICT (execution_key) DO NOTHING RETURNING *"
            ),
            {"k": execution_key, "i": economic_action_intent_id, "leg": action_set_leg_id,
             "cs": contract_spec_id, "tk": token_id, "fr": fill_role, "q": quantity,
             "ns": portfolio_namespace, "checkpoint": quote_checkpoint_id},
        )
        rows = _rows(result)
        if rows:
            rows[0]["inserted"] = True
            return rows[0]
        existing_result = await session.execute(
            text("SELECT * FROM trading.executions WHERE execution_key=:k FOR UPDATE"),
            {"k": execution_key},
        )
        existing_rows = _rows(existing_result)
        if not existing_rows:
            raise RuntimeError("execution_claim_lost")
        existing = existing_rows[0]
        expected = {
            "economic_action_intent_id": economic_action_intent_id,
            "action_set_leg_id": action_set_leg_id,
            "contract_spec_id": contract_spec_id,
            "token_id": token_id,
            "fill_role": fill_role,
            "quantity": Decimal(str(quantity)),
            "portfolio_namespace": portfolio_namespace,
            "quote_checkpoint_id": quote_checkpoint_id,
        }
        for field, value in expected.items():
            actual = existing[field]
            if field == "quantity":
                actual = Decimal(str(actual))
            if actual != value:
                raise RuntimeError(f"execution_idempotency_mismatch:{field}")
        existing["inserted"] = False
        return existing

    async def fill_material(
        self,
        session: AsyncSession,
        *,
        economic_action_intent_id: int,
        action_set_leg_id: int,
    ) -> dict[str, Any] | None:
        """Load the complete immutable execution chain; no payload economics are trusted."""
        result = await session.execute(
            text(
                "SELECT i.id AS intent_id, i.status AS intent_status, i.ttl_at, "
                "       i.trade_decision_id AS intent_decision_id, i.action_set_id, "
                "       a.trade_decision_id, a.disposition, "
                "       l.id AS leg_id, l.contract_spec_id, l.token_id, l.leg_role, "
                "       l.quantity, l.signed_quantity, "
                "       d.status AS decision_status, d.experiment_variant, d.release_manifest_id, "
                "       pt.token_id AS external_token_id, pt.market_id, fcv.component_id, "
                "       qb.checkpoint_id, qb.checkpoint_received_at, qb.stale_at, "
                "       bc.completeness AS checkpoint_complete, bc.validity AS checkpoint_validity, "
                "       es.status AS execution_spec_status, es.content AS execution_spec, "
                "       cp.status AS permission_status, cp.mode AS permission_mode, "
                "       cp.authorized_capital, cp.kill_switch, cp.capability, "
                "       oc.content AS objective_content "
                "FROM trading.economic_action_intents i "
                "JOIN trading.action_sets a ON a.id=i.action_set_id "
                "JOIN trading.action_set_legs l ON l.action_set_id=a.id AND l.id=:leg "
                "JOIN trading.trade_decisions d ON d.id=a.trade_decision_id "
                "JOIN trading.pm_tokens pt ON pt.id=l.token_id "
                "JOIN trading.forecast_episodes fe ON fe.id=d.episode_id "
                "JOIN trading.forecast_component_versions fcv ON fcv.id=fe.component_version_id "
                "JOIN trading.pm_quote_bindings qb "
                "  ON qb.trade_decision_id=d.id AND qb.token_id=pt.token_id "
                "JOIN trading.pm_book_checkpoints bc "
                "  ON bc.id=qb.checkpoint_id AND bc.received_at=qb.checkpoint_received_at "
                " AND bc.token_id=qb.token_id "
                "JOIN trading.execution_spec_versions es ON es.id=d.execution_spec_version_id "
                "JOIN trading.capital_permission_manifests cp "
                "  ON cp.id=d.capital_permission_manifest_id "
                "JOIN trading.strategy_objective_contracts oc ON oc.id=d.objective_contract_id "
                "WHERE i.id=:intent"
            ),
            {"intent": economic_action_intent_id, "leg": action_set_leg_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def checkpoint_levels(
        self,
        session: AsyncSession,
        *,
        checkpoint_id: int,
        checkpoint_received_at: datetime,
        side: str,
        max_levels: int,
    ) -> list[tuple[Any, Any]]:
        book_side = "ask" if side == "buy" else "bid"
        result = await session.execute(
            text(
                "SELECT price,size FROM trading.pm_book_levels "
                "WHERE checkpoint_id=:checkpoint AND received_at=:received AND side=:side "
                "ORDER BY ordinal, price LIMIT :limit"
            ),
            {"checkpoint": checkpoint_id, "received": checkpoint_received_at,
             "side": book_side, "limit": max_levels},
        )
        return [(row[0], row[1]) for row in result.fetchall()]

    async def acquire_position_lock(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        component_id: int | None,
        market_id: int | None,
        contract_spec_id: int,
        token_id: int,
    ) -> None:
        key = f"position:{portfolio_namespace}:{component_id or 0}:{market_id or 0}:{contract_spec_id}:{token_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def acquire_execution_lock(
        self, session: AsyncSession, *, economic_action_intent_id: int, action_set_leg_id: int
    ) -> None:
        key = f"execution:{economic_action_intent_id}:{action_set_leg_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def execution_for_leg(
        self, session: AsyncSession, *, economic_action_intent_id: int, action_set_leg_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.executions WHERE economic_action_intent_id=:intent "
                "AND action_set_leg_id=:leg ORDER BY id LIMIT 1 FOR UPDATE"
            ),
            {"intent": economic_action_intent_id, "leg": action_set_leg_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

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

    async def get_execution_by_key(
        self, session: AsyncSession, execution_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.executions WHERE execution_key=:key"),
            {"key": execution_key},
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
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.positions "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk" +
                (" FOR UPDATE" if for_update else "")
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
            contract_spec_id=contract_spec_id, token_id=token_id, for_update=True,
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
        if (existing["market_id"] is not None and market_id is not None
                and existing["market_id"] != market_id):
            raise RuntimeError("position_market_mismatch")
        if (existing["component_id"] is not None and component_id is not None
                and existing["component_id"] != component_id):
            raise RuntimeError("position_component_mismatch")
        result = await session.execute(
            text(
                "UPDATE trading.positions SET quantity=:q, cost_basis=:cb, "
                "market_id=COALESCE(market_id,:m), component_id=COALESCE(component_id,:cmp), "
                "version=version+1 "
                "WHERE portfolio_namespace=:ns AND contract_spec_id=:cs AND token_id=:tk "
                "AND version=:old_version"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id,
             "q": quantity, "cb": cost_basis, "m": market_id, "cmp": component_id,
             "old_version": existing["version"]},
        )
        if result.rowcount != 1:
            raise RuntimeError("position_version_conflict")

    async def position_lots_for(
        self,
        session: AsyncSession,
        *,
        portfolio_namespace: str,
        contract_spec_id: int,
        token_id: int,
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT lot.quantity,lot.entry_vwap,lot.fill_role,lot.created_at,lot.id "
                "FROM trading.position_lots lot WHERE lot.portfolio_namespace=:ns "
                "AND lot.contract_spec_id=:cs AND lot.token_id=:tk "
                "ORDER BY lot.created_at,lot.id"
            ),
            {"ns": portfolio_namespace, "cs": contract_spec_id, "tk": token_id},
        )
        return _rows(result)

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
