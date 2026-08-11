"""Cognition handler（WP-02 Checkpoint C）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
不实现 Gate 算法；G4/G5A/G5B/G6 与 AI runner 的编排在 runtime。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.uow import UnitOfWork
from app.logics.trading.evidence import EvidenceLogic, G4Result, G5AResult, G5BResult
from app.logics.trading.forecast import ForecastLogic, G6Result, InputManifestMaterial
from app.schemas.trading.evidence import (
    EvidenceBundleInput,
    EvidenceCoveragePolicyInput,
    EvidenceRevisionInput,
    PriorInput,
)
from app.schemas.trading.forecast import (
    ForecastLeaseInput,
    ForecastSubmissionInput,
)


@dataclass(frozen=True)
class CognitionEvent:
    kind: str  # g4_prior / evidence_revision / g5a_bundle / g5b_sufficiency / g6_commit
    episode_id: int
    payload: dict


@dataclass(frozen=True)
class HandlerResult:
    ok: bool
    result: Any | None = None
    reason: str | None = None


class CognitionHandler:
    """解析 cognition event 并调用对应 Logic；一个 event 一次 UoW。"""

    def __init__(
        self,
        evidence: EvidenceLogic,
        forecast: ForecastLogic,
    ) -> None:
        self._evidence = evidence
        self._forecast = forecast

    async def handle(
        self,
        uow: UnitOfWork,
        event: CognitionEvent,
        *,
        version_manifest_id: int,
        policy_hash: str | None = None,
    ) -> HandlerResult:
        kind = event.kind
        if kind == "g4_prior":
            prior = PriorInput(**event.payload["prior"])
            result = await self._evidence.run_g4(
                uow, episode_id=event.episode_id, prior=prior,
                version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "evidence_revision":
            revision = EvidenceRevisionInput(**event.payload["revision"])
            revision_id = await self._evidence.add_revision(
                uow, episode_id=event.episode_id, revision=revision
            )
            return HandlerResult(True, {"revision_id": revision_id})
        if kind == "g5a_bundle":
            bundle = EvidenceBundleInput(**event.payload["bundle"])
            result = await self._evidence.run_g5a(
                uow, episode_id=event.episode_id, bundle=bundle,
                version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "g5b_sufficiency":
            policy = EvidenceCoveragePolicyInput(**event.payload["policy"])
            result = await self._evidence.run_g5b(
                uow, episode_id=event.episode_id, policy=policy,
                covered_branches=event.payload["covered_branches"],
                version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.result == "PASS", result, result.reason)
        if kind == "g6_commit":
            submission = ForecastSubmissionInput(**event.payload["submission"])
            material = InputManifestMaterial(**event.payload["material"])
            lease = ForecastLeaseInput(**event.payload["lease"])
            if policy_hash is None:
                raise ValueError("g6_policy_hash_required")
            result = await self._forecast.run_g6(
                uow, episode_id=event.episode_id, submission=submission,
                material=material, lease=lease,
                version_manifest_id=version_manifest_id, policy_hash=policy_hash,
            )
            return HandlerResult(result.ok, result, result.reason)
        raise ValueError(f"cognition_event_unknown:{kind}")
