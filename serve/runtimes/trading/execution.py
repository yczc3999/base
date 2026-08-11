"""Shadow execution runtime（WP-03 Checkpoint D）。

只注册 DB-backed decision/shadow handlers；不 import 私有 CLOB SDK、vault、wallet、签名、
Data API 或真实下单 Driver。每个 Handler 只做一次 UoW；外部/长计算不持有 DB transaction。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.uow import UnitOfWork
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler
from app.logics.trading.decision import DecisionLogic
from app.logics.trading.execution import ShadowExecutionLogic
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.outbox.repository import OutboxRepository

logger = logging.getLogger(__name__)


class ShadowExecutionRuntime:
    """编排 decision → G7A/G7B → terminal → intent → shadow fill（每步一次 UoW）。"""

    def __init__(self, sessions_factory: Any) -> None:
        self._sessions = sessions_factory
        self._decision_logic = DecisionLogic(DecisionRepository(), WorkflowRepository())
        self._decision_handler = DecisionHandler(self._decision_logic)
        self._execution_logic = ShadowExecutionLogic(
            ExecutionRepository(), LedgerRepository(), OutboxRepository()
        )
        self._execution_handler = ExecutionHandler(self._execution_logic)

    async def handle_decision_event(
        self,
        event: DecisionEvent,
        *,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._decision_handler.handle(
                uow, event, policy_hash=policy_hash,
                version_manifest_id=version_manifest_id,
            )

    async def handle_execution_event(
        self,
        event: ExecutionEvent,
        *,
        portfolio_namespace: str | None = None,
        cash_asset_key: str | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._execution_handler.handle(
                uow, event, portfolio_namespace=portfolio_namespace,
                cash_asset_key=cash_asset_key,
            )
