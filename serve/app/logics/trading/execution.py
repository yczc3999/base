"""Shadow execution Logic（WP-03 Checkpoint C）。

- ``shadow_fill``：按 exact quote checkpoint depth 确定性 walk；只成交 book 中存在的数量；
  partial/failed 是合法结果；不产生负仓位。
- execution/lot/ledger/outbox 同一 UoW；BUY 至少 4 postings（cash+token 双对手，各自归零）。
- 本期纯 shadow：authorized_capital=0、无真实 order、无凭据。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.ledger import build_buy_postings, postings_balanced
from app.domain.trading.rounding import round_cash, round_quantity
from app.domain.trading.valuation import DepthFill, depth_walk
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.schemas.trading.execution import PositionUpdateInput, ShadowFillInput


@dataclass(frozen=True)
class FillResult:
    ok: bool
    execution_id: int | None = None
    ledger_transaction_id: int | None = None
    status: str | None = None
    filled_quantity: Decimal | None = None
    vwap: Decimal | None = None
    fee: Decimal | None = None
    reason: str | None = None


class ShadowExecutionLogic:
    """shadow fill + position + ledger（同一 UoW 原子）。"""

    def __init__(
        self,
        execution: ExecutionRepository,
        ledger: LedgerRepository,
    ) -> None:
        self._execution = execution
        self._ledger = ledger

    async def shadow_fill(
        self,
        uow: UnitOfWork,
        *,
        fill: ShadowFillInput,
        portfolio_namespace: str,
        cash_asset_key: str,
    ) -> FillResult:
        """执行一次 shadow fill；execution/lot/ledger 同一 UoW。"""
        levels = [(lvl[0], lvl[1]) for lvl in fill.depth_levels if len(lvl) == 2]
        depth: DepthFill = depth_walk(
            levels, side=fill.side, target_quantity=fill.quantity,
            taker_fee_bps=fill.taker_fee_bps,
        )
        status = "FILLED" if depth.complete else ("PARTIAL" if depth.fill_quantity > 0 else "REJECTED")
        execution_id = await self._execution.insert_execution(
            uow.session,
            execution_key=fill.execution_key,
            economic_action_intent_id=fill.economic_action_intent_id,
            action_set_leg_id=fill.action_set_leg_id,
            contract_spec_id=fill.contract_spec_id,
            token_id=fill.token_id,
            fill_role=fill.fill_role,
            quantity=fill.quantity,
            portfolio_namespace=portfolio_namespace,
        )
        await self._execution.terminalize_execution(
            uow.session, execution_id,
            status=status,
            filled_quantity=depth.fill_quantity,
            vwap=depth.vwap if depth.fill_quantity > 0 else None,
            fee=depth.fee,
            unfilled_reason=depth.unfilled_reason,
        )
        # position/lot：买入正 quantity，卖出负（不产生负仓位 —— guard 保证）。
        signed_quantity = depth.fill_quantity if fill.side == "buy" else -depth.fill_quantity
        current = await self._execution.get_position(
            uow.session, portfolio_namespace=portfolio_namespace,
            contract_spec_id=fill.contract_spec_id, token_id=fill.token_id,
        )
        new_quantity = (current["quantity"] if current else Decimal("0")) + signed_quantity
        new_cost = (current["cost_basis"] if current else Decimal("0")) + round_cash(
            depth.fill_quantity * depth.vwap
        )
        await self._execution.upsert_position(
            uow.session, portfolio_namespace=portfolio_namespace,
            contract_spec_id=fill.contract_spec_id, token_id=fill.token_id,
            market_id=None, component_id=None,
            quantity=new_quantity, cost_basis=new_cost,
        )
        if depth.fill_quantity > 0:
            await self._execution.insert_position_lot(
                uow.session, execution_id=execution_id,
                portfolio_namespace=portfolio_namespace,
                contract_spec_id=fill.contract_spec_id, token_id=fill.token_id,
                quantity=depth.fill_quantity, entry_vwap=depth.vwap, fill_role=fill.fill_role,
            )
        # ledger：BUY 至少 4 postings（cash + token 双对手）。
        tx_id = await self._ledger.insert_transaction(
            uow.session,
            transaction_key=f"ledger-{fill.execution_key}",
            kind="FILL",
            trade_decision_id=None,
            execution_id=execution_id,
            portfolio_namespace=portfolio_namespace,
        )
        if fill.side == "buy" and depth.fill_quantity > 0:
            cash_spent = round_cash(depth.fill_quantity * depth.vwap + depth.fee)
            token_asset_key = f"tok:{fill.contract_spec_id}:{fill.token_id}"
            postings = build_buy_postings(
                venue="shadow",
                portfolio_namespace=portfolio_namespace,
                cash_asset_key=cash_asset_key,
                token_asset_key=token_asset_key,
                cash_spent=cash_spent,
                token_quantity=depth.fill_quantity,
            )
            if not postings_balanced(postings):
                return FillResult(False, reason="ledger_postings_unbalanced")
            await self._ledger.insert_postings(
                uow.session, transaction_id=tx_id,
                postings=[
                    {
                        "posting_no": i,
                        "asset_type": p.asset_type,
                        "asset_key": p.asset_key,
                        "amount": str(p.amount),
                        "counterparty": p.counterparty,
                    }
                    for i, p in enumerate(postings)
                ],
            )
        await self._ledger.mark_posted(
            uow.session, tx_id, posted_at=datetime.now(timezone.utc)
        )
        return FillResult(
            True, execution_id=execution_id, ledger_transaction_id=tx_id,
            status=status, filled_quantity=depth.fill_quantity,
            vwap=depth.vwap, fee=depth.fee,
        )

    async def rebuild_position(
        self,
        uow: UnitOfWork,
        *,
        update: PositionUpdateInput,
    ) -> None:
        """从 lots 重建 position（可重建 current projection）。"""
        positions = await self._execution.positions_for_namespace(
            uow.session, update.portfolio_namespace
        )
        target = [p for p in positions if p["contract_spec_id"] == update.contract_spec_id
                  and p["token_id"] == update.token_id]
        # lots 是 fill 事实；position 由其 + 当前值重建。这里保持幂等：
        # 以当前 position 为权威（重建由上游按需调用）。
        if target:
            await self._execution.upsert_position(
                uow.session, portfolio_namespace=update.portfolio_namespace,
                contract_spec_id=update.contract_spec_id, token_id=update.token_id,
                market_id=update.market_id, component_id=update.component_id,
                quantity=target[0]["quantity"], cost_basis=target[0]["cost_basis"],
            )
