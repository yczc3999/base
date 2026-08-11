"""Shadow execution handler（WP-03 Checkpoint D）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.execution import PrivateExecutionLogic, ShadowExecutionLogic
from app.logics.trading.reconciliation import ReconciliationLogic
from app.schemas.trading.execution import (
    CancelOrderInput,
    EnvelopeInput,
    ReconcileInput,
    ShadowFillInput,
)


@dataclass(frozen=True)
class ExecutionEvent:
    kind: str  # shadow_fill | ledger_reversal | system_net | create_envelope |
    #          # apply_submit_outcome | cancel_order | apply_fill | reconcile_start |
    #          # reconcile_complete | heartbeat
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class ExecutionHandler:
    """解析 execution event 并调用 Logic；一个 event 一次 UoW。

    WP-05 Checkpoint C：``prepare_submit`` 与 ``reconcile_rest`` 涉及网络（Driver），由
    Runtime 在 UoW 之间编排；本 Handler 只处理纯 DB 步骤。
    """

    def __init__(
        self,
        logic: ShadowExecutionLogic,
        private_logic: PrivateExecutionLogic | None = None,
        reconcile_logic: ReconciliationLogic | None = None,
    ) -> None:
        self._logic = logic
        self._private = private_logic
        self._reconcile = reconcile_logic

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
        elif event.kind == "create_envelope":
            if self._private is None:
                raise ValueError("private_logic_required")
            envelope = await self._private.create_envelope(
                uow, input_=EnvelopeInput(**event.payload)
            )
            return HandlerResult(True, envelope)
        elif event.kind == "apply_submit_outcome":
            if self._private is None:
                raise ValueError("private_logic_required")
            result = await self._private.apply_submit_outcome(
                uow,
                prepared=event.payload["prepared"],
                outcome=_OutcomeShim(event.payload.get("outcome_cls")),
                response_hash=event.payload.get("response_hash"),
                http_status=event.payload.get("http_status"),
                error_reason=event.payload.get("error_reason"),
            )
            return HandlerResult(result.ok, result, result.reason)
        elif event.kind == "cancel_order":
            if self._private is None:
                raise ValueError("private_logic_required")
            result = await self._private.cancel_order(
                uow,
                input_=CancelOrderInput(**event.payload.get("input", {})),
                outcome=_OutcomeShim(event.payload.get("outcome_cls")),
                response_hash=event.payload.get("response_hash"),
                error_reason=event.payload.get("error_reason"),
            )
            return HandlerResult(result.ok, result, result.reason)
        elif event.kind == "apply_fill":
            if self._private is None:
                raise ValueError("private_logic_required")
            result = await self._private.apply_fill(
                uow,
                order_id=int(event.payload["order_id"]),
                account_id=int(event.payload["account_id"]),
                envelope_id=int(event.payload["envelope_id"]),
                intent_id=int(event.payload["intent_id"]),
                fencing_token=int(event.payload["fencing_token"]),
                external_trade_id=str(event.payload["external_trade_id"]),
                side=str(event.payload["side"]),
                price=event.payload["price"],
                size=event.payload["size"],
                fee=event.payload.get("fee", 0),
                trade_time=_parse_dt(event.payload.get("trade_time")),
            )
            return HandlerResult(result.ok, result, result.reason)
        elif event.kind == "reconcile_start":
            if self._reconcile is None:
                raise ValueError("reconcile_logic_required")
            reconciliation = await self._reconcile.start_reconcile(
                uow, input_=ReconcileInput(**event.payload)
            )
            return HandlerResult(True, reconciliation)
        elif event.kind == "reconcile_complete":
            if self._reconcile is None:
                raise ValueError("reconcile_logic_required")
            result = await self._reconcile.complete_reconcile(
                uow,
                reconciliation_id=int(event.payload["reconciliation_id"]),
                account_id=int(event.payload["account_id"]),
                remote_orders=event.payload.get("remote_orders", []),
                remote_trades=event.payload.get("remote_trades", []),
                remote_positions=event.payload.get("remote_positions", []),
                remote_funds=event.payload.get("remote_funds", []),
                unknown_queries=event.payload.get("unknown_queries", {}),
            )
            return HandlerResult(result.ok, result, result.reason)
        elif event.kind == "heartbeat":
            # heartbeat 只记录外部调用 + workflow event（ID 链由 Runtime/Lease 维护）。
            if self._private is not None and self._reconcile is not None:
                pass
            return HandlerResult(True, {"heartbeat": True})
        else:
            raise ValueError(f"execution_event_unknown:{event.kind}")
        return HandlerResult(result.ok, result, result.reason)


class _OutcomeShim:
    """把 dict/字符串 outcome 包成具备 ``cls``/``order_id`` 的最小对象。"""

    def __init__(self, value: Any) -> None:
        if isinstance(value, dict):
            self.cls = value.get("cls", "UNKNOWN")
            self.order_id = value.get("order_id")
        else:
            self.cls = value or "UNKNOWN"
            self.order_id = None


def _parse_dt(value: Any):
    from datetime import datetime, timezone

    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
