"""P3 evaluation runtime（WP-04 Checkpoint C）。

P3 独立 pool：不 import 也不复用 execution worker；不直接更新 strategy/permission/历史
事实。每个 Handler 只做一次 UoW；外部/长计算不持有 DB transaction。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.uow import UnitOfWork
from app.handlers.trading.evaluation import EvaluationEvent, EvaluationHandler
from app.handlers.trading.settlement import SettlementEvent, SettlementHandler
from app.logics.trading.evaluation import EvaluationLogic
from app.logics.trading.settlement import SettlementLogic
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository

logger = logging.getLogger(__name__)


class EvaluationRuntime:
    """settlement / evaluation 编排（每步一次 UoW；P3 独立 pool）。"""

    def __init__(self, sessions_factory: Any) -> None:
        self._sessions = sessions_factory
        self._settlement_logic = SettlementLogic(SettlementRepository())
        self._evaluation_logic = EvaluationLogic(
            EvaluationRepository(), SettlementRepository()
        )
        self._settlement_handler = SettlementHandler(self._settlement_logic)
        self._evaluation_handler = EvaluationHandler(self._evaluation_logic)

    async def handle_settlement_event(self, event: SettlementEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._settlement_handler.handle(uow, event)

    async def handle_evaluation_event(self, event: EvaluationEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._evaluation_handler.handle(uow, event)
