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

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.domain.trading.evaluation_policy import evaluation_policy
from app.domain.trading.payout import apply_payout_lookup
from app.repositories.trading.settlement import SettlementRepository
from app.schemas.trading.settlement import CLUSTER_SPLITS, LabelRevisionInput
from app.services.artifact_store import ArtifactRef, ArtifactStore

SETTLEMENT_CONFLICT = "SETTLEMENT_CONFLICT"
_FINAL_STATES = ("final_admissible", "final_excluded", "disputed")

def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def load_label_policy() -> dict:
    """Read the deployment-owned, content-verified frozen label policy."""
    return evaluation_policy("label_policy")


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

    def __init__(
        self,
        settlement: SettlementRepository | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._settlement = settlement or SettlementRepository()
        self._artifact_store = artifact_store

    # ---------------- label revision compiler ----------------

    async def audit_label_revision(
        self, uow: UnitOfWork, *, input_: LabelRevisionInput
    ) -> LabelRevisionResult:
        policy = load_label_policy()
        if input_.policy_code_hash != canonical_hash(policy):
            return LabelRevisionResult(False, reason="label_policy_hash_mismatch")
        if input_.state in _FINAL_STATES and not (input_.auditor_identity or "").strip():
            return LabelRevisionResult(False, reason="label_terminal_auditor_required")
        R_c, payouts, h_c, expected_resolution_source = await self._load_contract_material(
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
                policy, input_, R_c, payouts, h_c, expected_resolution_source
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
        contract_spec_ids: list[int] | None = None,
        token_ids: list[int] | None = None,
        contract_token_ids: dict[int, list[int]] | None = None,
    ) -> ClusterResult:
        if split not in CLUSTER_SPLITS:
            return ClusterResult(False, reason=f"cluster_split_unknown:{split}")
        if time_block_end <= time_block_start:
            return ClusterResult(False, reason="cluster_block_order_invalid")
        mapping, mapping_error = self._exact_contract_token_mapping(
            contract_spec_ids=contract_spec_ids,
            token_ids=token_ids,
            contract_token_ids=contract_token_ids,
        )
        if mapping_error is not None:
            return ClusterResult(False, reason=mapping_error)
        assert mapping is not None
        contract_spec_ids = sorted(mapping)

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
                "contract_token_ids": {
                    str(spec_id): mapping[spec_id] for spec_id in contract_spec_ids
                },
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
        for spec_id in contract_spec_ids:
            for token_id in mapping[spec_id]:
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
        contract_spec_ids: list[int] | None = None,
        token_ids: list[int] | None = None,
        contract_token_ids: dict[int, list[int]] | None = None,
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
            contract_token_ids=contract_token_ids,
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
    ) -> tuple[list[str] | None, dict[int, dict], dict, str | None]:
        spec_result = await uow.session.execute(
            text(
                "SELECT cs.kc_resolution_states, cs.contract_key, s.resolution_source "
                "FROM trading.contract_specs cs "
                "LEFT JOIN trading.contract_snapshots s ON s.id=cs.snapshot_id "
                "WHERE cs.id=:cs"
            ),
            {"cs": contract_spec_id},
        )
        spec_row = spec_result.first()
        if spec_row is None:
            return None, {}, {}, None
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
        return R_c, payouts, h_c, spec_row[2]

    async def _evidence_ok(self, uow: UnitOfWork, input_: LabelRevisionInput) -> bool:
        if input_.evidence_artifact_id is None or self._artifact_store is None:
            return False
        result = await uow.session.execute(
            text(
                "SELECT sha256, original_size, stored_size, mime, compression, "
                "       storage_driver, storage_version, locator "
                "FROM trading.artifact_objects WHERE id=:a"
            ),
            {"a": input_.evidence_artifact_id},
        )
        row = result.first()
        if row is None:
            return False
        sha = row[0]
        if not isinstance(sha, str) or len(sha) != 64:
            return False
        try:
            ref = ArtifactRef(
                sha256=sha,
                original_size=int(row[1]),
                stored_size=int(row[2]),
                mime=row[3],
                compression=row[4],
                storage_driver=row[5],
                storage_version=row[6],
                locator=row[7],
            )
            payload = self._artifact_store.get_bytes(ref, verify=True)
            decoded = json.loads(payload)
        except Exception:
            return False
        return (
            payload == canonical_bytes(decoded)
            and decoded == (input_.raw_outcome or {})
            and canonical_hash(decoded) == sha
        )

    def _detect_conflicts(
        self,
        policy: dict,
        input_: LabelRevisionInput,
        R_c: list[str],
        payouts: dict[int, dict],
        h_c: dict,
        expected_resolution_source: str | None,
    ) -> list[str]:
        conflicts: list[str] = []
        if input_.resolution_state not in R_c:
            conflicts.append("rule")
        if (
            not expected_resolution_source
            or input_.resolution_source != expected_resolution_source
        ):
            conflicts.append("resolution_source")
        cashflow = input_.token_cashflow
        actual_cashflow: Any = None
        if isinstance(input_.raw_outcome, dict):
            actual_cashflow = input_.raw_outcome.get("actual_cashflow")

        # h must actually map at least one frozen world state to the reported R_c state.
        # Merely accepting a string that appears in R_c is not an h/g recomputation.
        if not isinstance(h_c, dict) or input_.resolution_state not in set(h_c.values()):
            conflicts.append("rule")

        # Every payout function is required exactly once.  Iterating only caller supplied
        # keys allowed an empty or partial map to become final_admissible.
        expected_ids = set(payouts)
        cashflow_ids = self._cashflow_token_ids(payouts, cashflow)
        if cashflow_ids is None or cashflow_ids != expected_ids:
            conflicts.append("token_mapping")
        else:
            for token_key, reported in cashflow.items():
                token_id = self._token_key_to_id(payouts, token_key)
                assert token_id is not None
                expected = self._expected_payout(payouts[token_id], input_.resolution_state)
                try:
                    matches = expected is not None and _decimal(
                        reported, "label_cashflow"
                    ) == expected
                except (ValueError, ArithmeticError):
                    matches = False
                if not matches:
                    conflicts.extend(("token_mapping", "rule"))

        # If the evidence payload carries an independently observed cashflow, it too must
        # be a complete exact map.  ``token_cashflow`` remains the required canonical
        # actual-cashflow field for sources that do not duplicate it inside raw evidence.
        if actual_cashflow is not None:
            actual_ids = self._cashflow_token_ids(payouts, actual_cashflow)
            if actual_ids is None or actual_ids != expected_ids:
                conflicts.append("cashflow")
            else:
                for token_key, reported in actual_cashflow.items():
                    token_id = self._token_key_to_id(payouts, token_key)
                    assert token_id is not None
                    expected = self._expected_payout(
                        payouts[token_id], input_.resolution_state
                    )
                    try:
                        matches = expected is not None and _decimal(
                            reported, "label_actual_cashflow"
                        ) == expected
                    except (ValueError, ArithmeticError):
                        matches = False
                    if not matches:
                        conflicts.append("cashflow")
        return sorted(set(conflicts))

    @classmethod
    def _cashflow_token_ids(
        cls, payouts: dict[int, dict], cashflow: Any
    ) -> set[int] | None:
        if not isinstance(cashflow, dict):
            return None
        token_ids: list[int] = []
        for token_key in cashflow:
            token_id = cls._token_key_to_id(payouts, token_key)
            if token_id is None:
                return None
            token_ids.append(token_id)
        if len(token_ids) != len(set(token_ids)):
            return None
        return set(token_ids)

    @staticmethod
    def _exact_contract_token_mapping(
        *,
        contract_spec_ids: list[int] | None,
        token_ids: list[int] | None,
        contract_token_ids: dict[int, list[int]] | None,
    ) -> tuple[dict[int, list[int]] | None, str | None]:
        if contract_token_ids is not None:
            if contract_spec_ids is not None or token_ids is not None:
                return None, "cluster_token_mapping_ambiguous"
            raw_mapping = contract_token_ids
        else:
            specs = list(contract_spec_ids or [])
            tokens = list(token_ids or [])
            if len(specs) != 1:
                return None, "cluster_exact_token_mapping_required"
            raw_mapping = {specs[0]: tokens}
        if not raw_mapping:
            return None, "cluster_spec_set_invalid"
        mapping: dict[int, list[int]] = {}
        for raw_spec, raw_tokens in raw_mapping.items():
            spec_id = int(raw_spec)
            values = [int(value) for value in raw_tokens]
            if spec_id <= 0 or not values or any(value <= 0 for value in values):
                return None, "cluster_token_mapping_invalid"
            if len(values) != len(set(values)) or spec_id in mapping:
                return None, "cluster_token_mapping_invalid"
            mapping[spec_id] = sorted(values)
        return mapping, None

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
                "JOIN trading.resolution_cluster_memberships m "
                "  ON m.contract_spec_id=rl.contract_spec_id "
                "JOIN trading.resolution_clusters c ON c.id=m.resolution_cluster_id "
                "WHERE rl.contract_spec_id = ANY(:specs) AND c.split='forward_holdout' "
                "  AND rl.state='final_admissible' AND rl.created_at <= m.added_at "
                "LIMIT 1"
            ),
            {"specs": list(holdout_specs)},
        )
        return result.first() is not None


# ======================================================================
# WP-06 Checkpoint C —— chain settlement logic（Polygon/Relayer/CTF 结算闭环）
#
# - ``assess_settlement``：五类 source exact set 一致才 admissible；缺项/冲突 →
#   ``SETTLEMENT_CONFLICT``，G8/score/learning/redeem/ledger effect=0。
# - ``prepare_redeem``：两次 preflight 全等后创建 REDEEM operation（fake-only、
#   authorized_capital=0、registry 版本/chain/code exact 复核）。
# - ``apply_finality``：FINALIZED 在同一 UoW 写 execution/position/balanced ledger/
#   audit；effect 只产生一次（idempotent）。
# - ``recover_unknown``：UNKNOWN 只读恢复（relayer transaction/nonce/receipt/finalized
#   block/pre-post balance），绝不盲重发。
# ======================================================================

from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.domain.trading.payout import (
    build_redeem_calldata,
    function_selector,
    verify_payout_consistency,
)
from app.models.trading.settlement import (
    CHAIN_OPERATION_ACTIVE_STATES,
    CHAIN_OPERATION_STATES,
    SETTLEMENT_SOURCE_KINDS,
)
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.settlement import (
    ChainOperationRepository,
    ContractRegistryRepository,
    SettlementObservationRepository,
    SettlementRepository,
)


@dataclass(frozen=True)
class SettlementAssessment:
    """五元组 exact set 核验结果。admissible=True 才允许 redeem/ledger。"""

    admissible: bool
    conflict_reason: str | None = None
    winner: str | None = None
    is_50_50: bool | None = None
    payout_numerator: str | None = None
    payout_denominator: str | None = None
    present_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedOperation:
    operation_id: int
    operation_key: str
    economic_hash: str
    expected_operation_hash: str
    calldata: str
    body_hash: str


class ChainSettlementLogic:
    """Polygon/Relayer/CTF 结算与兑换（fake-only；require injected wire）。"""

    def __init__(
        self,
        *,
        chain_operations: ChainOperationRepository | None = None,
        registry_repo: ContractRegistryRepository | None = None,
        observations: SettlementObservationRepository | None = None,
        settlement: SettlementRepository | None = None,
        audit: AuditRepository | None = None,
        execution: ExecutionRepository | None = None,
        ledger: LedgerRepository | None = None,
        chain_id: int = 137,
        registry_version: str = "polygon-mainnet-v1",
        adapter_standard: str = "0xAdA100Db00Ca00073811820692005400218FcE1f",
        adapter_neg_risk: str = "0xadA2005600Dec949baf300f4C6120000bDB6eAab",
        pusd: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
        ctf: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        deposit_wallet: str = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
        parent_collection_id: str = "0x" + "00" * 32,
        partition: tuple[str, ...] = ("1", "2"),
        pusd_base_units_per_pair: int = 1_000_000,
    ) -> None:
        self.chain_operations = chain_operations or ChainOperationRepository()
        self.registry_repo = registry_repo or ContractRegistryRepository()
        self.observations = observations or SettlementObservationRepository()
        self.settlement = settlement or SettlementRepository()
        self.audit = audit or AuditRepository()
        self.execution = execution or ExecutionRepository()
        self.ledger = ledger or LedgerRepository()
        self.chain_id = chain_id
        self.registry_version = registry_version
        self.adapter_standard = adapter_standard
        self.adapter_neg_risk = adapter_neg_risk
        self.pusd = pusd
        self.ctf = ctf
        self.deposit_wallet = deposit_wallet
        self.parent_collection_id = parent_collection_id
        self.partition = tuple(partition)
        self.pusd_base_units_per_pair = pusd_base_units_per_pair

    # ---------------- settlement assessment ----------------

    async def assess_settlement(self, uow: UnitOfWork, condition_id: str
                                ) -> SettlementAssessment:
        """五元组 exact set 核验：缺一项或 payout/winner/token/cutoff 冲突 effect=0。"""
        rows = await self.observations.get_observations(uow.session, condition_id)
        by_kind: dict[str, dict] = {}
        for row in rows:
            kind = row["source_kind"]
            if kind in by_kind:
                return SettlementAssessment(False, "duplicate_source_kind",
                                            present_kinds=tuple(sorted(by_kind)))
            by_kind[kind] = row
        present = tuple(sorted(by_kind))
        if set(present) != set(SETTLEMENT_SOURCE_KINDS):
            return SettlementAssessment(False, f"incomplete_source_set:{present}",
                                        present_kinds=present)
        # 任一 source status=CONFLICT → conflict
        if any(r["status"] == "CONFLICT" for r in rows):
            return SettlementAssessment(False, "source_conflict", present_kinds=present)
        payout = by_kind["ctf_payout"]
        winner = by_kind["clob_winner_5050"]
        data = by_kind["data_api_redeemable"]
        gamma = by_kind["gamma_clob_closed"]
        label = by_kind["label_audit"]
        # Gamma/CLOB 必须 closed && !acceptingOrders（记录于 observation payload）
        gamma_payload = gamma.get("payload") or {}
        if not (gamma_payload.get("closed") is True
                and gamma_payload.get("accepting_orders") is False):
            return SettlementAssessment(False, "market_not_closed", present_kinds=present)
        # Data API redeemable 必须 true
        if data.get("redeemable") is not True:
            return SettlementAssessment(False, "not_redeemable", present_kinds=present)
        # label audit 必须 final_admissible（记录于 observation payload）
        label_payload = label.get("payload") or {}
        if label_payload.get("status") != "final_admissible":
            return SettlementAssessment(False, "label_not_final", present_kinds=present)
        # CTF payout 与 CLOB winner/50-50 一致性
        outcome = payout.get("outcome_index")
        numerator = payout.get("numerator")
        denominator = payout.get("denominator")
        if not (outcome and numerator and denominator):
            return SettlementAssessment(False, "payout_incomplete", present_kinds=present)
        consistent = verify_payout_consistency(
            ctf_payout_outcome=outcome, ctf_numerator=numerator,
            ctf_denominator=denominator,
            clob_winner=winner.get("winner"),
            clob_is_50_50=winner.get("is_50_50_outcome"),
        )
        if not consistent:
            return SettlementAssessment(False, "payout_winner_conflict", present_kinds=present)
        return SettlementAssessment(
            admissible=True,
            winner=winner.get("winner"),
            is_50_50=winner.get("is_50_50_outcome"),
            payout_numerator=numerator,
            payout_denominator=denominator,
            present_kinds=present,
        )

    # ---------------- redeem preparation ----------------

    async def _verify_registry(self, uow: UnitOfWork, *, kind: str,
                               expected_content_hash: str) -> dict:
        """registry 版本/chain/code exact 复核（启动与每次 operation 前）。"""
        entry = await self.registry_repo.get_active(
            uow.session, chain_id=self.chain_id, kind=kind
        )
        if entry is None:
            raise RuntimeError(f"registry_missing:{kind}")
        if entry["content_hash"] != expected_content_hash:
            raise RuntimeError("registry_version_drift")
        if entry["chain_id"] != self.chain_id:
            raise RuntimeError("registry_chain_drift")
        return entry

    def _build_operation_hashes(self, *, binding: dict, calls: list[dict]) -> dict:
        """冻结 economic_hash/call_set_hash/expected_operation_hash（确定性）。"""
        economic_hash = canonical_hash({
            "operation_type": binding["operation_type"],
            "account_id": binding["account_id"],
            "wallet_address": binding["wallet_address"],
            "condition_id": binding["condition_id"],
            "market_id": binding.get("market_id"),
            "amount_base_units": binding["amount_base_units"],
            "target_address": binding["target_address"],
        })
        call_set_hash = canonical_hash(calls)
        expected_operation_hash = canonical_hash({
            **binding,
            "call_set": calls,
            "registry_version": self.registry_version,
            "chain_id": self.chain_id,
            "deposit_wallet": self.deposit_wallet,
        })
        return {
            "economic_hash": economic_hash,
            "call_set_hash": call_set_hash,
            "expected_operation_hash": expected_operation_hash,
        }

    async def prepare_redeem(
        self,
        uow: UnitOfWork,
        *,
        operation_key: str,
        idempotency_key: str,
        account_id: int,
        wallet_address: str,
        condition_id: str,
        market_id: int | None,
        neg_risk: bool,
        registry_content_hash: str,
        permission_ref: str,
        release_manifest_id: int,
        capital_permission_manifest_id: int,
        fencing_token: int,
    ) -> PreparedOperation:
        """两次 preflight 全等后创建 REDEEM operation；caller 不可覆盖 adapter/calldata。"""
        # preflight 1：registry + assessment
        adapter = self.adapter_neg_risk if neg_risk else self.adapter_standard
        entry = await self._verify_registry(
            uow, kind="neg_risk_adapter" if neg_risk else "ctf_adapter_standard",
            expected_content_hash=registry_content_hash,
        )
        assessment = await self.assess_settlement(uow, condition_id)
        if not assessment.admissible:
            raise RuntimeError(f"settlement_not_admissible:{assessment.conflict_reason}:{condition_id}")
        # preflight 2：calldata 确定性 + 全等（两次结果必须一致）
        calldata = build_redeem_calldata(
            collateral_address=self.ctf, condition_id=condition_id,
            parent_collection_id=self.parent_collection_id,
            partition=list(self.partition),
        )
        calldata_2 = build_redeem_calldata(
            collateral_address=self.ctf, condition_id=condition_id,
            parent_collection_id=self.parent_collection_id,
            partition=list(self.partition),
        )
        if calldata != calldata_2:
            raise RuntimeError("preflight_calldata_mismatch")
        calls = [{"target": adapter, "value": "0", "data": calldata}]
        # exact body（fake signature 占位；发送前由 signer 替换，body_hash 冻结结构）
        fake_signature = "0x" + "0" * 130
        body = {
            "type": "WALLET",
            "from": wallet_address,
            "to": self.deposit_wallet,
            "nonce": "0",
            "signature": fake_signature,
            "metadata": "pm-v2-settlement/v1",
            "depositWalletParams": {
                "depositWallet": wallet_address,
                "deadline": "0",
                "calls": calls,
            },
        }
        body_bytes = json.dumps(body, separators=(",", ":")).encode()
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        binding = {
            "operation_type": "REDEEM",
            "account_id": account_id,
            "wallet_address": wallet_address,
            "condition_id": condition_id,
            "market_id": market_id,
            "target_address": adapter,
            "amount_base_units": 0,
        }
        hashes = self._build_operation_hashes(binding=binding, calls=calls)
        # 并发 claim：非分区 idempotency_claims
        claimed = await self.chain_operations.claim_idempotency(
            uow.session, key=idempotency_key, owner="chain-settlement"
        )
        if not claimed:
            raise RuntimeError("redeem_idempotency_conflict")
        from eth_utils import keccak as _keccak

        calldata_keccak = _keccak(bytes.fromhex(calldata[2:])).hex()
        op_id = await self.chain_operations.insert_operation(
            uow.session,
            {
                **binding,
                "operation_key": operation_key,
                "idempotency_key": idempotency_key,
                "chain_id": self.chain_id,
                "registry_version_id": entry["id"],
                "permission_ref": permission_ref,
                "release_manifest_id": release_manifest_id,
                "capital_permission_manifest_id": capital_permission_manifest_id,
                "fencing_token": fencing_token,
                "calldata": calldata,
                "calldata_keccak": calldata_keccak,
                **hashes,
                "body_hash": body_hash,
                "preflight_hash1": canonical_hash({"calldata": calldata}),
                "preflight_hash2": canonical_hash({"calldata": calldata_2}),
            },
        )
        return PreparedOperation(
            operation_id=op_id, operation_key=operation_key,
            economic_hash=hashes["economic_hash"],
            expected_operation_hash=hashes["expected_operation_hash"],
            calldata=calldata, body_hash=body_hash,
        )

    # ---------------- finality & ledger effect ----------------

    async def apply_finality(self, uow: UnitOfWork, operation_id: int,
                             *, winning_token_id: int | None = None) -> dict:
        """FINALIZED 在同一 UoW 写 execution/position/balanced ledger/audit；effect 一次。"""
        op = await self.chain_operations.get_for_update(uow.session, operation_id)
        if op is None:
            raise RuntimeError("chain_operation_missing")
        if op["status"] != "FINALIZED":
            raise RuntimeError("chain_operation_not_finalized")
        # idempotency：已存在该 operation 的 SETTLEMENT 账务即跳过（effect 只一次）
        existing = await self._settlement_ledger_for_operation(uow, operation_id)
        if existing is not None:
            return {"applied": False, "transaction_key": existing["transaction_key"]}

        pre = op["pre_balance"] or {}
        post = op["post_balance"] or {}
        try:
            g = Decimal(str(post.get("pusd", 0))) - Decimal(str(pre.get("pusd", 0)))
        except Exception as exc:
            raise RuntimeError("settlement_balance_delta_invalid") from exc
        # redeem 消费全部 outcome 余额：T = pre 的 winning token 余额
        outcome = op["condition_id"]
        t = Decimal(str(pre.get("token", 0)))
        portfolio_namespace = f"chain:{op['wallet_address']}"
        tx_key = f"settle-{op['operation_key']}"
        tx_id = await self.ledger.insert_transaction(
            uow.session,
            transaction_key=tx_key,
            kind="SETTLEMENT",
            trade_decision_id=None,
            execution_id=None,
            portfolio_namespace=portfolio_namespace,
            # WP-05 real-fill lineage guard：settlement 账务不设 account_id（避免填充链要求），
            # account 关联通过 chain_operation_id → chain_operations.account_id 派生。
            account_id=None,
            chain_operation_id=operation_id,
        )
        # base-unit 整数金额（ledger postings 要求 NUMERIC(38,0)；jsonb_to_recordset 需 int）
        g_int = int(g)
        t_int = int(t)
        postings = [
            {"posting_no": 0, "asset_type": "CASH", "asset_key": self.pusd,
             "amount": g_int, "counterparty": f"ctf-redeem:{outcome}"},
            {"posting_no": 1, "asset_type": "CASH", "asset_key": self.pusd,
             "amount": -g_int, "counterparty": f"ctf-redeem:{outcome}"},
            {"posting_no": 2, "asset_type": "TOKEN", "asset_key": outcome,
             "amount": -t_int, "counterparty": f"ctf-redeem:{outcome}"},
            {"posting_no": 3, "asset_type": "TOKEN", "asset_key": outcome,
             "amount": t_int, "counterparty": f"ctf-redeem:{outcome}"},
        ]
        if g == 0 and t == 0:
            # 空 effect：仍写账务容器（SETTLEMENT kind）以证明 effect 只产生一次
            postings = []
        if postings:
            await self.ledger.insert_postings(uow.session, transaction_id=tx_id, postings=postings)
            await self.ledger.mark_posted(uow.session, tx_id, posted_at=_utcnow())
        # 审计：settlement applied（append-only workflow event）
        await self.audit.insert_workflow_event(
            uow.session,
            event_key=f"settle-applied-{operation_id}",
            event_type="SETTLEMENT_FINALIZED",
            aggregate_type="chain_operation",
            aggregate_id=op["operation_key"],
            payload_hash=canonical_hash({
                "operation_id": operation_id, "ledger_transaction_id": tx_id,
                "pusd_delta": str(g), "token_delta": str(t),
            }),
            payload={
                "operation_id": operation_id,
                "ledger_transaction_id": tx_id,
                "pusd_delta": str(g),
                "token_delta": str(t),
                "condition_id": outcome,
            },
        )
        return {"applied": True, "transaction_key": tx_key,
                "pusd_delta": g, "token_delta": t}

    async def _settlement_ledger_for_operation(self, uow: UnitOfWork,
                                               operation_id: int) -> dict | None:
        result = await uow.session.execute(
            text(
                "SELECT * FROM trading.ledger_transactions "
                "WHERE chain_operation_id = :oid AND kind = 'SETTLEMENT' LIMIT 1"
            ),
            {"oid": operation_id},
        )
        rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]
        return rows[0] if rows else None

    # ---------------- UNKNOWN 只读恢复 ----------------

    async def recover_unknown(self, uow: UnitOfWork, operation_id: int) -> dict:
        """UNKNOWN/active operation 只读恢复：relayer transaction/nonce/receipt/finalized
        block/pre-post balance。无权威失败/不存在证据时 real resend=0（禁盲重发）。"""
        op = await self.chain_operations.get_for_update(uow.session, operation_id)
        if op is None:
            raise RuntimeError("chain_operation_missing")
        if op["status"] not in CHAIN_OPERATION_ACTIVE_STATES:
            raise RuntimeError("chain_operation_not_recoverable")
        return {
            "operation_id": operation_id,
            "status": op["status"],
            "transaction_id": op.get("transaction_id"),
            "transaction_hash": op.get("transaction_hash"),
            "nonce": op.get("relayer_nonce"),
            "blind_resend": False,
            "evidence": {
                "has_transaction_id": op.get("transaction_id") is not None,
                "has_transaction_hash": op.get("transaction_hash") is not None,
                "has_receipt": op.get("receipt_block_number") is not None,
                "has_finalized": op.get("finalized_block_number") is not None,
            },
        }


def _utcnow() -> datetime:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
