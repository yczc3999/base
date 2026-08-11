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
from app.services.artifact_store import ArtifactStore

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
        artifacts: ArtifactStore,
        validator: OutputValidator | None = None,
    ) -> None:
        self._sessions = sessions_factory
        self._runner = AIRunner(
            gateway, validator or OutputValidator(), artifacts=artifacts
        )
        self._handler = CognitionHandler(
            EvidenceLogic(ForecastRepository(), WorkflowRepository()),
            ForecastLogic(ForecastRepository(), WorkflowRepository()),
            artifacts,
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
        plan_kwargs = {
            **plan_kwargs,
            "effort": model_request.effort,
        }
        code_hash = plan_kwargs.get("git_sha")
        if isinstance(code_hash, str):
            plan_kwargs["cache_key_hash"] = self._runner.request_cache_key(
                model_request, code_hash
            )
            async with self._sessions() as session:
                hit = await self._runner.check_cache(
                    session,
                    model_request=model_request,
                    code_hash=code_hash,
                )
                if hit.hit and hit.source_invocation_id is not None:
                    invocation_id = await self._runner.record_cache_hit(
                        session,
                        plan_kwargs=plan_kwargs,
                        source_invocation_id=hit.source_invocation_id,
                        occurred_at=plan_kwargs.get("occurred_at")
                        or datetime.now(timezone.utc),
                        model_request=model_request,
                        code_hash=code_hash,
                    )
                    await session.commit()
                    logger.info(
                        "ai_attempt role=%s episode=%s state=ACCEPTED cache_hit=true",
                        role,
                        episode_id,
                    )
                    return invocation_id
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
        prior_invocation_id: int,
        revision_invocation_ids: list[int],
        forecast_invocation_id: int,
    ) -> dict[str, Any]:
        """推进一次完整 cognition 链（G4→revisions→G5A→G5B→G6）并返回每步结果。

        AI 调用（planner/researcher/verifier/forecaster）由调用方先经 ``run_ai_attempt``
        完成；这里把结构化结果以 handler event 落到同一 DB 事实链。
        """
        if len(revision_payloads) != len(revision_invocation_ids):
            raise ValueError("revision_invocation_cardinality_mismatch")
        results: dict[str, Any] = {}
        async with UnitOfWork(self._sessions) as uow:
            # G4 prior
            g4 = await self._handler.handle(
                uow, CognitionEvent(kind="g4_prior", episode_id=episode_id,
                                    payload={"prior": prior_payload},
                                    accepted_invocation_id=prior_invocation_id),
                version_manifest_id=version_manifest_id,
            )
            results["g4"] = g4.reason
            if not g4.ok:
                results["ok"] = False
                return results
            # evidence revisions
            for revision, invocation_id in zip(
                revision_payloads, revision_invocation_ids, strict=True
            ):
                await self._handler.handle(
                    uow, CognitionEvent(kind="evidence_revision", episode_id=episode_id,
                                        payload={"revision": revision},
                                        accepted_invocation_id=invocation_id),
                    version_manifest_id=version_manifest_id,
                )
            # G5A bundle
            g5a = await self._handler.handle(
                uow, CognitionEvent(kind="g5a_bundle", episode_id=episode_id,
                                    payload={"bundle": bundle_payload}),
                version_manifest_id=version_manifest_id,
            )
            results["g5a"] = g5a.reason
            if not g5a.ok:
                results["ok"] = False
                return results
            # G5B sufficiency
            g5b = await self._handler.handle(
                uow, CognitionEvent(kind="g5b_sufficiency", episode_id=episode_id,
                                    payload={"policy": coverage_policy_payload,
                                             "covered_branches": covered_branches}),
                version_manifest_id=version_manifest_id,
            )
            results["g5b"] = g5b.reason
            if not g5b.ok:
                results["ok"] = False
                return results
            # G6 atomic commit
            g6 = await self._handler.handle(
                uow, CognitionEvent(kind="g6_commit", episode_id=episode_id,
                                    payload={"submission": submission_payload,
                                             "material": material_payload,
                                             "lease": lease_payload},
                                    accepted_invocation_id=forecast_invocation_id),
                version_manifest_id=version_manifest_id,
                policy_hash=evidence_coverage_policy_hash,
            )
            results["g6"] = g6.reason
            results["ok"] = bool(g4.ok and g5a.ok and g5b.ok and g6.ok)
            return results
