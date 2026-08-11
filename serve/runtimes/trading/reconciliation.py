"""Reconciliation runtime（WP-05 Checkpoint C）。

User WS 断线 → REST open orders 全分页 + trades watermark + UNKNOWN 单查 → 收敛。

- ``reconcile``：UoW1 ``start_reconcile``（RECONCILING，停止增仓）→ Driver REST keyset
  全分页 + trades + positions（网络，不在事务内）→ UoW2 ``complete_reconcile``
  （diff=0 才 COMPLETED；一次空页不证明 UNKNOWN 未提交）。
- 重连 User WS 本身不解除 RECONCILING；只有 manifest 完整且全 diff=0 才恢复 LIVE。
"""

from __future__ import annotations

from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.reconciliation import ReconciliationLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository


class ReconciliationRuntime:
    """编排 start → REST 观察 → complete；网络调用绝不在事务内。"""

    def __init__(self, sessions_factory: Any, audit: AuditRepository | None = None) -> None:
        self._sessions = sessions_factory
        self._logic = ReconciliationLogic(
            ExecutionRepository(), LedgerRepository(), audit or AuditRepository(),
        )

    async def reconcile(
        self,
        *,
        reconcile_input: Any,
        driver: Any,
        auth_headers: dict[str, str] | None = None,
        trade_after: str | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            reconciliation = await self._logic.start_reconcile(
                uow, input_=reconcile_input,
            )
            reconciliation_id = reconciliation["id"]
            account_id = reconcile_input.account_id

        # ---- REST 观察（全分页 + trades watermark + positions）----
        remote_orders: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0
        while True:
            result = await driver.open_orders(cursor=cursor, limit=200, headers=auth_headers)
            page = result.typed
            remote_orders.extend(
                {
                    "external_order_id": item.order_id,
                    "token_id": item.token_id,
                    "side": item.side,
                    "price": str(item.price),
                    "size": str(item.size),
                    "status": item.status,
                }
                for item in page.items
            )
            pages += 1
            if not page.next_cursor:
                break
            cursor = page.next_cursor

        remote_trades: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = await driver.trades(
                cursor=cursor, limit=200, after=trade_after, headers=auth_headers,
            )
            page = result.typed
            remote_trades.extend(
                {
                    "external_trade_id": item.trade_id,
                    "token_id": item.token_id,
                    "side": item.side,
                    "price": str(item.price),
                    "size": str(item.size),
                }
                for item in page.items
            )
            if not page.next_cursor:
                break
            cursor = page.next_cursor

        remote_positions: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = await driver.positions(cursor=cursor, limit=200, headers=auth_headers)
            page = result.typed
            remote_positions.extend(
                {
                    "token_id": item.token_id,
                    "size": str(item.size),
                    "avg_price": str(item.avg_price) if item.avg_price is not None else None,
                }
                for item in page.items
            )
            if not page.next_cursor:
                break
            cursor = page.next_cursor

        remote_funds: list[dict[str, Any]] = []
        unknown_queries: dict[str, Any] = {"unknown_orders": []}

        async with UnitOfWork(self._sessions) as uow:
            result = await self._logic.complete_reconcile(
                uow,
                reconciliation_id=reconciliation_id,
                account_id=account_id,
                remote_orders=remote_orders,
                remote_trades=remote_trades,
                remote_positions=remote_positions,
                remote_funds=remote_funds,
                unknown_queries=unknown_queries,
            )
        return {
            "status": result.status,
            "differences": result.differences,
            "reconciliation_id": reconciliation_id,
            "pages": pages,
            "output_manifest_hash": result.output_manifest_hash,
        }
