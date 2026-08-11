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
    kind: str  # shadow_fill
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
        portfolio_namespace: str,
        cash_asset_key: str,
    ) -> HandlerResult:
        if event.kind != "shadow_fill":
            raise ValueError(f"execution_event_unknown:{event.kind}")
        if event.payload is None:
            return HandlerResult(False, reason="execution_event_incomplete")
        fill = ShadowFillInput(**event.payload)
        result = await self._logic.shadow_fill(
            uow, fill=fill,
            portfolio_namespace=portfolio_namespace, cash_asset_key=cash_asset_key,
        )
        return HandlerResult(result.ok, result, result.reason)
