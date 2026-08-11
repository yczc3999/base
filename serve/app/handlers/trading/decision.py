"""Decision handler（WP-03 Checkpoint D）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
不实现 Gate 算法；G7A/G7B/terminal 编排在 runtime。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.decision import DecisionLogic
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)


@dataclass(frozen=True)
class DecisionEvent:
    kind: str  # create / reveal / market_relative / g7a / g7b / terminal
    trade_decision_id: int | None = None
    episode_id: int | None = None
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class DecisionHandler:
    """解析 decision event 并调用 DecisionLogic；一个 event 一次 UoW。"""

    def __init__(self, logic: DecisionLogic) -> None:
        self._logic = logic

    async def handle(
        self,
        uow: UnitOfWork,
        event: DecisionEvent,
        *,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> HandlerResult:
        kind = event.kind
        if kind == "create":
            if event.episode_id is None or event.payload is None:
                return HandlerResult(False, reason="decision_event_incomplete")
            result = await self._logic.create_decision(
                uow, episode_id=event.episode_id,
                trigger_at=datetime.fromisoformat(event.payload["trigger_at"]),
                experiment_variant=event.payload.get("experiment_variant", "champion"),
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "reveal":
            result = await self._logic.reveal(
                uow, trade_decision_id=event.trade_decision_id,
                quote_reveal_at=datetime.fromisoformat(event.payload["quote_reveal_at"]),
                quotes=event.payload["quotes"],
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "market_relative":
            input_ = MarketRelativeInput(**event.payload)
            result = await self._logic.market_relative(
                uow, trade_decision_id=event.trade_decision_id, input_=input_,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "g7a":
            if policy_hash is None or version_manifest_id is None:
                return HandlerResult(False, reason="g7a_policy_binding_required")
            candidates = [ActionCandidateInput(**c) for c in event.payload["candidates"]]
            result = await self._logic.run_g7a(
                uow, trade_decision_id=event.trade_decision_id, candidates=candidates,
                policy_hash=policy_hash, version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "g7b":
            if policy_hash is None or version_manifest_id is None:
                return HandlerResult(False, reason="g7b_policy_binding_required")
            portfolio = PortfolioGateInput(**event.payload["portfolio"])
            result = await self._logic.run_g7b(
                uow, trade_decision_id=event.trade_decision_id, portfolio=portfolio,
                policy_hash=policy_hash, version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "terminal":
            action_set = ActionSetInput(**event.payload["action_set"])
            underwriting = None
            if event.payload.get("underwriting"):
                underwriting = UnderwritingInput(**event.payload["underwriting"])
            result = await self._logic.terminalize(
                uow, trade_decision_id=event.trade_decision_id, action_set=action_set,
                underwriting=underwriting,
                decided_at=datetime.fromisoformat(event.payload["decided_at"]),
            )
            return HandlerResult(result.ok, result, result.reason)
        raise ValueError(f"decision_event_unknown:{kind}")
