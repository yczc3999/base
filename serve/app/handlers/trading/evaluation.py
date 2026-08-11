"""Evaluation handler（WP-04 Checkpoint C）。

Handler 只解析一个 typed event、调用一个 Logic/UoW、返回 completion。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.evaluation import EvaluationLogic
from app.schemas.trading.evaluation import (
    MetricRunInput,
    PromotionDecisionInput,
    ScoreObservationInput,
)


@dataclass(frozen=True)
class EvaluationEvent:
    kind: str  # score_observation | run_metric | promote | champion_challenger_pair
    payload: dict | None = None


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class EvaluationHandler:
    """解析 evaluation event 并调用 EvaluationLogic；一个 event 一次 UoW。"""

    def __init__(self, logic: EvaluationLogic) -> None:
        self._logic = logic

    async def handle(
        self, uow: UnitOfWork, event: EvaluationEvent
    ) -> HandlerResult:
        kind = event.kind
        if event.payload is None:
            return HandlerResult(False, reason="evaluation_event_incomplete")
        if kind == "score_observation":
            input_ = ScoreObservationInput(**event.payload)
            result = await self._logic.score_observation(uow, input_=input_)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "run_metric":
            input_ = MetricRunInput(**event.payload)
            result = await self._logic.run_metric(uow, input_=input_)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "promote":
            input_ = PromotionDecisionInput(**event.payload)
            result = await self._logic.promote(uow, input_=input_)
            return HandlerResult(result.ok, result, result.reason)
        if kind == "champion_challenger_pair":
            result = await self._logic.champion_challenger_pair(
                uow, experiment_key=str(event.payload["experiment_key"])
            )
            return HandlerResult(result.ok, result, result.reason)
        raise ValueError(f"evaluation_event_unknown:{kind}")
