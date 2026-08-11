"""Settlement / label Logic（WP-04 Checkpoint C）。

- ``audit_label_revision``：确定性 label compiler。读取冻结 spec 的 label_policy，从 DB 读取
  contract spec + payout function IR + token cashflow 事实，校验 resolution_state ∈ R_c、
  token payout 可由冻结 IR 重算且等于 actual cashflow、证据 artifact 存在且 hash 可验。
  任一冲突 → 固定 ``SETTLEMENT_CONFLICT`` 写入 dispute conflict_set 并置 ``disputed``
  （fail closed）；无证据保持 pending。通过 ``SettlementRepository.insert_label_revision``
  追加新 revision（不覆盖）。
- ``create_cluster`` / ``assign_holdout`` / ``check_split_integrity``：resolution cluster
  在 outcome 未知时创建并分配 split；一个 cluster 永不跨 split；同 contract/spec 不得属
  两个 active cluster version；membership 追加后不可搬移（DB deferred guard 兜底）。

状态机（DB guard 已强约束，Logic 前置校验）：
``pending → provisional → disputed | final_admissible | final_excluded``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.payout import apply_payout_lookup
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.settlement import CLUSTER_SPLITS, LabelRevisionInput

SETTLEMENT_CONFLICT = "SETTLEMENT_CONFLICT"
_FINAL_STATES = ("final_admissible", "final_excluded", "disputed")

_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests" / "trading" / "fixtures" / "p3_learning" / "p_evaluation_spec_v1.json"
)


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def load_label_policy() -> dict:
    """读取冻结 P3 spec 的 label_policy（与 p3_helpers.frozen_spec 同源）。"""
    with open(_SPEC_PATH, encoding="utf-8") as f:
        spec = json.load(f)
    return spec["label_policy"]


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{path}_bool_or_float_forbidden")
    return Decimal(str(value))


@dataclass(frozen=True)
class LabelRevisionResult:
    ok: bool
    label_id: int | None = None
    state: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ClusterResult:
    ok: bool
    cluster_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SplitIntegrityResult:
    ok: bool
    reason: str | None = None
    cluster_count: int = 0


class SettlementLogic:
    """label / cluster 确定性业务规则；写路径全部走 SettlementRepository。"""

    def __init__(self, settlement: SettlementRepository | None = None) -> None:
        self._settlement = settlement or SettlementRepository()

    # ---------------- label revision compiler ----------------

    async def audit_label_revision(
        self, uow: UnitOfWork, *, input_: LabelRevisionInput
    ) -> LabelRevisionResult:
        policy = load_label_policy()
        R_c, payouts, h_c = await self._load_contract_material(
            uow, input_.contract_spec_id
        )
        if R_c is None:
            return LabelRevisionResult(False, reason="label_contract_spec_missing")

        # version / supersede 派生（identity = contract_spec + label_key + version_no）。
        supersedes_id = input_.supersedes_id
        if supersedes_id is None:
            current = await self._settlement.get_label_current(
                uow.session, input_.contract_spec_id, input_.label_key
            )
            if current is not None:
                return LabelRevisionResult(False, reason="label_supersede_required")
            version_no = 1
            prev_state: str | None = None
        else:
            prev = await self._settlement.get_label_by_version(uow.session, supersedes_id)
            if prev is None:
                return LabelRevisionResult(False, reason="label_supersedes_missing")
            version_no = prev["version_no"] + 1
            prev_state = prev["state"]

        if not self._transition_allowed(policy, prev_state, input_.state):
            return LabelRevisionResult(
                False, reason=f"label_transition_invalid:{prev_state}->{input_.state}"
            )

        state = input_.state
        conflict_set: list[str] | None = list(input_.conflict_set) if input_.conflict_set else None
        exclusion_reason = input_.exclusion_reason
        resolution_state = input_.resolution_state

        if state in _FINAL_STATES:
            if not await self._evidence_ok(uow, input_):
                # 无证据 → 保持 pending（不追加 revision）。
                return LabelRevisionResult(False, reason="label_evidence_missing")
            conflicts = self._detect_conflicts(
                policy, input_, R_c, payouts, h_c
            )
            if conflicts:
                # 固定 SETTLEMENT_CONFLICT → disputed（fail closed）。
                if state in ("final_admissible", "final_excluded"):
                    state = "disputed"
                    conflict_set = sorted(set(conflicts))
                    exclusion_reason = None
                else:
                    conflict_set = sorted(set((conflict_set or []) + conflicts))

        if state == "disputed" and not conflict_set:
            return LabelRevisionResult(False, reason="label_disputed_requires_conflict")
        if state == "final_excluded" and not exclusion_reason:
            return LabelRevisionResult(False, reason="label_excluded_requires_reason")
        if state == "final_admissible" and resolution_state is None:
            return LabelRevisionResult(False, reason="label_admissible_requires_resolution")

        label_id = await self._settlement.insert_label_revision(
            uow.session,
            contract_spec_id=input_.contract_spec_id,
            label_key=input_.label_key,
            version_no=version_no,
            state=state,
            resolution_state=resolution_state,
            resolution_source=input_.resolution_source,
            evidence_artifact_id=input_.evidence_artifact_id,
            raw_outcome=json.dumps(input_.raw_outcome) if input_.raw_outcome is not None else None,
            token_cashflow=json.dumps(input_.token_cashflow) if input_.token_cashflow is not None else None,
            policy_code_hash=input_.policy_code_hash,
            supersedes_id=supersedes_id,
            auditor_identity=input_.auditor_identity,
            exclusion_reason=exclusion_reason,
            conflict_set=json.dumps(conflict_set) if conflict_set is not None else None,
        )
        return LabelRevisionResult(
            True,
            label_id=label_id,
            state=state,
            reason=SETTLEMENT_CONFLICT if state == "disputed" else None,
        )

    # ---------------- cluster / split ----------------

    async def create_cluster(
        self,
        uow: UnitOfWork,
        *,
        split: str,
        time_block_start: datetime,
        time_block_end: datetime,
        horizon: str,
        contract_spec_ids: list[int],
        token_ids: list[int],
    ) -> ClusterResult:
        if split not in CLUSTER_SPLITS:
            return ClusterResult(False, reason=f"cluster_split_unknown:{split}")
        if time_block_end <= time_block_start:
            return ClusterResult(False, reason="cluster_block_order_invalid")
        if not contract_spec_ids or len(contract_spec_ids) != len(set(contract_spec_ids)):
            return ClusterResult(False, reason="cluster_spec_set_invalid")

        for spec_id in sorted(set(contract_spec_ids)):
            active = await self._active_cluster_for_spec(uow, spec_id)
            if active is not None:
                return ClusterResult(
                    False, reason=f"cluster_contract_already_active:{spec_id}"
                )

        cluster_key = canonical_hash(
            {
                "kind": "resolution_cluster",
                "split": split,
                "time_block_start": time_block_start,
                "time_block_end": time_block_end,
                "horizon": horizon,
                "contract_spec_ids": sorted(set(contract_spec_ids)),
                "token_ids": sorted(set(token_ids)),
            }
        )
        cluster_id = await self._settlement.insert_cluster(
            uow.session,
            cluster_key=cluster_key,
            cluster_version=1,
            split=split,
            time_block_start=time_block_start,
            time_block_end=time_block_end,
            horizon=horizon,
            status="OPEN",
        )
        for spec_id in sorted(set(contract_spec_ids)):
            for token_id in sorted(set(token_ids)):
                await self._settlement.insert_cluster_membership(
                    uow.session,
                    resolution_cluster_id=cluster_id,
                    contract_spec_id=spec_id,
                    token_id=token_id,
                )
        return ClusterResult(True, cluster_id=cluster_id)

    async def assign_holdout(
        self,
        uow: UnitOfWork,
        *,
        time_block_start: datetime,
        time_block_end: datetime,
        horizon: str,
        contract_spec_ids: list[int],
        token_ids: list[int],
    ) -> ClusterResult:
        """forward-holdout 专用别名：split 固定为 forward_holdout（创建时 outcome 未知）。"""
        return await self.create_cluster(
            uow,
            split="forward_holdout",
            time_block_start=time_block_start,
            time_block_end=time_block_end,
            horizon=horizon,
            contract_spec_ids=contract_spec_ids,
            token_ids=token_ids,
        )

    async def check_split_integrity(self, uow: UnitOfWork) -> SplitIntegrityResult:
        """验证无 cluster 跨 split、holdout 未被 tamper（比较 spec 的 split_policy）。"""
        rows = await self._active_clusters(uow)
        by_spec: dict[int, set[str]] = {}
        holdout_specs: set[int] = set()
        for row in rows:
            split = row["split"]
            spec_id = row["contract_spec_id"]
            by_spec.setdefault(spec_id, set()).add(split)
            if split == "forward_holdout":
                holdout_specs.add(spec_id)
        for spec_id, splits in by_spec.items():
            if len(splits) > 1:
                return SplitIntegrityResult(
                    False, reason=f"cluster_split_crossing:{spec_id}", cluster_count=len(rows)
                )
        # holdout 不得引用 final_admissible label（开封即 tamper）。
        if holdout_specs:
            tampered = await self._holdout_tampered(uow, holdout_specs)
            if tampered:
                return SplitIntegrityResult(
                    False, reason="holdout_tampered", cluster_count=len(rows)
                )
        return SplitIntegrityResult(True, cluster_count=len(rows))

    # ---------------- helpers ----------------

    async def _load_contract_material(
        self, uow: UnitOfWork, contract_spec_id: int
    ) -> tuple[list[str] | None, dict[int, dict], dict]:
        spec_result = await uow.session.execute(
            text(
                "SELECT kc_resolution_states, contract_key FROM trading.contract_specs "
                "WHERE id=:cs"
            ),
            {"cs": contract_spec_id},
        )
        spec_row = spec_result.first()
        if spec_row is None:
            return None, {}, {}
        R_c = list(spec_row[0])
        payout_result = await uow.session.execute(
            text(
                "SELECT pm_token_id, function_ir FROM trading.payout_functions "
                "WHERE contract_spec_id=:cs ORDER BY outcome_index"
            ),
            {"cs": contract_spec_id},
        )
        payouts = {int(row[0]): row[1] for row in payout_result.fetchall()}
        hc_result = await uow.session.execute(
            text(
                "SELECT h_c FROM trading.forecast_component_contract_specs "
                "WHERE contract_spec_id=:cs LIMIT 1"
            ),
            {"cs": contract_spec_id},
        )
        hc_row = hc_result.first()
        h_c = hc_row[0] if hc_row else {}
        return R_c, payouts, h_c

    async def _evidence_ok(self, uow: UnitOfWork, input_: LabelRevisionInput) -> bool:
        if input_.evidence_artifact_id is None:
            return False
        result = await uow.session.execute(
            text("SELECT sha256 FROM trading.artifact_objects WHERE id=:a"),
            {"a": input_.evidence_artifact_id},
        )
        row = result.first()
        if row is None:
            return False
        sha = row[0]
        if not isinstance(sha, str) or len(sha) != 64:
            return False
        # 证据 artifact 内容 hash 必须可验（canonical hash of raw outcome）。
        return canonical_hash(input_.raw_outcome or {}) == sha

    def _detect_conflicts(
        self,
        policy: dict,
        input_: LabelRevisionInput,
        R_c: list[str],
        payouts: dict[int, dict],
        h_c: dict,
    ) -> list[str]:
        conflicts: list[str] = []
        if input_.resolution_state not in R_c:
            conflicts.append("rule")
        if not input_.resolution_source:
            conflicts.append("resolution_source")
        cashflow = input_.token_cashflow or {}
        actual_cashflow = {}
        if isinstance(input_.raw_outcome, dict):
            actual_cashflow = input_.raw_outcome.get("actual_cashflow") or {}
        # token_mapping / rule：token_cashflow 与冻结 IR 重算不符。
        for token_key, reported in cashflow.items():
            token_id = self._token_key_to_id(payouts, token_key)
            if token_id is None:
                conflicts.append("token_mapping")
                continue
            expected = self._expected_payout(payouts[token_id], input_.resolution_state)
            if expected is None or _decimal(reported, "label_cashflow") != expected:
                conflicts.append("token_mapping")
                conflicts.append("rule")
        # cashflow：raw_outcome.actual_cashflow 与冻结 IR 重算不符。
        for token_key, reported in actual_cashflow.items():
            token_id = self._token_key_to_id(payouts, token_key)
            if token_id is None:
                conflicts.append("cashflow")
                continue
            expected = self._expected_payout(payouts[token_id], input_.resolution_state)
            if expected is None or _decimal(reported, "label_actual_cashflow") != expected:
                conflicts.append("cashflow")
        return sorted(set(conflicts))

    @staticmethod
    def _token_key_to_id(payouts: dict[int, dict], token_key: Any) -> int | None:
        # token_cashflow 的键可能是 pm_token_id（int/str）或 token_key 字符串。
        for token_id in payouts:
            if str(token_id) == str(token_key):
                return int(token_id)
        return None

    @staticmethod
    def _expected_payout(ir: dict, resolution_state: str) -> Decimal | None:
        try:
            return apply_payout_lookup(ir, resolution_state)
        except ValueError:
            return None

    @staticmethod
    def _transition_allowed(policy: dict, prev_state: str | None, next_state: str) -> bool:
        if prev_state is None:
            return next_state == "pending"
        for transition in policy.get("transitions", []):
            if transition.get("from") == prev_state and transition.get("to") == next_state:
                return True
        return False

    async def _active_cluster_for_spec(
        self, uow: UnitOfWork, contract_spec_id: int
    ) -> dict | None:
        result = await uow.session.execute(
            text(
                "SELECT c.id FROM trading.resolution_cluster_memberships m "
                "JOIN trading.resolution_clusters c ON c.id=m.resolution_cluster_id "
                "WHERE m.contract_spec_id=:cs AND c.status IN ('OPEN','FROZEN') "
                "LIMIT 1"
            ),
            {"cs": contract_spec_id},
        )
        row = result.first()
        return {"id": row[0]} if row is not None else None

    async def _active_clusters(self, uow: UnitOfWork) -> list[dict]:
        result = await uow.session.execute(
            text(
                "SELECT c.id, c.split, c.cluster_key, m.contract_spec_id "
                "FROM trading.resolution_clusters c "
                "JOIN trading.resolution_cluster_memberships m ON m.resolution_cluster_id=c.id "
                "WHERE c.status IN ('OPEN','FROZEN') "
                "ORDER BY c.id, m.contract_spec_id"
            )
        )
        return _rows(result)

    async def _holdout_tampered(self, uow: UnitOfWork, holdout_specs: set[int]) -> bool:
        result = await uow.session.execute(
            text(
                "SELECT 1 FROM trading.resolution_labels rl "
                "WHERE rl.contract_spec_id = ANY(:specs) AND rl.state='final_admissible' "
                "LIMIT 1"
            ),
            {"specs": list(holdout_specs)},
        )
        return result.first() is not None
