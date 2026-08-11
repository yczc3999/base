"""Cognition runtime（WP-02 Checkpoint C）。

编排 R1 ROUTED → G4 → research/verification → G5A/G5B → joint forecast → G6 → BLIND_COMMITTED。

- 每个 AI attempt 先 plan+commit（DB 事务），再调用 provider（网络调用不在事务内），
  最后写终态。retry/fallback/cache hit 都创建新 attempt。
- Blind 角色（planner_prior/joint_forecaster）固定 network=NONE/tools=[]；researcher/
  verifier 允许 Web/X/Search。
- provider 网络调用在 DB 事务外；所有事实（invocation/tool/validator/submission）append-only。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.ai_runtime.cache import cache_key
from app.ai_runtime.runner import AIRunner
from app.ai_runtime.validator import OutputValidator
from app.db.uow import UnitOfWork
from app.handlers.trading.cognition import CognitionEvent, CognitionHandler
from app.logics.trading.evidence import EvidenceLogic
from app.logics.trading.forecast import ForecastLogic
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.services.model_gateway.contracts import ModelRequest
from app.services.model_gateway.service import ModelGatewayService

logger = logging.getLogger(__name__)

# 角色 → 网络策略/工具（任务 §5.4）
BLIND_ROLES = {"planner_prior", "joint_forecaster"}
RESEARCH_ROLES = {"researcher", "verifier"}

PROMPT_VERSION_BY_ROLE = {
    "planner_prior": "planner_prior/v1",
    "researcher": "researcher/v1",
    "verifier": "verifier/v1",
    "joint_forecaster": "joint_forecaster/v1",
}


@dataclass(frozen=True)
class RoleBinding:
    model_role_binding_id: int
    provider: str
    route: str
    model: str
    network_policy: str
    allowed_tools: list[str]


class CognitionRuntime:
    """按 episode 推进 cognition 管道；依赖注入，不持有全局状态。"""

    def __init__(
        self,
        sessions_factory: Any,
        gateway: ModelGatewayService,
        validator: OutputValidator | None = None,
    ) -> None:
        self._sessions = sessions_factory
        self._runner = AIRunner(gateway, validator or OutputValidator())
        self._handler = CognitionHandler(
            EvidenceLogic(ForecastRepository(), WorkflowRepository()),
            ForecastLogic(ForecastRepository(), WorkflowRepository()),
        )

    async def run_ai_attempt(
        self,
        *,
        episode_id: int,
        stage: str,
        role: str,
        binding: RoleBinding,
        model_request: ModelRequest,
        blind_context: bool,
        plan_kwargs: dict[str, Any],
    ) -> int:
        """执行一次 AI attempt：plan → (cache hit? 记录) → run。返回 invocation id。"""
        async with self._sessions() as session:
            invocation_id = await self._runner.plan(session, **plan_kwargs)
            await session.commit()
        async with self._sessions() as session:
            outcome = await self._runner.run(
                session, invocation_id=invocation_id,
                model_role_binding_id=binding.model_role_binding_id,
                model_request=model_request, blind_context=blind_context,
            )
            logger.info(
                "ai_attempt role=%s episode=%s state=%s",
                role, episode_id, outcome.lifecycle_state,
            )
            return invocation_id

    async def run_cognition_chain(
        self,
        *,
        episode_id: int,
        version_manifest_id: int,
        evidence_coverage_policy_hash: str,
        prior_payload: dict,
        revision_payloads: list[dict],
        bundle_payload: dict,
        coverage_policy_payload: dict,
        covered_branches: list[str],
        submission_payload: dict,
        material_payload: dict,
        lease_payload: dict,
    ) -> dict[str, Any]:
        """推进一次完整 cognition 链（G4→revisions→G5A→G5B→G6）并返回每步结果。

        AI 调用（planner/researcher/verifier/forecaster）由调用方先经 ``run_ai_attempt``
        完成；这里把结构化结果以 handler event 落到同一 DB 事实链。
        """
        results: dict[str, Any] = {}
        async with self._sessions() as session:
            uow = UnitOfWork(session)
            # G4 prior
            g4 = await self._handler.handle(
                uow, CognitionEvent(kind="g4_prior", episode_id=episode_id,
                                    payload={"prior": prior_payload}),
                version_manifest_id=version_manifest_id,
            )
            results["g4"] = g4.reason
            # evidence revisions
            for revision in revision_payloads:
                await self._handler.handle(
                    uow, CognitionEvent(kind="evidence_revision", episode_id=episode_id,
                                        payload={"revision": revision}),
                    version_manifest_id=version_manifest_id,
                )
            # G5A bundle
            g5a = await self._handler.handle(
                uow, CognitionEvent(kind="g5a_bundle", episode_id=episode_id,
                                    payload={"bundle": bundle_payload}),
                version_manifest_id=version_manifest_id,
            )
            results["g5a"] = g5a.reason
            # G5B sufficiency
            g5b = await self._handler.handle(
                uow, CognitionEvent(kind="g5b_sufficiency", episode_id=episode_id,
                                    payload={"policy": coverage_policy_payload,
                                             "covered_branches": covered_branches}),
                version_manifest_id=version_manifest_id,
            )
            results["g5b"] = g5b.reason
            # G6 atomic commit
            g6 = await self._handler.handle(
                uow, CognitionEvent(kind="g6_commit", episode_id=episode_id,
                                    payload={"submission": submission_payload,
                                             "material": material_payload,
                                             "lease": lease_payload}),
                version_manifest_id=version_manifest_id,
                policy_hash=evidence_coverage_policy_hash,
            )
            results["g6"] = g6.reason
            results["ok"] = bool(g4.ok and g5a.ok and g5b.ok and g6.ok)
            return results
