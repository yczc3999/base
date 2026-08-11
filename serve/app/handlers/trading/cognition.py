"""Cognition handler（WP-02 Checkpoint C）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
不实现 Gate 算法；G4/G5A/G5B/G6 与 AI runner 的编排在 runtime。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.ai_runtime.redaction import detect_taint
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
from app.repositories.trading.market_stream import MarketStreamRepository
from app.services.artifact_store import ArtifactStore


@dataclass(frozen=True)
class CognitionEvent:
    kind: str  # g4_prior / evidence_revision / g5a_bundle / g5b_sufficiency / g6_commit
    episode_id: int
    payload: dict
    accepted_invocation_id: int | None = None


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
        artifacts: ArtifactStore,
    ) -> None:
        self._evidence = evidence
        self._forecast = forecast
        self._artifacts = artifacts
        self._artifact_catalog = MarketStreamRepository()

    async def _accepted_output(
        self,
        uow: UnitOfWork,
        event: CognitionEvent,
        *,
        roles: set[str],
        stages: set[str],
    ) -> dict:
        """读取并验证与 episode/role/stage 绑定的 ACCEPTED normalized Artifact。"""
        if event.accepted_invocation_id is None:
            raise ValueError("accepted_invocation_required")
        row = (
            await uow.session.execute(
                text(
                    "SELECT id, role, stage, lifecycle_state, normalized_output_artifact_ref, "
                    "accepted_output_binding FROM trading.ai_invocations "
                    "WHERE id=:id AND episode_id=:episode"
                ),
                {"id": event.accepted_invocation_id, "episode": event.episode_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise ValueError("accepted_invocation_missing")
        output_ref = row["normalized_output_artifact_ref"]
        if (
            row["lifecycle_state"] != "ACCEPTED"
            or row["role"] not in roles
            or row["stage"] not in stages
            or not output_ref
            or row["accepted_output_binding"] != output_ref
        ):
            raise ValueError("accepted_invocation_binding_invalid")
        artifact_id = (
            await uow.session.execute(
                text(
                    "SELECT id FROM trading.artifact_objects "
                    "WHERE sha256=:sha AND compression='none' ORDER BY id LIMIT 1"
                ),
                {"sha": output_ref},
            )
        ).scalar_one_or_none()
        if artifact_id is None:
            raise ValueError("accepted_output_artifact_missing")
        ref = await self._artifact_catalog.load_artifact_ref(uow.session, artifact_id)
        raw = await asyncio.to_thread(self._artifacts.get_bytes, ref)
        try:
            output = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("accepted_output_artifact_invalid") from exc
        if not isinstance(output, dict):
            raise ValueError("accepted_output_not_object")
        hits = detect_taint(output)
        if hits:
            raise ValueError(f"accepted_output_taint:{hits[0]}")
        return output

    @staticmethod
    def _contains_node(root: Any, expected: Any) -> bool:
        if root == expected:
            return True
        if isinstance(root, dict):
            return any(
                CognitionHandler._contains_node(child, expected)
                for child in root.values()
            )
        if isinstance(root, list):
            return any(
                CognitionHandler._contains_node(child, expected) for child in root
            )
        return False

    @staticmethod
    def _assert_prior_bound(output: dict, prior: PriorInput) -> None:
        candidate = prior.model_dump(mode="json")
        keys = {
            "reference_class",
            "hazard_ref",
            "applicability",
            "sample_rule",
            "width",
            "failure_conditions",
            "market_blind_declaration",
        }
        for key in keys:
            if key in output or candidate.get(key) is not None:
                if output.get(key) != candidate.get(key):
                    raise ValueError(f"prior_not_bound_to_accepted_output:{key}")

    @staticmethod
    def _assert_submission_bound(
        output: dict, submission: ForecastSubmissionInput
    ) -> None:
        if output.get("abstain") is True:
            raise ValueError("forecast_output_abstained")
        if output.get("Q") != submission.Q.values:
            raise ValueError("forecast_q_not_bound_to_accepted_output")
        if output.get("U") != [member.values for member in submission.U]:
            raise ValueError("forecast_u_not_bound_to_accepted_output")

    async def handle(
        self,
        uow: UnitOfWork,
        event: CognitionEvent,
        *,
        version_manifest_id: int,
        policy_hash: str | None = None,
    ) -> HandlerResult:
        kind = event.kind
        hits = detect_taint(event.payload)
        if hits:
            raise ValueError(f"blind_cognition_input_taint:{hits[0]}")
        if kind == "g4_prior":
            prior = PriorInput(**event.payload["prior"])
            output = await self._accepted_output(
                uow, event, roles={"planner_prior"}, stages={"g4"}
            )
            self._assert_prior_bound(output, prior)
            result = await self._evidence.run_g4(
                uow, episode_id=event.episode_id, prior=prior,
                version_manifest_id=version_manifest_id,
            )
            return HandlerResult(result.ok, result, result.reason)
        if kind == "evidence_revision":
            revision = EvidenceRevisionInput(**event.payload["revision"])
            output = await self._accepted_output(
                uow,
                event,
                roles={"researcher", "verifier"},
                stages={"g5a", "research", "verify"},
            )
            if not revision.content or not self._contains_node(output, revision.content):
                raise ValueError("evidence_not_bound_to_accepted_output")
            has_tool_artifact = (
                await uow.session.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM trading.ai_tool_calls "
                        "WHERE invocation_id=:id AND result_artifact_ref=:ref)"
                    ),
                    {
                        "id": event.accepted_invocation_id,
                        "ref": revision.raw_artifact_ref,
                    },
                )
            ).scalar_one()
            if not has_tool_artifact:
                raise ValueError("evidence_tool_artifact_binding_missing")
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
            output = await self._accepted_output(
                uow, event, roles={"joint_forecaster"}, stages={"g6"}
            )
            self._assert_submission_bound(output, submission)
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
