"""Settlement / label handler（WP-04 Checkpoint C）。

Handler 只解析一个 typed event、调用一个 Logic/UoW、返回 completion。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.settlement import SettlementLogic
from app.schemas.trading.settlement import LabelRevisionInput


@dataclass(frozen=True)
class SettlementEvent:
    kind: str  # label_revision | create_cluster | check_split_integrity
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class SettlementHandler:
    """解析 settlement event 并调用 SettlementLogic；一个 event 一次 UoW。"""

    def __init__(self, logic: SettlementLogic) -> None:
        self._logic = logic

    async def handle(
        self, uow: UnitOfWork, event: SettlementEvent
    ) -> HandlerResult:
        kind = event.kind
        if kind == "label_revision":
            if event.payload is None:
                return HandlerResult(False, reason="settlement_event_incomplete")
            input_ = LabelRevisionInput(**event.payload)
            result = await self._logic.audit_label_revision(uow, input_=input_)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "create_cluster":
            if event.payload is None:
                return HandlerResult(False, reason="settlement_event_incomplete")
            payload = event.payload
            result = await self._logic.create_cluster(
                uow,
                split=str(payload["split"]),
                time_block_start=datetime.fromisoformat(payload["time_block_start"]),
                time_block_end=datetime.fromisoformat(payload["time_block_end"]),
                horizon=str(payload["horizon"]),
                contract_spec_ids=[int(v) for v in payload["contract_spec_ids"]],
                token_ids=[int(v) for v in payload["token_ids"]],
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "check_split_integrity":
            result = await self._logic.check_split_integrity(uow)
            return HandlerResult(result.ok, result, result.reason)
        raise ValueError(f"settlement_event_unknown:{kind}")
