"""Replay runtime（WP-04 Checkpoint C）。

科学回放 / 消融 / 错误评审采样；每步一次 UoW。只写新 artifact，绝不写回原事实。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.replay import ReplayLogic
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.evaluation import EvaluationRepository

logger = logging.getLogger(__name__)


class ReplayRuntime:
    """科学回放编排（每步一次 UoW）。"""

    def __init__(self, sessions_factory: Any) -> None:
        self._sessions = sessions_factory
        self._logic = ReplayLogic(AuditRepository(), EvaluationRepository())

    async def replay_original(
        self, *, run_key: str, manifest_hash: str, seed: int
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._logic.replay_original(
                uow, run_key=run_key, manifest_hash=manifest_hash, seed=seed
            )

    async def replay_new_code(
        self,
        *,
        run_key: str,
        manifest_hash: str,
        code_hash: str,
        seed: int,
        variant: str | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._logic.replay_new_code(
                uow, run_key=run_key, manifest_hash=manifest_hash,
                code_hash=code_hash, seed=seed, variant=variant,
            )

    async def ablation(
        self,
        *,
        ablation_key: str,
        metric_run_id: int,
        bundle_hash: str,
        fields: dict,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._logic.ablation(
                uow, ablation_key=ablation_key, metric_run_id=metric_run_id,
                bundle_hash=bundle_hash, fields=fields,
            )

    async def error_review_selection(
        self,
        *,
        metric_run_id: int,
        seed: int,
        top_n: int = 3,
        explicit_taxonomies: dict[str, str] | None = None,
    ) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._logic.error_review_selection(
                uow, metric_run_id=metric_run_id, seed=seed, top_n=top_n,
                explicit_taxonomies=explicit_taxonomies,
            )
