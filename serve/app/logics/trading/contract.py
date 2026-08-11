"""Contract Logic（WP-01C Checkpoint A）。

G1 合约有效性 Gate：``K_c/R_c/g_{c,t}`` 不完整、基数不唯一、无法唯一解释兑付、规则来源
不完整、outcome 映射冲突或关键 clarification 缺失 → G1 fail-closed（任务 §2.2/§5.1）。

本期不让模型猜自然语言：Logic 接收 typed candidate，确定性校验并持久化。PASS spec 恰引用
一个 snapshot；snapshot 只作 provenance，不被下游当 spec FK。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.gates import assert_frozen_gate_binding
from app.domain.trading.payout import validate_payout_ir
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.semantics import ContractSpecInput

# G1 固定 reason codes
G1_OK = None
G1_MISSING_RULES = "g1_missing_rules"
G1_AMBIGUOUS_RESOLUTION = "g1_ambiguous_resolution"
G1_TOKEN_MAPPING_CONFLICT = "g1_token_mapping_conflict"
G1_CLARIFICATION_MISSING = "g1_clarification_missing"
G1_PAYOUT_INCOMPLETE = "g1_payout_incomplete"
G1_PAYOUT_CARDINALITY = "g1_payout_cardinality"
G1_UNKNOWN_RESOLUTION = "g1_unknown_resolution"

# payout 算法版本（固定；可重算）
PAYOUT_ALGORITHM_VERSION = "lookup-truth-table/v1"


@dataclass(frozen=True)
class G1Result:
    ok: bool
    spec_id: int | None = None
    reason: str | None = None
    content_hash: str | None = None

    @property
    def reason_code(self) -> str | None:
        return None if self.ok else self.reason


def spec_canonical_content(
    *,
    contract_key: str,
    resolution_states: list[str],
    token_ids: dict,
    payout_irs: dict[str, dict],
    snapshot_hash: str | None = None,
    compiler_version: str | None = None,
    schema_version: int | None = None,
) -> dict[str, Any]:
    """spec 的 canonical content（用于 content_hash 与 diff 判定）。"""
    return {
        "contract_key": contract_key,
        "resolution_states": sorted(resolution_states),
        "token_ids": {str(k): v for k, v in sorted(token_ids.items())},
        "payouts": {k: {kk: str(vv) for kk, vv in sorted(v.items())} for k, v in sorted(payout_irs.items())},
        "algorithm": PAYOUT_ALGORITHM_VERSION,
        "snapshot_hash": snapshot_hash,
        "compiler_version": compiler_version,
        "schema_version": schema_version,
    }


def _g1_validate(candidate: ContractSpecInput) -> str | None:
    """确定性 G1 校验：任一不满足 → 固定 reason（任务 §5.1/§2.2）。"""
    if not candidate.rules:
        return G1_MISSING_RULES
    if not candidate.resolution_source:
        return G1_AMBIGUOUS_RESOLUTION
    if candidate.clarification_required and not candidate.clarification:
        return G1_CLARIFICATION_MISSING
    # outcome/token mapping 固定为两个 token，R_c 可以额外包含 VOID/PARTIAL 等裁决态。
    if len(candidate.resolution_states) < 2:
        return G1_UNKNOWN_RESOLUTION
    if len(candidate.payouts) != 2:
        return G1_PAYOUT_CARDINALITY
    by_index = {payout.outcome_index: payout for payout in candidate.payouts}
    if set(by_index) != {0, 1}:
        return G1_TOKEN_MAPPING_CONFLICT
    if len({p.pm_token_id for p in candidate.payouts}) != 2:
        return G1_TOKEN_MAPPING_CONFLICT
    if len({p.token_version_id for p in candidate.payouts}) != 2:
        return G1_TOKEN_MAPPING_CONFLICT
    if by_index[0].token_version_id != candidate.yes_token_version_id:
        return G1_TOKEN_MAPPING_CONFLICT
    if by_index[1].token_version_id != candidate.no_token_version_id:
        return G1_TOKEN_MAPPING_CONFLICT
    # 每个 payout IR 的 key 集必须 == R_c
    for payout in candidate.payouts:
        try:
            validate_payout_ir(payout.function_ir, resolution_states=candidate.resolution_states)
        except ValueError as exc:
            if "payout_key_mismatch" in str(exc):
                return G1_PAYOUT_INCOMPLETE
            return G1_AMBIGUOUS_RESOLUTION
    return G1_OK


class ContractLogic:
    """G1 编排：snapshot + spec + payouts 单 UoW 原子写；绝不 commit。"""

    def __init__(
        self,
        semantics: SemanticsRepository,
        workflow: WorkflowRepository | None = None,
    ) -> None:
        self._sem = semantics
        self._workflow = workflow or WorkflowRepository()

    async def run_g1(
        self,
        uow: UnitOfWork,
        *,
        candidate: ContractSpecInput,
        cutoff_at: datetime | None,
        timezone_name: str | None,
        raw_outcome_mapping: dict | None,
        opportunity_id: int | None = None,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> G1Result:
        """G1：校验 → 存 snapshot → 建 spec（PASS/FAIL）→ 写 payouts（仅 PASS）。"""
        reason = _g1_validate(candidate)
        session = uow.session

        if opportunity_id is None or policy_hash is None or version_manifest_id is None:
            raise ValueError("g1_gate_binding_required")
        lineage = await self._workflow.get_opportunity_lineage(session, opportunity_id)
        if lineage is None or lineage["parent_id"] is None or lineage["status"] != "OPEN":
            raise ValueError("g1_opportunity_not_open_child")
        assert_frozen_gate_binding(
            lineage,
            policy_type="eligibility",
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
        )
        opportunity_markets = await self._workflow.opportunity_market_ids(session, opportunity_id)

        token_context = await self._sem.token_mapping_context(
            session,
            market_version_id=candidate.market_version_id,
            yes_token_version_id=candidate.yes_token_version_id,
            no_token_version_id=candidate.no_token_version_id,
        )
        if token_context is None:
            reason = reason or G1_TOKEN_MAPPING_CONFLICT
        else:
            by_index = {payout.outcome_index: payout for payout in candidate.payouts}
            exact = (
                token_context["market_id"] in opportunity_markets
                and token_context["yes_market_id"] == token_context["market_id"]
                and token_context["no_market_id"] == token_context["market_id"]
                and token_context["yes_outcome_index"] == 0
                and token_context["no_outcome_index"] == 1
                and 0 in by_index
                and 1 in by_index
                and by_index[0].pm_token_id == token_context["yes_token_id"]
                and by_index[1].pm_token_id == token_context["no_token_id"]
            )
            if not exact:
                reason = reason or G1_TOKEN_MAPPING_CONFLICT

        artifact_hash = await self._sem.artifact_hash(
            session, candidate.artifact_object_id
        )
        if artifact_hash is None:
            raise ValueError("g1_artifact_binding_missing")

        await self._sem.lock_contract_key(session, candidate.contract_key)

        # snapshot 始终保存（provenance；即使 G1 FAIL 也保留输入）
        snapshot_content = {
            "market_key": token_context["market_key"] if token_context else None,
            "market_version_no": token_context["market_version_no"] if token_context else None,
            "market_version_hash": token_context["market_version_hash"] if token_context else None,
            "yes_token_key": token_context["yes_token_key"] if token_context else None,
            "yes_token_version_no": token_context["yes_token_version_no"] if token_context else None,
            "no_token_key": token_context["no_token_key"] if token_context else None,
            "no_token_version_no": token_context["no_token_version_no"] if token_context else None,
            "question": candidate.question,
            "rules": candidate.rules,
            "clarification": candidate.clarification,
            "resolution_source": candidate.resolution_source,
            "artifact_hash": artifact_hash,
            "cutoff_at": cutoff_at,
            "timezone_name": timezone_name,
            "raw_outcome_mapping": raw_outcome_mapping,
        }
        snapshot_hash = canonical_hash(snapshot_content)
        snapshot = await self._sem.get_snapshot_by_hash(session, snapshot_hash)
        if snapshot is None:
            snapshot_id = await self._sem.insert_snapshot(
                session,
                market_version_id=candidate.market_version_id,
                yes_token_version_id=candidate.yes_token_version_id,
                no_token_version_id=candidate.no_token_version_id,
                artifact_object_id=candidate.artifact_object_id,
                question=candidate.question,
                rules=candidate.rules,
                clarification=candidate.clarification,
                resolution_source=candidate.resolution_source,
                cutoff_at=cutoff_at,
                timezone_name=timezone_name,
                raw_outcome_mapping=raw_outcome_mapping,
                content_hash=snapshot_hash,
            )
        else:
            snapshot_id = snapshot["id"]

        # token_ids: {index: pm_token_id}（来自 candidate payouts 的 token_key→?）
        # candidate.payouts 携带 outcome_index；映射到 spec token_ids
        token_ids = {
            str(p.outcome_index): str(p.pm_token_id) for p in candidate.payouts
        }
        stable_token_ids = {
            0: {
                "token_key": token_context["yes_token_key"] if token_context else None,
                "version_no": token_context["yes_token_version_no"] if token_context else None,
                "outcome_index": 0,
            },
            1: {
                "token_key": token_context["no_token_key"] if token_context else None,
                "version_no": token_context["no_token_version_no"] if token_context else None,
                "outcome_index": 1,
            },
        }
        payout_irs = {p.token_key: dict(p.function_ir) for p in candidate.payouts}
        content = spec_canonical_content(
            contract_key=candidate.contract_key,
            resolution_states=candidate.resolution_states,
            token_ids=stable_token_ids,
            payout_irs=payout_irs,
            snapshot_hash=snapshot_hash,
            compiler_version=candidate.compiler_version,
            schema_version=candidate.schema_version,
        )
        content_hash = canonical_hash(content)

        ok = reason is None
        status = "pass" if ok else "fail"
        existing_spec = await self._sem.get_spec_by_hash(
            session, contract_key=candidate.contract_key, content_hash=content_hash
        )
        if existing_spec is None:
            spec_id = await self._sem.insert_spec(
                session,
                contract_key=candidate.contract_key,
                snapshot_id=snapshot_id,
                resolution_states=candidate.resolution_states,
                token_ids=token_ids,
                token_count=len(candidate.payouts),
                state_count=len(candidate.resolution_states),
                compiler_version=candidate.compiler_version,
                schema_version=candidate.schema_version,
                status=status,
                content_hash=content_hash,
                g1_reason=reason,
            )
        else:
            if existing_spec["status"] != status or existing_spec["g1_reason"] != reason:
                raise RuntimeError("g1_spec_idempotency_conflict")
            spec_id = existing_spec["id"]

        if ok and existing_spec is None:
            for payout in candidate.payouts:
                payout_content = {
                    "token_key": payout.token_key,
                    "function_ir": {k: str(v) for k, v in sorted(payout.function_ir.items())},
                }
                await self._sem.insert_payout(
                    session,
                    contract_spec_id=spec_id,
                    pm_token_id=payout.pm_token_id,
                    token_version_id=payout.token_version_id,
                    outcome_index=payout.outcome_index,
                    function_ir=payout.function_ir,
                    test_vectors=payout.test_vectors,
                    algorithm_hash=canonical_hash({"algorithm": PAYOUT_ALGORITHM_VERSION}),
                    content_hash=canonical_hash(payout_content),
                )

        gate_input_hash = canonical_hash(
            {
                "contract_key": candidate.contract_key,
                "cutoff_at": cutoff_at,
                "timezone_name": timezone_name,
                "raw_outcome_mapping": raw_outcome_mapping,
                "artifact_hash": artifact_hash,
                "snapshot_hash": snapshot_hash,
                "spec_hash": content_hash,
            }
        )
        await self._workflow.insert_gate_decision(
            session,
            gate="G1",
            target_kind="opportunity",
            target_id=opportunity_id,
            input_hash=gate_input_hash,
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            result="PASS" if ok else "FAIL",
            reason_code=reason,
            committed_at=datetime.now(timezone.utc),
        )
        return G1Result(ok=ok, spec_id=spec_id, reason=reason, content_hash=content_hash)
