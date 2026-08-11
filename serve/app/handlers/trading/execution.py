"""Shadow execution handler（WP-03 Checkpoint D）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.execution import ShadowExecutionLogic
from app.schemas.trading.execution import ShadowFillInput


@dataclass(frozen=True)
class ExecutionEvent:
    kind: str  # shadow_fill | ledger_reversal | system_net
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class ExecutionHandler:
    """解析 execution event 并调用 ShadowExecutionLogic；一个 event 一次 UoW。"""

    def __init__(self, logic: ShadowExecutionLogic) -> None:
        self._logic = logic

    async def handle(
        self,
        uow: UnitOfWork,
        event: ExecutionEvent,
        *,
        portfolio_namespace: str | None = None,
        cash_asset_key: str | None = None,
    ) -> HandlerResult:
        if event.payload is None:
            return HandlerResult(False, reason="execution_event_incomplete")
        if event.kind == "shadow_fill":
            fill = ShadowFillInput(**event.payload)
            result = await self._logic.shadow_fill(
                uow, fill=fill,
                portfolio_namespace=portfolio_namespace, cash_asset_key=cash_asset_key,
            )
        elif event.kind == "ledger_reversal":
            result = await self._logic.reverse_ledger(
                uow,
                reference_transaction_id=int(event.payload["reference_transaction_id"]),
                transaction_key=str(event.payload["transaction_key"]),
            )
        elif event.kind == "system_net":
            namespace = str(event.payload.get("portfolio_namespace") or portfolio_namespace or "")
            if not namespace:
                return HandlerResult(False, reason="portfolio_namespace_required")
            result = await self._logic.system_net(uow, portfolio_namespace=namespace)
            return HandlerResult(True, result)
        else:
            raise ValueError(f"execution_event_unknown:{event.kind}")
        return HandlerResult(result.ok, result, result.reason)
