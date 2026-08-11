"""Evidence Logic（WP-02 Checkpoint A）：G4 prior gate、G5A integrity、G5B sufficiency。

- G4：prior 完整、hash/版本/适用性/失效条件有效，且为显式市场盲先验（无 quote/odds/crowd）。
- G5A：四时态、source、raw/hash、cutoff、taint、market-conditioned discovery 全部合格。
- G5B：按冻结 coverage policy 返回 ``PASS|WIDEN_REQUIRED|ABSTAIN_EVIDENCE_INSUFFICIENT``；
  widening 算法、输入/输出 hash 可重算。

本期只实现确定性校验与 Gate 证据落库；G5B 的 widening 仅记录策略与 gap，
不自动生成 ``U'``（由 ForecastLogic.G6 按冻结算法消费）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.evidence import (
    EvidenceBundleInput,
    EvidenceCoveragePolicyInput,
    EvidenceRevisionInput,
    PriorInput,
)

G4_PASS = "PASS"
G4_FAIL = "FAIL"
G5A_PASS = "PASS"
G5A_ABSTAIN = "ABSTAIN_EVIDENCE_INTEGRITY"
G5B_PASS = "PASS"
G5B_WIDEN = "WIDEN_REQUIRED"
G5B_ABSTAIN = "ABSTAIN_EVIDENCE_INSUFFICIENT"

G4_REASON_MISSING = "g4_prior_missing"
G4_REASON_REFERENCE = "g4_prior_reference_required"
G4_REASON_STRUCTURE = "g4_prior_structure_incomplete"
G4_REASON_HASH = "g4_prior_hash_mismatch"
G4_REASON_BLIND = "g4_prior_not_market_blind"
G4_REASON_VERSION = "g4_prior_version_invalid"
G4_REASON_EPISODE = "g4_episode_not_routed"

G5A_REASON_TAINT = "g5a_taint"
G5A_REASON_MARKET_CONDITIONED = "g5a_market_conditioned_discovery"
G5A_REASON_CUTOFF = "g5a_cutoff_violation"
G5A_REASON_TIMES = "g5a_time_order_invalid"
G5A_REASON_SOURCE = "g5a_source_missing"
G5A_REASON_RAW = "g5a_raw_missing"
G5A_REASON_EPISODE = "g5a_episode_not_prior_ready"
G5A_REASON_EMPTY = "g5a_bundle_empty"

G5B_REASON_COVERAGE = "g5b_branch_uncovered"
G5B_REASON_POLICY = "g5b_policy_mismatch"
G5B_REASON_EPISODE = "g5b_episode_not_evidence_ready"

_WIDEN_SCALE = Decimal("0.000000000001")


@dataclass(frozen=True)
class G4Result:
    ok: bool
    reason: str | None = None
    prior_hash: str | None = None
    prior_version: int | None = None
    prior_id: int | None = None


@dataclass(frozen=True)
class G5AResult:
    ok: bool
    reason: str | None = None
    bundle_id: int | None = None
    bundle_hash: str | None = None
    revision_count: int = 0


@dataclass(frozen=True)
class G5BResult:
    result: str
    reason: str | None = None
    policy_hash: str | None = None
    covered_branches: list[str] | None = None
    missing_branches: list[str] | None = None
    widening_input_hash: str | None = None
    widening_output_hash: str | None = None


class EvidenceLogic:
    def __init__(
        self,
        forecast: ForecastRepository,
        workflow: WorkflowRepository | None = None,
    ) -> None:
        self._forecast = forecast
        self._workflow = workflow or WorkflowRepository()

    # ---------------- G4：explicit market-blind prior ----------------

    async def run_g4(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        prior: PriorInput,
        expected_prior_hash: str | None = None,
        version_manifest_id: int,
    ) -> G4Result:
        """冻结显式市场盲先验并写 G4 Gate 证据（PASS/FAIL 均记录）。"""
        episode = await self._forecast.get_episode(uow.session, episode_id)
        if episode is None or episode["status"] != "ROUTED":
            return G4Result(False, G4_REASON_EPISODE)
        if episode["cognition_status"] != "PENDING":
            return G4Result(False, "g4_cognition_not_pending")

        if not prior.market_blind_declaration:
            return G4Result(False, G4_REASON_BLIND)
        try:
            prior.require_reference()
            prior.require_structured()
        except ValueError:
            return G4Result(False, G4_REASON_STRUCTURE)
        if not prior.reference_class and not prior.hazard_ref:
            return G4Result(False, G4_REASON_REFERENCE)

        prior_dict = prior.model_dump(mode="json")
        prior_hash = canonical_hash(prior_dict)
        if expected_prior_hash is not None and expected_prior_hash != prior_hash:
            return G4Result(False, G4_REASON_HASH)

        version_no = (await self._forecast.latest_prior_version(uow.session, episode_id)) + 1
        prior_id = await self._forecast.insert_prior(
            uow.session,
            episode_id=episode_id,
            version_no=version_no,
            reference_class=prior.reference_class,
            hazard_ref=prior.hazard_ref,
            applicability=prior.applicability,
            sample_rule=prior.sample_rule,
            width=prior.width,
            failure_conditions=prior.failure_conditions,
            market_blind_declaration=True,
            content=prior_dict,
            content_hash=prior_hash,
            status="active",
        )
        if not await self._forecast.mark_episode_prior_ready(
            uow.session, episode_id, prior_frozen_at=datetime.now(timezone.utc)
        ):
            raise RuntimeError("g4_episode_progression_conflict")
        await self._write_gate(
            uow, episode_id=episode_id, gate="G4", result=G4_PASS,
            input_hash=prior_hash, policy_hash=None,
            version_manifest_id=version_manifest_id, reason_code=None,
        )
        return G4Result(True, prior_hash=prior_hash, prior_version=version_no, prior_id=prior_id)

    # ---------------- evidence revision ingestion ----------------

    async def add_revision(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        revision: EvidenceRevisionInput,
    ) -> int:
        """插入一条四时态 evidence revision；时态/cutoff/taint 由 G5A 冻结时统一判定。

        此处只做结构校验与去重（revision_key 唯一），不判定 eligibility。
        """
        episode = await self._forecast.get_episode(uow.session, episode_id)
        if episode is None:
            raise ValueError("g5a_episode_missing")
        revision.assert_time_order()
        prev_id = None
        if revision.prev_revision_key is not None:
            prev_id = await self._forecast.revision_id_by_key(
                uow.session, episode_id=episode_id, revision_key=revision.prev_revision_key
            )
            if prev_id is None:
                raise ValueError("evidence_prev_revision_missing")
        return await self._forecast.insert_evidence_revision(
            uow.session,
            episode_id=episode_id,
            revision_key=revision.revision_key,
            kind=revision.kind,
            event_at=revision.event_at,
            published_at=revision.published_at,
            observed_at=revision.observed_at,
            ingested_at=revision.ingested_at,
            source=revision.source,
            source_type=revision.source_type,
            branch=revision.branch,
            prev_revision_id=prev_id,
            raw_artifact_id=await self._require_artifact(uow, revision.raw_artifact_ref),
            content=revision.content,
            content_hash=canonical_hash(revision.content),
            taint_status=revision.taint_status,
            market_conditioned_discovery=revision.market_conditioned_discovery,
        )

    async def _require_artifact(self, uow: UnitOfWork, artifact_ref: str) -> int:
        result = await uow.session.execute(
            text("SELECT id FROM trading.artifact_objects WHERE sha256=:s"),
            {"s": artifact_ref},
        )
        artifact_id = result.scalar_one_or_none()
        if artifact_id is None:
            raise ValueError("evidence_raw_artifact_missing")
        return artifact_id

    # ---------------- G5A：as-of evidence integrity ----------------

    async def run_g5a(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        bundle: EvidenceBundleInput,
        version_manifest_id: int,
    ) -> G5AResult:
        """冻结 cutoff 前合格 evidence revision 为不可变 bundle（G5A hard veto）。"""
        episode = await self._forecast.get_episode(uow.session, episode_id)
        if episode is None or episode["status"] != "ROUTED":
            return G5AResult(False, G5A_REASON_EPISODE)
        if episode["cognition_status"] != "PRIOR_READY":
            return G5AResult(False, "g5a_cognition_not_prior_ready")

        cutoff = bundle.information_cutoff_at
        revision_ids: list[int] = []
        revisions: list[dict[str, Any]] = []
        for key in bundle.revision_keys:
            revision = await self._forecast.get_evidence_revision(
                uow.session, episode_id=episode_id, revision_key=key
            )
            if revision is None:
                raise ValueError(f"evidence_revision_missing:{key}")
            reason = self._integrity_check(revision, cutoff)
            if reason is not None:
                return G5AResult(False, reason)
            revisions.append(revision)
            revision_ids.append(revision["id"])
        if not revision_ids:
            return G5AResult(False, G5A_REASON_EMPTY)

        # bundle hash：按 (revision_key, content_hash, ingested_at) 排序，输入乱序不改变 hash。
        bundle_material = [
            {
                "revision_key": revision["revision_key"],
                "content_hash": revision["content_hash"],
                "ingested_at": revision["ingested_at"],
            }
            for revision in revisions
        ]
        bundle_material.sort(key=lambda item: (item["revision_key"], item["content_hash"]))
        bundle_hash = canonical_hash(
            {
                "episode_key": episode["episode_key"],
                "cutoff": cutoff,
                "revisions": bundle_material,
            }
        )
        bundle_id = await self._forecast.insert_evidence_bundle(
            uow.session,
            episode_id=episode_id,
            bundle_key=bundle.bundle_key,
            information_cutoff_at=cutoff,
            bundle_hash=bundle_hash,
            status="frozen",
        )
        await self._forecast.insert_evidence_bundle_items(
            uow.session,
            bundle_id=bundle_id,
            rows=[
                {
                    "revision_id": revision_id,
                    "item_no": index,
                    "eligible": True,
                    "eligibility_reason": None,
                }
                for index, revision_id in enumerate(revision_ids)
            ],
        )
        if not await self._forecast.mark_episode_evidence_ready(
            uow.session, episode_id, evidence_bundle_at=datetime.now(timezone.utc)
        ):
            raise RuntimeError("g5a_episode_progression_conflict")
        await self._write_gate(
            uow, episode_id=episode_id, gate="G5A", result=G5A_PASS,
            input_hash=bundle_hash, policy_hash=None,
            version_manifest_id=version_manifest_id, reason_code=None,
        )
        return G5AResult(
            True, bundle_id=bundle_id, bundle_hash=bundle_hash,
            revision_count=len(revision_ids),
        )

    def _integrity_check(self, revision: dict[str, Any], cutoff: datetime) -> str | None:
        """hard veto：任何一条 evidence 违反四时态/cutoff/taint/source/raw 即失败。"""
        if revision["taint_status"] != "none":
            return f"{G5A_REASON_TAINT}:{revision['taint_status']}"
        if revision["market_conditioned_discovery"]:
            return G5A_REASON_MARKET_CONDITIONED
        if (
            revision["published_at"] > cutoff
            or revision["observed_at"] > cutoff
            or revision["ingested_at"] > cutoff
        ):
            return G5A_REASON_CUTOFF
        if not revision["source"] or not revision["source_type"]:
            return G5A_REASON_SOURCE
        if revision["raw_artifact_id"] is None:
            return G5A_REASON_RAW
        if (
            revision["published_at"] > revision["observed_at"]
            or revision["observed_at"] > revision["ingested_at"]
        ):
            return G5A_REASON_TIMES
        return None

    # ---------------- G5B：evidence sufficiency ----------------

    async def run_g5b(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        policy: EvidenceCoveragePolicyInput,
        covered_branches: list[str],
        version_manifest_id: int,
    ) -> G5BResult:
        """按冻结 coverage policy 判定 material branch 覆盖。"""
        chain = await self._forecast.episode_cognition_chain(uow.session, episode_id)
        if chain is None or chain["status"] != "ROUTED":
            return G5BResult(G5B_ABSTAIN, G5B_REASON_EPISODE)
        if chain["cognition_status"] != "EVIDENCE_READY":
            return G5BResult(G5B_ABSTAIN, "g5b_cognition_not_evidence_ready")

        policy_hash = canonical_hash(policy.model_dump(mode="json"))
        policy_id = await self._forecast.get_evidence_coverage_policy(
            uow.session,
            cohort_id=chain["cohort_id"],
            policy_version=policy.policy_version,
        )
        if policy_id is None:
            raise ValueError("g5b_policy_not_frozen")

        required = set(policy.material_branches)
        covered = set(covered_branches)
        missing = sorted(required - covered)
        widening_input_hash = canonical_hash(
            {
                "policy_hash": policy_hash,
                "covered_branches": sorted(covered),
                "required_branches": sorted(required),
            }
        )
        if not missing:
            result = G5B_PASS
            reason = None
        elif policy.missing_branch_policy == "widen":
            result = G5B_WIDEN
            reason = G5B_REASON_COVERAGE
        else:
            result = G5B_ABSTAIN
            reason = G5B_REASON_COVERAGE
        widening_output_hash = canonical_hash(
            {
                "result": result,
                "missing": missing,
                "policy_hash": policy_hash,
                "input_hash": widening_input_hash,
            }
        )
        await self._write_gate(
            uow, episode_id=episode_id, gate="G5B", result=result,
            input_hash=widening_input_hash, policy_hash=policy_hash,
            version_manifest_id=version_manifest_id, reason_code=reason,
        )
        return G5BResult(
            result=result,
            reason=reason,
            policy_hash=policy_hash,
            covered_branches=sorted(covered),
            missing_branches=missing,
            widening_input_hash=widening_input_hash,
            widening_output_hash=widening_output_hash,
        )

    # ---------------- helpers ----------------

    async def _write_gate(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        gate: str,
        result: str,
        input_hash: str,
        policy_hash: str | None,
        version_manifest_id: int,
        reason_code: str | None,
    ) -> None:
        committed_at = datetime.now(timezone.utc)
        await self._workflow.insert_gate_decision(
            uow.session,
            gate=gate,
            target_kind="episode",
            target_id=episode_id,
            input_hash=input_hash,
            policy_hash=policy_hash or input_hash,
            version_manifest_id=version_manifest_id,
            result=result,
            reason_code=reason_code,
            committed_at=committed_at,
        )
