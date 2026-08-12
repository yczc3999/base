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
# This layer owns the authority gates and deterministic state transitions.  It
# never performs provider I/O: runtimes bracket every call with separate UoWs.
# ======================================================================

from app.domain.trading.payout import build_redeem_calldata, verify_payout_consistency
from app.outbox.contracts import create_envelope
from app.outbox.repository import OutboxRepository
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.settlement import (
    ChainOperationRepository,
    ContractRegistryRepository,
    SettlementObservationRepository,
)
from app.schemas.trading.settlement import (
    CHAIN_OPERATION_STATES,
    SETTLEMENT_SOURCE_KINDS,
    ChainRecoveryEvidence,
    ChainRedeemRequest,
    ChainSettlementEvidenceInput,
    ChainWireEvidence,
)

CHAIN_OPERATION_ACTIVE_STATES = frozenset(
    state
    for state in CHAIN_OPERATION_STATES
    if state not in {"FINALIZED", "INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"}
)


@dataclass(frozen=True)
class SettlementAssessment:
    """The coherent, exact five-source settlement cut."""

    admissible: bool
    conflict_reason: str | None = None
    winner: str | None = None
    is_50_50: bool | None = None
    payout_numerator: str | None = None
    payout_denominator: str | None = None
    payout_numerators: tuple[str, ...] = ()
    token_set: tuple[str, ...] = ()
    market_id: int | None = None
    as_of: datetime | None = None
    settlement_set_key: str | None = None
    present_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RedeemPreflight:
    """Non-secret DB authority material safe to carry across the read boundary."""

    request: ChainRedeemRequest
    authority_hash: str
    account_key: str
    wallet_address: str
    signing_identity: str
    registry_id: int
    registry_kind: str
    registry_version: str
    registry_content_hash: str
    registry_address: str
    registry_snapshot_block_number: int
    registry_runtime_keccak: str
    registry_resolved_address: str | None
    registry_resolved_code_keccak: str
    registry_proxy_kind: str
    registry_snapshot_block_hash: str
    registry_extra: dict[str, Any]
    registry_bundle: tuple[dict[str, Any], ...]
    registry_bundle_hash: str
    pusd_address: str
    ctf_address: str
    deposit_wallet_address: str
    release_manifest_id: int
    release_name: str
    release_total_hash: str
    permission_manifest_id: int
    permission_ref: str
    market_key: str
    market_content_hash: str
    market_version_no: int
    market_version_hash: str
    token_set: tuple[str, ...]
    settlement_set_key: str
    calldata: str
    calls: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class PreparedOperation:
    operation_id: int
    operation_key: str
    account_id: int
    fencing_token: int
    economic_hash: str
    expected_operation_hash: str
    calldata: str
    body_hash: str
    nonce: str
    deadline: datetime
    # Only the transaction that inserted the operation owns the single transport
    # boundary. Exact idempotent contenders receive the frozen row for recovery.
    transport_owner: bool = True


class ChainSettlementLogic:
    """DB-authoritative fake-conformance settlement state machine.

    There are three explicit phases:

    * :meth:`preflight_redeem` reads and hashes locked DB authority facts;
    * :meth:`prepare_redeem` repeats the preflight and persists the exact opaque
      wire hash before changing the operation to ``SUBMITTING``;
    * :meth:`apply_submit_outcome` / :meth:`apply_recovery` append provider
      observations after network calls have completed outside the UoW.
    """

    _CHAIN_ID = 137
    _DEPOSIT_WALLET = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
    _CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    _PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    _PARENT = "0x" + "00" * 32
    _PARTITION = ("1", "2")

    def __init__(
        self,
        *,
        chain_operations: ChainOperationRepository | None = None,
        registry_repo: ContractRegistryRepository | None = None,
        observations: SettlementObservationRepository | None = None,
        audit: AuditRepository | None = None,
        execution: ExecutionRepository | None = None,
        ledger: LedgerRepository | None = None,
        outbox: OutboxRepository | None = None,
        chain_id: int = 137,
        registry_version: str = "polygon-mainnet-v1",
        deposit_wallet: str = _DEPOSIT_WALLET,
        pusd: str = _PUSD,
        ctf: str = _CTF,
        parent_collection_id: str = _PARENT,
        partition: tuple[str, ...] = _PARTITION,
    ) -> None:
        if int(chain_id) != self._CHAIN_ID:
            raise ValueError("chain_settlement_chain_id_unsupported")
        self.chain_operations = chain_operations or ChainOperationRepository()
        self.registry_repo = registry_repo or ContractRegistryRepository()
        self.observations = observations or SettlementObservationRepository()
        self.audit = audit or AuditRepository()
        self.execution = execution or ExecutionRepository()
        self.ledger = ledger or LedgerRepository()
        self.outbox = outbox or OutboxRepository()
        self.chain_id = int(chain_id)
        self.registry_version = registry_version
        self.deposit_wallet = deposit_wallet
        self.pusd = pusd
        self.ctf = ctf
        self.parent_collection_id = parent_collection_id
        self.partition = tuple(partition)

    # ---------------- exact five-source assessment ----------------

    async def assess_settlement(
        self,
        uow: UnitOfWork,
        condition_id: str,
        *,
        market_id: int | None = None,
    ) -> SettlementAssessment:
        loader = getattr(self.observations, "get_complete_set", None)
        if callable(loader):
            if market_id is None:
                raise RuntimeError("settlement_market_id_required")
            rows = await loader(uow.session, condition_id=condition_id, market_id=market_id)
        else:
            rows = await self.observations.get_observations(uow.session, condition_id)

        # Only COMPLETE observations are settlement authority.  PENDING history is
        # retained but may neither create duplicates nor complete an evidence cut.
        rows = [row for row in rows if row.get("status") == "COMPLETE"]
        by_kind: dict[str, dict[str, Any]] = {}
        for row in rows:
            kind = str(row.get("source_kind"))
            if kind in by_kind:
                return SettlementAssessment(
                    False, "duplicate_source_kind", present_kinds=tuple(sorted(by_kind))
                )
            by_kind[kind] = row
        present = tuple(sorted(by_kind))
        required = set(SETTLEMENT_SOURCE_KINDS)
        if set(present) != required:
            return SettlementAssessment(
                False, f"incomplete_source_set:{present}", present_kinds=present
            )
        if any(row.get("status") == "CONFLICT" for row in rows):
            return SettlementAssessment(False, "source_conflict", present_kinds=present)

        # All five facts must describe one immutable cut, market and exact token set.
        cutoffs = {row.get("as_of") for row in rows}
        token_sets = {
            tuple(str(token).lower() for token in (row.get("token_set") or []))
            for row in rows
        }
        conditions = {str(row.get("condition_id")).lower() for row in rows}
        markets = {row.get("market_id") for row in rows}
        if len(cutoffs) != 1 or None in cutoffs:
            return SettlementAssessment(False, "source_cutoff_conflict", present_kinds=present)
        if len(token_sets) != 1 or not next(iter(token_sets)):
            return SettlementAssessment(False, "source_token_set_conflict", present_kinds=present)
        if conditions != {condition_id.lower()}:
            return SettlementAssessment(False, "source_condition_conflict", present_kinds=present)
        if len(markets) != 1 or None in markets or (market_id is not None and markets != {market_id}):
            return SettlementAssessment(False, "source_market_conflict", present_kinds=present)

        payout = by_kind["ctf_payout"]
        winner = by_kind["clob_winner_5050"]
        data = by_kind["data_api_redeemable"]
        gamma = by_kind["gamma_clob_closed"]
        label = by_kind["label_audit"]
        gamma_payload = gamma.get("payload") or {}
        label_payload = label.get("payload") or {}
        if not (
            gamma_payload.get("closed") is True
            and gamma_payload.get("accepting_orders") is False
        ):
            return SettlementAssessment(False, "market_not_closed", present_kinds=present)
        if data.get("redeemable") is not True:
            return SettlementAssessment(False, "not_redeemable", present_kinds=present)
        if (
            label_payload.get("status") != "final_admissible"
            or not label.get("label_audit_version")
        ):
            return SettlementAssessment(False, "label_not_final", present_kinds=present)

        outcome = payout.get("outcome_index")
        numerator = payout.get("numerator")
        denominator = payout.get("denominator")
        payout_vector = payout.get("payout_vector") or {}
        numerators = payout_vector.get("numerators")
        vector_denominator = payout_vector.get("denominator")
        if (
            outcome is None or numerator is None or denominator is None
            or not isinstance(numerators, list) or len(numerators) != 2
            or str(vector_denominator) != str(denominator)
        ):
            return SettlementAssessment(False, "payout_incomplete", present_kinds=present)
        try:
            rates = self._normalized_payout_rates(numerators, str(denominator))
        except RuntimeError:
            return SettlementAssessment(False, "payout_vector_invalid", present_kinds=present)
        if not verify_payout_consistency(
            ctf_payout_outcome=str(outcome),
            ctf_numerator=str(numerator),
            ctf_denominator=str(denominator),
            clob_winner=winner.get("winner"),
            clob_is_50_50=winner.get("is_50_50_outcome"),
        ):
            return SettlementAssessment(False, "payout_winner_conflict", present_kinds=present)
        expected_winner = (
            ("YES", False) if rates == (Decimal(1), Decimal(0)) else
            ("NO", False) if rates == (Decimal(0), Decimal(1)) else
            (None, True) if rates == (Decimal("0.5"), Decimal("0.5")) else
            (None, None)
        )
        if expected_winner[1] is None or (
            winner.get("winner"), winner.get("is_50_50_outcome")
        ) != expected_winner:
            return SettlementAssessment(False, "payout_winner_vector_conflict", present_kinds=present)
        label_rates = label_payload.get("token_cashflow_rates")
        if not isinstance(label_rates, list) or len(label_rates) != 2:
            return SettlementAssessment(False, "label_cashflow_missing", present_kinds=present)
        try:
            if tuple(_decimal(value, "label_cashflow") for value in label_rates) != rates:
                return SettlementAssessment(False, "label_cashflow_conflict", present_kinds=present)
        except Exception:
            return SettlementAssessment(False, "label_cashflow_invalid", present_kinds=present)

        return SettlementAssessment(
            True,
            winner=winner.get("winner"),
            is_50_50=winner.get("is_50_50_outcome"),
            payout_numerator=str(numerator),
            payout_denominator=str(denominator),
            payout_numerators=tuple(str(value) for value in numerators),
            token_set=next(iter(token_sets)),
            market_id=next(iter(markets)),
            as_of=next(iter(cutoffs)),
            settlement_set_key=str(rows[0]["settlement_set_key"]),
            present_kinds=present,
        )

    async def record_settlement_evidence(
        self,
        uow: UnitOfWork,
        *,
        evidence: ChainSettlementEvidenceInput,
    ) -> str:
        """Validate provider observations against DB facts and append one exact set."""
        market = (
            await uow.session.execute(
                text(
                    "SELECT m.id, m.gamma_market_id, m.condition_id, m.content_hash, "
                    "m.closed, m.accepting_orders, mv.version_no AS market_version_no, "
                    "mv.normalized_hash AS market_version_hash "
                    "FROM trading.pm_markets m LEFT JOIN LATERAL ("
                    " SELECT version_no, normalized_hash FROM trading.pm_market_versions "
                    " WHERE market_id=m.id ORDER BY version_no DESC LIMIT 1) mv ON true "
                    "WHERE m.id=:market FOR UPDATE OF m"
                ),
                {"market": evidence.market_id},
            )
        ).mappings().one_or_none()
        if market is None or str(market["condition_id"]).lower() != evidence.condition_id.lower():
            raise RuntimeError("settlement_evidence_market_condition_mismatch")
        if (
            market["closed"] is not True
            or market["accepting_orders"] is not False
            or evidence.gamma_closed is not True
            or evidence.gamma_accepting_orders is not False
        ):
            raise RuntimeError("settlement_evidence_market_not_closed")
        tokens = (
            await uow.session.execute(
                text(
                    "SELECT id, token_id, outcome_index FROM trading.pm_tokens "
                    "WHERE market_id=:market ORDER BY outcome_index"
                ),
                {"market": evidence.market_id},
            )
        ).mappings().all()
        # Canonical token order is outcome_index, never lexical token-id order.
        db_token_set = tuple(str(row["token_id"]).lower() for row in tokens)
        if len(tokens) != 2 or db_token_set != tuple(evidence.token_set):
            raise RuntimeError("settlement_evidence_token_set_mismatch")
        label = (
            await uow.session.execute(
                text(
                    "SELECT rl.id, rl.label_key, rl.version_no, rl.state, rl.resolution_state, "
                    "rl.token_cashflow, rl.evidence_artifact_id, rl.policy_code_hash, "
                    "rl.auditor_identity, rl.conflict_set, rl.supersedes_id, "
                    "cs.contract_key, cs.version_no AS contract_version_no, "
                    "cs.content_hash AS contract_content_hash, ao.sha256 AS label_artifact_hash, "
                    "NOT EXISTS (SELECT 1 FROM trading.resolution_labels newer "
                    " WHERE newer.supersedes_id=rl.id) AS is_latest "
                    "FROM trading.resolution_labels rl "
                    "JOIN trading.contract_specs cs ON cs.id=rl.contract_spec_id "
                    "JOIN trading.artifact_objects ao ON ao.id=rl.evidence_artifact_id "
                    "JOIN trading.contract_snapshots snap ON snap.id=cs.snapshot_id "
                    "JOIN trading.pm_market_versions mv ON mv.id=snap.market_version_id "
                    "WHERE rl.id=:label AND mv.market_id=:market"
                ),
                {"label": evidence.label_id, "market": evidence.market_id},
            )
        ).mappings().one_or_none()
        if (
            label is None
            or int(label["version_no"]) != evidence.label_version_no
            or label["state"] != "final_admissible"
            or label["resolution_state"] != evidence.label_resolution_state
            or not label["is_latest"]
            or label["evidence_artifact_id"] is None
            or not label["policy_code_hash"]
            or not label["auditor_identity"]
            or label["conflict_set"] not in (None, [])
        ):
            raise RuntimeError("settlement_evidence_label_mismatch")
        if not verify_payout_consistency(
            ctf_payout_outcome=evidence.ctf_outcome_index,
            ctf_numerator=evidence.ctf_numerator,
            ctf_denominator=evidence.ctf_denominator,
            clob_winner=evidence.clob_winner,
            clob_is_50_50=evidence.clob_is_50_50,
        ):
            raise RuntimeError("settlement_evidence_payout_conflict")
        expected_vector = self._payout_numerators(
            outcome=evidence.ctf_outcome_index,
            numerator=evidence.ctf_numerator,
            denominator=evidence.ctf_denominator,
        )
        if evidence.ctf_payout_numerators != expected_vector:
            raise RuntimeError("settlement_evidence_payout_vector_conflict")
        # WP-04's audited token_cashflow is the label authority.  It is keyed by
        # internal pm_token_id and must equal the full ordered CTF payout vector.
        token_cashflow = label["token_cashflow"]
        if not isinstance(token_cashflow, dict):
            raise RuntimeError("settlement_evidence_label_cashflow_missing")
        payout_rates = self._normalized_payout_rates(
            evidence.ctf_payout_numerators, evidence.ctf_denominator
        )
        expected_cashflow = {
            str(row["id"]): value for row, value in zip(tokens, payout_rates)
        }
        try:
            actual_cashflow = {
                str(key): _decimal(value, f"label_cashflow_{key}")
                for key, value in token_cashflow.items()
            }
        except Exception as exc:
            raise RuntimeError("settlement_evidence_label_cashflow_invalid") from exc
        if actual_cashflow != expected_cashflow:
            raise RuntimeError("settlement_evidence_label_cashflow_conflict")
        if evidence.data_api_redeemable is not True:
            raise RuntimeError("settlement_evidence_not_redeemable")

        artifact_rows = (
            await uow.session.execute(
                text(
                    "SELECT id, sha256 FROM trading.artifact_objects "
                    "WHERE id=ANY(:ids) FOR SHARE"
                ),
                {"ids": [item.artifact_id for item in evidence.artifacts.values()]},
            )
        ).mappings().all()
        catalog = {int(row["id"]): row["sha256"] for row in artifact_rows}
        if len(catalog) != len(evidence.artifacts):
            raise RuntimeError("settlement_evidence_artifact_catalog_incomplete")
        provenance: dict[str, dict[str, Any]] = {}
        for kind in sorted(SETTLEMENT_SOURCE_KINDS):
            artifact = evidence.artifacts[kind]
            if catalog.get(artifact.artifact_id) != artifact.artifact_hash:
                raise RuntimeError(f"settlement_evidence_artifact_catalog_mismatch:{kind}")
            provenance[kind] = {
                "artifact_ref": artifact.artifact_ref,
                "artifact_hash": artifact.artifact_hash,
                "source_version": artifact.source_version,
                "source_cutoff": artifact.source_cutoff,
            }
        set_material = {
            "schema": "settlement-source-set/v2",
            "market": {
                "gamma_market_id": market["gamma_market_id"],
                "condition_id": evidence.condition_id.lower(),
                "content_hash": market["content_hash"],
                "version_no": market["market_version_no"],
                "version_hash": market["market_version_hash"],
            },
            "condition_id": evidence.condition_id.lower(),
            "token_set": db_token_set,
            "cutoff_at": evidence.cutoff_at,
            "label": {
                "contract_key": label["contract_key"],
                "contract_version_no": label["contract_version_no"],
                "contract_content_hash": label["contract_content_hash"],
                "label_key": label["label_key"],
                "version_no": label["version_no"],
                "resolution_state": label["resolution_state"],
                "policy_code_hash": label["policy_code_hash"],
                "auditor_identity": label["auditor_identity"],
                "evidence_artifact_hash": label["label_artifact_hash"],
            },
            "source_provenance": provenance,
        }
        settlement_set_key = canonical_hash(set_material)
        source_rows = {
            "gamma_clob_closed": {
                "payload": {"closed": True, "accepting_orders": False},
            },
            "ctf_payout": {
                "outcome_index": evidence.ctf_outcome_index,
                "numerator": evidence.ctf_numerator,
                "denominator": evidence.ctf_denominator,
                "payout_vector": {
                    "numerators": evidence.ctf_payout_numerators,
                    "denominator": evidence.ctf_denominator,
                },
            },
            "data_api_redeemable": {"redeemable": True},
            "clob_winner_5050": {
                "winner": evidence.clob_winner,
                "is_50_50_outcome": evidence.clob_is_50_50,
            },
            "label_audit": {
                "label_audit_version": str(evidence.label_version_no),
                "payload": {
                    "status": "final_admissible",
                    "resolution_state": evidence.label_resolution_state,
                    "label_key": label["label_key"],
                    "contract_key": label["contract_key"],
                    "token_cashflow_rates": [str(value) for value in payout_rates],
                },
            },
        }
        for kind in SETTLEMENT_SOURCE_KINDS:
            artifact = evidence.artifacts[kind]
            fields = source_rows[kind]
            content = {
                "settlement_set_key": settlement_set_key,
                "source_kind": kind,
                "condition_id": evidence.condition_id.lower(),
                "market_id": evidence.market_id,
                "token_set": db_token_set,
                "as_of": evidence.cutoff_at,
                "artifact_hash": artifact.artifact_hash,
                "source_provenance": provenance[kind],
                **fields,
            }
            await self.observations.insert_observation(
                uow.session,
                {
                    "observation_key": f"{settlement_set_key}:{kind}",
                    "settlement_set_key": settlement_set_key,
                    "source_kind": kind,
                    "condition_id": evidence.condition_id.lower(),
                    "market_id": evidence.market_id,
                    "token_set": list(db_token_set),
                    "token_set_hash": canonical_hash(list(db_token_set)),
                    "outcome_index": fields.get("outcome_index"),
                    "numerator": fields.get("numerator"),
                    "denominator": fields.get("denominator"),
                    "payout_vector": fields.get("payout_vector"),
                    "winner": fields.get("winner"),
                    "is_50_50_outcome": fields.get("is_50_50_outcome"),
                    "redeemable": fields.get("redeemable"),
                    "label_audit_version": fields.get("label_audit_version"),
                    "source_version": artifact.source_version,
                    "source_cutoff": artifact.source_cutoff,
                    "as_of": evidence.cutoff_at,
                    "received_at": evidence.received_at,
                    "raw_artifact_ref": artifact.artifact_ref,
                    "raw_artifact_id": artifact.artifact_id,
                    "raw_artifact_hash": artifact.artifact_hash,
                    "content_hash": canonical_hash(content),
                    "payload": fields.get("payload"),
                    "status": "COMPLETE",
                },
            )
        return settlement_set_key

    @staticmethod
    def _payout_numerators(
        *, outcome: str, numerator: str, denominator: str
    ) -> list[str]:
        if denominator == "2" and numerator == "1" and outcome in {"YES", "NO", "50_50"}:
            return ["1", "1"]
        if outcome == "YES":
            return [numerator, "0"]
        if outcome == "NO":
            return ["0", numerator]
        raise RuntimeError("settlement_payout_vector_unsupported")

    @staticmethod
    def _normalized_payout_rates(
        numerators: list[str] | tuple[str, ...], denominator: str
    ) -> tuple[Decimal, Decimal]:
        try:
            den = Decimal(str(denominator))
            nums = tuple(Decimal(str(value)) for value in numerators)
        except Exception as exc:
            raise RuntimeError("settlement_payout_vector_invalid") from exc
        if len(nums) != 2 or den <= 0 or any(value < 0 for value in nums):
            raise RuntimeError("settlement_payout_vector_invalid")
        if sum(nums, Decimal(0)) != den:
            raise RuntimeError("settlement_payout_vector_not_normalized")
        return (nums[0] / den, nums[1] / den)

    # ---------------- authoritative preflight / TX1 ----------------

    async def preflight_redeem(
        self,
        uow: UnitOfWork,
        *,
        request: ChainRedeemRequest,
        runtime_identity: str,
        expected_registry_content_hash: str,
    ) -> RedeemPreflight:
        if not runtime_identity.strip():
            raise RuntimeError("chain_runtime_identity_required")
        kind = await self._registry_kind_for_market(uow, request.market_id)
        loader = getattr(self.chain_operations, "load_preflight_context", None)
        if not callable(loader):
            raise RuntimeError("chain_preflight_repository_capability_missing")
        material = await loader(
            uow.session,
            account_id=request.account_id,
            market_id=request.market_id,
            registry_kind=kind,
            registry_content_hash=expected_registry_content_hash,
            release_manifest_id=None,
            capital_permission_manifest_id=None,
            lease_owner=runtime_identity,
            fencing_token=request.fencing_token,
            for_update=True,
        )
        if material is None:
            raise RuntimeError("chain_preflight_context_missing")
        exact_checks = {
            "account_id": request.account_id,
            "account_status": "active",
            "account_provider": "polymarket",
            "account_chain_id": self.chain_id,
            "account_identity_type": "FIXTURE_ONLY",
            "account_network_mode": "fixture",
            "market_id": request.market_id,
            "market_condition_id": request.condition_id,
            "release_status": "active",
            "permission_status": "active",
            "permission_mode": "shadow",
            "registry_status": "ACTIVE",
            "registry_version": self.registry_version,
            "registry_kind": kind,
            "registry_content_hash": expected_registry_content_hash,
            "lease_owner": runtime_identity,
            "lease_fencing_token": request.fencing_token,
        }
        for field, expected in exact_checks.items():
            actual = material.get(field)
            if field == "market_condition_id" and isinstance(actual, str):
                actual, expected = actual.lower(), str(expected).lower()
            if actual != expected:
                raise RuntimeError(f"chain_preflight_mismatch:{field}")
        if material["lease_until"] <= _utcnow():
            raise RuntimeError("chain_preflight_lease_expired")
        if material["funder_address"] != material["maker_address"]:
            raise RuntimeError("chain_preflight_wallet_identity_mismatch")
        if not material.get("signing_identity") or material["signing_identity"] == material["maker_address"]:
            raise RuntimeError("chain_preflight_signer_identity_invalid")
        if material["account_release_manifest_id"] != material["release_manifest_id"]:
            raise RuntimeError("chain_preflight_release_lineage_mismatch")
        if (
            material["account_capital_permission_manifest_id"]
            != material["capital_permission_manifest_id"]
            or material["release_capital_permission_manifest_id"]
            != material["capital_permission_manifest_id"]
        ):
            raise RuntimeError("chain_preflight_permission_lineage_mismatch")
        if Decimal(str(material["permission_authorized_capital"])) != 0:
            raise RuntimeError("chain_preflight_nonzero_capital_forbidden")
        if material["permission_kill_switch"] is True:
            raise RuntimeError("chain_preflight_kill_switch")
        if material["active_reconciliation"] is True:
            raise RuntimeError("chain_preflight_reconciliation_active")
        if material["market_neg_risk"] is None:
            raise RuntimeError("chain_preflight_neg_risk_missing")
        if material["market_closed"] is not True or material["market_accepting_orders"] is not False:
            raise RuntimeError("chain_preflight_market_not_closed")
        capability = material.get("permission_capability") or {}
        if (
            not isinstance(capability, dict)
            or capability.get("chain_settlement") != "FAKE_CONFORMANCE"
        ):
            raise RuntimeError("chain_preflight_fake_capability_missing")

        market_identity = (
            await uow.session.execute(
                text(
                    "SELECT m.gamma_market_id, m.condition_id, m.content_hash, "
                    "mv.version_no, mv.normalized_hash "
                    "FROM trading.pm_markets m JOIN LATERAL ("
                    " SELECT version_no, normalized_hash FROM trading.pm_market_versions "
                    " WHERE market_id=m.id ORDER BY version_no DESC LIMIT 1"
                    ") mv ON true WHERE m.id=:market FOR SHARE OF m"
                ),
                {"market": request.market_id},
            )
        ).mappings().one_or_none()
        if market_identity is None or any(
            market_identity[field] is None
            for field in ("gamma_market_id", "condition_id", "content_hash", "normalized_hash")
        ):
            raise RuntimeError("chain_preflight_market_identity_incomplete")

        raw_bundle_entries = material.get("registry_bundle_entries") or {}
        if not isinstance(raw_bundle_entries, dict):
            raise RuntimeError("chain_preflight_registry_bundle_incomplete")
        registry_bundle = [
            {"kind": bundle_kind, "chain_id": self.chain_id, "status": "ACTIVE", **dict(entry)}
            for bundle_kind, entry in sorted(raw_bundle_entries.items())
        ]
        required_bundle = {"pusd", "ctf", "deposit_wallet", kind}
        if {row["kind"] for row in registry_bundle} != required_bundle:
            raise RuntimeError("chain_preflight_registry_bundle_incomplete")
        by_kind = {str(row["kind"]): row for row in registry_bundle}
        if by_kind[kind]["id"] != material["registry_version_id"]:
            raise RuntimeError("chain_preflight_registry_adapter_drift")
        registry_bundle_material = [
            {
                key: row.get(key)
                for key in (
                    "kind", "registry_version", "version_no", "chain_id", "address",
                    "proxy_kind", "runtime_keccak", "resolved_implementation_or_beacon",
                    "resolved_code_keccak", "snapshot_block_number", "snapshot_block_hash",
                    "content_hash", "extra",
                )
            }
            for row in registry_bundle
        ]
        registry_bundle_hash = str(material["registry_bundle_content_hash"])
        if material.get("registry_bundle") != {
            str(row["kind"]): str(row["content_hash"]) for row in registry_bundle
        }:
            raise RuntimeError("chain_preflight_registry_bundle_identity_mismatch")

        assessment = await self.assess_settlement(
            uow, request.condition_id, market_id=request.market_id
        )
        if not assessment.admissible:
            raise RuntimeError(f"settlement_not_admissible:{assessment.conflict_reason}")
        calldata = build_redeem_calldata(
            collateral_address=by_kind["pusd"]["address"], condition_id=request.condition_id,
            parent_collection_id=self.parent_collection_id, partition=list(self.partition),
        )
        calls = ({"target": material["registry_address"], "value": "0", "data": calldata},)
        authority = {
            "schema": "chain-redeem-preflight/v2",
            "runtime_identity": runtime_identity,
            "request": {
                "operation_key": request.operation_key,
                "idempotency_key": request.idempotency_key,
                "condition_id": request.condition_id.lower(),
                "fencing_token": request.fencing_token,
            },
            "account": {
                "account_key": material["account_key"],
                "wallet_address": material["funder_address"],
                "signing_identity": material["signing_identity"],
            },
            "release": {
                "release_name": material["release_name"],
                "total_hash": material["release_total_hash"],
                "db_revision": material["release_db_revision"],
            },
            "permission": {
                "name": material["permission_name"],
                "content_hash": material["permission_content_hash"],
                "capability": capability,
                "authorized_capital": str(material["permission_authorized_capital"]),
                "kill_switch": material["permission_kill_switch"],
            },
            "market": {
                "gamma_market_id": market_identity["gamma_market_id"],
                "condition_id": material["market_condition_id"],
                "content_hash": market_identity["content_hash"],
                "version_no": market_identity["version_no"],
                "version_hash": market_identity["normalized_hash"],
                "neg_risk": material["market_neg_risk"],
                "token_set": assessment.token_set,
                "settlement_cutoff": assessment.as_of,
            },
            "registry_bundle": registry_bundle_material,
            "registry_bundle_hash": registry_bundle_hash,
            "calls": calls,
        }
        return RedeemPreflight(
            request=request, authority_hash=canonical_hash(authority),
            account_key=material["account_key"], wallet_address=material["funder_address"],
            signing_identity=material["signing_identity"],
            registry_id=int(material["registry_version_id"]),
            registry_kind=material["registry_kind"],
            registry_version=material["registry_version"],
            registry_content_hash=material["registry_content_hash"],
            registry_address=material["registry_address"],
            registry_snapshot_block_number=int(material["registry_snapshot_block_number"]),
            registry_runtime_keccak=material["registry_runtime_keccak"],
            registry_resolved_address=material["registry_resolved_implementation_or_beacon"],
            registry_resolved_code_keccak=material["registry_resolved_code_keccak"],
            registry_proxy_kind=material["registry_proxy_kind"],
            registry_snapshot_block_hash=material["registry_snapshot_block_hash"],
            registry_extra=dict(material.get("registry_extra") or {}),
            registry_bundle=tuple(dict(row) for row in registry_bundle),
            registry_bundle_hash=registry_bundle_hash,
            pusd_address=by_kind["pusd"]["address"],
            ctf_address=by_kind["ctf"]["address"],
            deposit_wallet_address=by_kind["deposit_wallet"]["address"],
            release_manifest_id=int(material["release_manifest_id"]),
            release_name=material["release_name"],
            release_total_hash=material["release_total_hash"],
            permission_manifest_id=int(material["capital_permission_manifest_id"]),
            permission_ref=material["permission_content_hash"],
            market_key=market_identity["gamma_market_id"],
            market_content_hash=market_identity["content_hash"],
            market_version_no=int(market_identity["version_no"]),
            market_version_hash=market_identity["normalized_hash"],
            token_set=assessment.token_set,
            settlement_set_key=str(assessment.settlement_set_key),
            calldata=calldata, calls=calls,
        )

    async def _load_registry_bundle(
        self,
        uow: UnitOfWork,
        *,
        registry_version: str,
        adapter_kind: str,
    ) -> list[dict[str, Any]]:
        required = ("pusd", "ctf", "deposit_wallet", adapter_kind)
        rows = [dict(row) for row in (
            await uow.session.execute(
                text(
                    "SELECT id, registry_version, kind, version_no, chain_id, address, "
                    "proxy_kind, runtime_keccak, resolved_implementation_or_beacon, "
                    "resolved_code_keccak, snapshot_block_number, snapshot_block_hash, "
                    "content_hash, extra, status FROM trading.contract_registry "
                    "WHERE chain_id=:chain AND registry_version=:version "
                    "AND kind=ANY(:kinds) AND status='ACTIVE' ORDER BY kind FOR SHARE"
                ),
                {"chain": self.chain_id, "version": registry_version, "kinds": list(required)},
            )
        ).mappings().all()]
        if len(rows) != len(required) or {row["kind"] for row in rows} != set(required):
            raise RuntimeError("chain_preflight_registry_bundle_incomplete")
        snapshots = {
            (row["snapshot_block_number"], row["snapshot_block_hash"]) for row in rows
        }
        if len(snapshots) != 1:
            raise RuntimeError("chain_preflight_registry_bundle_snapshot_conflict")
        return rows

    async def _registry_kind_for_market(self, uow: UnitOfWork, market_id: int) -> str:
        value = (await uow.session.execute(
            text("SELECT neg_risk FROM trading.pm_markets WHERE id=:market"),
            {"market": market_id},
        )).scalar_one_or_none()
        if value is None:
            raise RuntimeError("chain_preflight_neg_risk_missing")
        return "neg_risk_adapter" if bool(value) else "ctf_adapter_standard"

    async def prepare_redeem(
        self,
        uow: UnitOfWork,
        *,
        request: ChainRedeemRequest,
        runtime_identity: str,
        expected_registry_content_hash: str,
        first_preflight_hash: str,
        wire: ChainWireEvidence,
    ) -> PreparedOperation:
        current = await self.preflight_redeem(
            uow,
            request=request,
            runtime_identity=runtime_identity,
            expected_registry_content_hash=expected_registry_content_hash,
        )
        if current.authority_hash != first_preflight_hash:
            raise RuntimeError("chain_preflight_authority_drift")
        expected_call_set_hash = canonical_hash(list(current.calls))
        if wire.call_set_hash != expected_call_set_hash:
            raise RuntimeError("chain_wire_call_set_mismatch")
        if wire.registry_content_hash != current.registry_content_hash:
            raise RuntimeError("chain_registry_content_drift")
        expected_bundle = {str(row["kind"]): str(row["content_hash"]) for row in current.registry_bundle}
        if wire.registry_bundle != expected_bundle:
            raise RuntimeError("chain_registry_bundle_drift")
        if wire.registry_bundle_content_hash != current.registry_bundle_hash:
            raise RuntimeError("chain_registry_bundle_hash_drift")
        artifact = (
            await uow.session.execute(
                text("SELECT sha256 FROM trading.artifact_objects WHERE id=:id FOR SHARE"),
                {"id": wire.registry_evidence_artifact_id},
            )
        ).scalar_one_or_none()
        if artifact != wire.registry_evidence_hash:
            raise RuntimeError("chain_registry_evidence_artifact_mismatch")
        geo_artifact = (
            await uow.session.execute(
                text("SELECT sha256 FROM trading.artifact_objects WHERE id=:id FOR SHARE"),
                {"id": wire.geo_evidence_artifact_id},
            )
        ).scalar_one_or_none()
        if geo_artifact != wire.geo_evidence_hash:
            raise RuntimeError("chain_geo_evidence_artifact_mismatch")
        if wire.geo_allowed is not True:
            raise RuntimeError("chain_geoblock_denied")
        now = _utcnow()
        if wire.geo_observed_at > now or (now - wire.geo_observed_at).total_seconds() > 30:
            raise RuntimeError("chain_geo_evidence_stale")
        if wire.settlement_set_key != current.settlement_set_key:
            raise RuntimeError("chain_settlement_set_drift")
        binding = {
            "schema": "chain-operation-binding/v2",
            "operation_type": "REDEEM",
            "account_key": current.account_key,
            "wallet_address": current.wallet_address,
            "condition_id": request.condition_id,
            "market_key": current.market_key,
            "market_content_hash": current.market_content_hash,
            "market_version_no": current.market_version_no,
            "market_version_hash": current.market_version_hash,
            "registry_version": current.registry_version,
            "registry_content_hash": current.registry_content_hash,
            "registry_bundle_hash": current.registry_bundle_hash,
            "target_address": current.registry_address,
            "permission_ref": current.permission_ref,
            "release_name": current.release_name,
            "release_total_hash": current.release_total_hash,
            "fencing_token": request.fencing_token,
            "token_set": current.token_set,
        }
        economic_hash = canonical_hash({
            key: binding[key]
            for key in (
                "operation_type", "account_key", "wallet_address", "condition_id",
                "market_key", "market_content_hash", "market_version_no",
                "market_version_hash", "target_address", "token_set",
            )
        })
        allocation = await self._derive_settlement_allocation(
            uow, account_id=request.account_id, market_id=request.market_id,
            pre_balance=wire.pre_balance, settlement_set_key=current.settlement_set_key,
        )
        allocation_hash = canonical_hash(allocation)
        expected_operation_hash = canonical_hash({
            **binding,
            "calls": current.calls,
            "settlement_set_key": wire.settlement_set_key,
            "settlement_allocation_hash": allocation_hash,
        })
        claimed = await self.chain_operations.claim_idempotency(
            uow.session, key=request.idempotency_key, owner=expected_operation_hash
        )
        if not claimed:
            existing = await self.chain_operations.get_by_key(
                uow.session, request.operation_key
            )
            if existing and existing.get("expected_operation_hash") == expected_operation_hash:
                return self._prepared_from_row(existing, transport_owner=False)
            raise RuntimeError("redeem_idempotency_conflict")

        from eth_utils import keccak as _keccak
        calldata_keccak = _keccak(bytes.fromhex(current.calldata[2:])).hex()
        op_id = await self.chain_operations.insert_operation(
            uow.session,
            {
                "operation_key": request.operation_key,
                "idempotency_key": request.idempotency_key,
                "economic_hash": economic_hash,
                "operation_type": "REDEEM",
                "chain_id": self.chain_id,
                "account_id": request.account_id,
                "wallet_address": current.wallet_address,
                "condition_id": request.condition_id,
                "market_id": request.market_id,
                "registry_version_id": current.registry_id,
                "target_address": current.registry_address,
                "permission_ref": current.permission_ref,
                "release_manifest_id": current.release_manifest_id,
                "capital_permission_manifest_id": current.permission_manifest_id,
                "fencing_token": request.fencing_token,
                "lease_owner": runtime_identity,
                "amount_base_units": 0,
                "calldata": current.calldata,
                "calldata_keccak": calldata_keccak,
                "body_hash": wire.body_hash,
                "call_set_hash": wire.call_set_hash,
                "expected_operation_hash": expected_operation_hash,
                "preflight_hash1": first_preflight_hash,
                "preflight_hash2": current.authority_hash,
                "registry_evidence_hash": wire.registry_evidence_hash,
                "registry_content_hash": wire.registry_content_hash,
                "registry_bundle": wire.registry_bundle,
                "registry_bundle_content_hash": wire.registry_bundle_content_hash,
                "registry_evidence_artifact_id": wire.registry_evidence_artifact_id,
                "geo_evidence_artifact_id": wire.geo_evidence_artifact_id,
                "geo_evidence_hash": wire.geo_evidence_hash,
                "geo_allowed": wire.geo_allowed,
                "geo_observed_at": wire.geo_observed_at,
                "geo_source_version": wire.geo_source_version,
                "settlement_set_key": wire.settlement_set_key,
                "settlement_allocation": allocation,
                "settlement_allocation_hash": allocation_hash,
                "pre_balance": wire.pre_balance,
            },
        )
        await self.chain_operations.update_evidence(
            uow.session,
            op_id,
            {
                "relayer_nonce": wire.nonce,
                "deadline": wire.deadline,
            },
            lease_owner=runtime_identity,
            fencing_token=request.fencing_token,
        )
        await self._append_state(
            uow,
            operation_id=op_id,
            from_status="PREPARED",
            to_status="SUBMITTING",
            runtime_identity=runtime_identity,
            fencing_token=request.fencing_token,
            event_type="WIRE_COMMITTED",
            payload={
                "body_hash": wire.body_hash,
                "call_set_hash": wire.call_set_hash,
                "nonce": wire.nonce,
                "deadline": wire.deadline.isoformat(),
            },
        )
        return PreparedOperation(
            operation_id=op_id,
            operation_key=request.operation_key,
            account_id=request.account_id,
            fencing_token=request.fencing_token,
            economic_hash=economic_hash,
            expected_operation_hash=expected_operation_hash,
            calldata=current.calldata,
            body_hash=wire.body_hash,
            nonce=wire.nonce,
            deadline=wire.deadline,
            transport_owner=True,
        )

    async def _derive_settlement_allocation(
        self,
        uow: UnitOfWork,
        *,
        account_id: int,
        market_id: int,
        pre_balance: dict[str, object],
        settlement_set_key: str,
    ) -> list[dict[str, str]]:
        tokens = pre_balance.get("tokens")
        if not isinstance(tokens, dict):
            raise RuntimeError("chain_pre_balance_token_set_missing")
        expected = {str(key).lower(): Decimal(str(value)) for key, value in tokens.items()}
        if len(expected) != 2 or any(value < 0 for value in expected.values()):
            raise RuntimeError("chain_pre_balance_token_set_invalid")
        rows = (await uow.session.execute(
            text(
                "SELECT p.portfolio_namespace, t.token_id, p.quantity "
                "FROM trading.positions p JOIN trading.pm_tokens t ON t.id=p.token_id "
                "WHERE p.account_id=:account AND p.market_id=:market AND p.quantity>0 "
                "ORDER BY p.portfolio_namespace, t.outcome_index, p.id FOR UPDATE OF p"
            ),
            {"account": account_id, "market": market_id},
        )).mappings().all()
        aggregated: dict[tuple[str, str], Decimal] = {}
        for row in rows:
            key = (str(row["portfolio_namespace"]), str(row["token_id"]).lower())
            aggregated[key] = aggregated.get(key, Decimal(0)) + Decimal(str(row["quantity"]))
        payout = (
            await uow.session.execute(
                text(
                    "SELECT token_set,payout_vector FROM trading.settlement_observations "
                    "WHERE settlement_set_key=:key AND source_kind='ctf_payout' "
                    "AND status='COMPLETE' FOR SHARE"
                ),
                {"key": settlement_set_key},
            )
        ).mappings().one_or_none()
        if payout is None:
            raise RuntimeError("chain_settlement_payout_missing")
        ordered_tokens = tuple(str(token).lower() for token in payout["token_set"])
        vector = payout["payout_vector"] or {}
        denominator = self._base_units(vector.get("denominator"), "payout_denominator")
        numerators = vector.get("numerators")
        if not isinstance(numerators, list) or len(numerators) != 2 or denominator <= 0:
            raise RuntimeError("chain_settlement_payout_vector_invalid")
        payout_by_token = {
            token: self._base_units(numerators[index], "payout_numerator")
            for index, token in enumerate(ordered_tokens)
        }
        allocation = []
        for (namespace, token), quantity_decimal in sorted(aggregated.items()):
            if quantity_decimal <= 0 or quantity_decimal != quantity_decimal.to_integral_value():
                continue
            quantity = int(quantity_decimal)
            if token not in payout_by_token:
                raise RuntimeError("chain_settlement_allocation_token_unknown")
            product = quantity * payout_by_token[token]
            if product % denominator:
                raise RuntimeError("chain_settlement_fractional_base_unit_allocation")
            allocation.append({
                "portfolio_namespace": namespace,
                "token_id": token,
                "quantity_base_units": str(quantity),
                "expected_cash_base_units": str(product // denominator),
            })
        actual = {token: Decimal(0) for token in expected}
        for row in allocation:
            token = row["token_id"]
            if token not in actual:
                raise RuntimeError("chain_pre_balance_position_token_set_conflict")
            actual[token] += Decimal(row["quantity_base_units"])
        # Both outcome tokens are always part of the authoritative balance vector;
        # the losing leg commonly has a zero wallet balance and therefore no position.
        if actual != expected or not allocation:
            raise RuntimeError("chain_pre_balance_position_allocation_conflict")
        return allocation

    # ---------------- TX2 and recovery application ----------------

    async def apply_submit_outcome(
        self,
        uow: UnitOfWork,
        *,
        operation_id: int,
        runtime_identity: str,
        fencing_token: int,
        outcome: Any,
        sent_body_hash: str,
    ) -> dict[str, Any]:
        op = await self._load_owned_operation(
            uow, operation_id, runtime_identity=runtime_identity,
            fencing_token=fencing_token,
        )
        if sent_body_hash != op["body_hash"]:
            raise RuntimeError("chain_submit_body_hash_mismatch")
        if op["status"] != "SUBMITTING":
            if op["status"] in CHAIN_OPERATION_ACTIVE_STATES:
                return {"status": op["status"], "replayed": True}
            raise RuntimeError("chain_submit_operation_not_submitting")
        cls = str(getattr(outcome, "cls", "UNKNOWN") or "UNKNOWN")
        transaction_id = getattr(outcome, "transaction_id", None)
        transaction_hash = getattr(outcome, "transaction_hash", None)
        relayer_state = str(getattr(outcome, "state", "") or "")
        evidence_updates: dict[str, Any] = {}
        if transaction_id is not None:
            evidence_updates["transaction_id"] = str(transaction_id)
        if transaction_hash is not None:
            evidence_updates["transaction_hash"] = str(transaction_hash).lower()
        if evidence_updates:
            await self.chain_operations.update_evidence(
                uow.session, operation_id, evidence_updates,
                lease_owner=runtime_identity, fencing_token=fencing_token,
            )
        if cls != "SUBMITTED" or relayer_state not in {"NEW", "EXECUTED"}:
            target = "UNKNOWN"
        else:
            target = "RELAYER_NEW" if relayer_state == "NEW" else "EXECUTED"
        await self._append_state(
            uow,
            operation_id=operation_id,
            from_status="SUBMITTING",
            to_status=target,
            runtime_identity=runtime_identity,
            fencing_token=fencing_token,
            event_type="RELAYER_SUBMIT_OUTCOME",
            payload={
                "class": cls,
                "state": relayer_state or None,
                "transaction_id": str(transaction_id) if transaction_id else None,
                "transaction_hash": str(transaction_hash).lower() if transaction_hash else None,
                "http_status": getattr(outcome, "http_status", None),
                "body_hash": sent_body_hash,
            },
        )
        return {"status": target, "replayed": False}

    async def recover_unknown(
        self,
        uow: UnitOfWork,
        operation_id: int,
        *,
        runtime_identity: str,
        fencing_token: int,
        allow_finalized_audit: bool = False,
    ) -> dict[str, Any]:
        """Return a provider query plan only.  It never claims recovery occurred."""
        op = await self.chain_operations.load_recovery_context(
            uow.session,
            operation_id=operation_id,
            lease_owner=runtime_identity,
            fencing_token=fencing_token,
            for_update=False,
        )
        if op is None:
            raise RuntimeError("chain_recovery_fence_or_operation_missing")
        if op["status"] == "FINALIZED" and allow_finalized_audit:
            pass
        elif op["status"] not in CHAIN_OPERATION_ACTIVE_STATES:
            raise RuntimeError("chain_operation_not_recoverable")
        if not op.get("signing_identity"):
            raise RuntimeError("chain_recovery_signing_identity_missing")
        return {
            "operation_id": operation_id,
            "account_id": op["account_id"],
            "wallet_address": op["wallet_address"],
            "signing_identity": op["signing_identity"],
            "condition_id": op["condition_id"],
            "market_id": op["market_id"],
            "target_address": op["target_address"],
            "status": op["status"],
            "transaction_id": op.get("transaction_id"),
            "transaction_hash": op.get("transaction_hash"),
            "nonce": op.get("relayer_nonce"),
            "body_hash": op["body_hash"],
            "operation_key": op["operation_key"],
            "idempotency_key": op["idempotency_key"],
            "registry_version_id": op["registry_version_id"],
            "registry_content_hash": op.get("registry_content_hash"),
            "registry_bundle": op.get("registry_bundle"),
            "registry_bundle_content_hash": op.get("registry_bundle_content_hash"),
            "registry_evidence_hash": op["registry_evidence_hash"],
            "blind_resend": False,
            "required_queries": (
                "relayer_transaction", "relayer_nonce", "polygon_receipt",
                "canonical_block", "finalized_block", "post_balances",
            ),
        }

    async def apply_recovery(
        self,
        uow: UnitOfWork,
        *,
        operation_id: int,
        runtime_identity: str,
        fencing_token: int,
        evidence: ChainRecoveryEvidence,
    ) -> dict[str, Any]:
        op = await self._load_owned_operation(
            uow, operation_id, runtime_identity=runtime_identity,
            fencing_token=fencing_token,
        )
        status = op["status"]
        if status == "FINALIZED":
            contradictions: list[str] = []
            # A periodic audit is affirmative proof of the *same* finalized
            # transaction, not a best-effort heartbeat.  Missing receipt/status
            # facts are contradictions too: otherwise a provider returning no
            # receipt (or INVALID) could silently replay a finalized operation.
            if evidence.relayer_state != "CONFIRMED":
                contradictions.append("relayer_state")
            if evidence.transaction_id != op.get("transaction_id"):
                contradictions.append("transaction_id")
            if not evidence.transaction_hash or str(evidence.transaction_hash).lower() != str(
                op.get("transaction_hash") or ""
            ).lower():
                contradictions.append("transaction_hash")
            if evidence.receipt_removed or evidence.canonical is not True:
                contradictions.append("canonical_receipt")
            if evidence.receipt_success is not True:
                contradictions.append("receipt_status")
            if evidence.receipt_block_number != op.get("receipt_block_number"):
                contradictions.append("receipt_block_number")
            if str(evidence.receipt_block_hash or "").lower() != str(
                op.get("receipt_block_hash") or ""
            ).lower():
                contradictions.append("receipt_block_hash")
            if str(evidence.canonical_block_hash or "").lower() != str(
                op.get("canonical_block_hash") or ""
            ).lower():
                contradictions.append("canonical_block_hash")
            if (
                evidence.finalized_after_receipt is not True
                or evidence.finalized_block_number is None
                or evidence.finalized_block_hash is None
            ):
                contradictions.append("finalized_proof")
            elif op.get("finalized_block_number") is not None:
                frozen_finalized = int(op["finalized_block_number"])
                if evidence.finalized_block_number < frozen_finalized:
                    contradictions.append("finalized_block_regression")
                elif (
                    evidence.finalized_block_number == frozen_finalized
                    and str(evidence.finalized_block_hash).lower()
                    != str(op.get("finalized_block_hash") or "").lower()
                ):
                    contradictions.append("finalized_block_hash")
            if evidence.post_balance is None or evidence.post_balance != op.get("post_balance"):
                contradictions.append("post_balance")
            if evidence.balance_artifact_id is None or evidence.balance_artifact_hash is None:
                contradictions.append("balance_artifact")
            await self._record_recovery_observation(
                uow, operation_id=operation_id, op=op, evidence=evidence,
                status=status, contradictions=contradictions,
            )
            if not contradictions:
                return {"status": status, "replayed": True, "applied": False}
            await self._append_state(
                uow, operation_id=operation_id, from_status="FINALIZED",
                to_status="SETTLEMENT_CONFLICT", runtime_identity=runtime_identity,
                fencing_token=fencing_token, event_type="POST_FINALITY_CONTRADICTION",
                payload={"observation_hash": evidence.observation_hash,
                         "contradictions": contradictions},
            )
            await self.audit.insert_alert_event(
                uow.session,
                alert_key=f"chain-finality-conflict:{op['operation_key']}:{evidence.observation_hash}",
                severity="CRITICAL", code="CHAIN_FINALITY_CONTRADICTION",
                message_redacted="Frozen finalized chain evidence contradicted by provider audit",
            )
            return {"status": "SETTLEMENT_CONFLICT", "replayed": False, "applied": False}
        if status not in CHAIN_OPERATION_ACTIVE_STATES:
            raise RuntimeError("chain_operation_not_recoverable")
        if evidence.transaction_id and op.get("transaction_id") not in (None, evidence.transaction_id):
            raise RuntimeError("chain_recovery_transaction_id_conflict")
        if evidence.transaction_hash and op.get("transaction_hash") not in (None, evidence.transaction_hash):
            raise RuntimeError("chain_recovery_transaction_hash_conflict")
        if evidence.nonce is not None and op.get("relayer_nonce") is not None:
            if int(evidence.nonce) < int(op["relayer_nonce"]):
                raise RuntimeError("chain_recovery_nonce_regression")
        updates: dict[str, Any] = {}
        if evidence.transaction_id:
            updates["transaction_id"] = evidence.transaction_id
        if evidence.transaction_hash:
            updates["transaction_hash"] = evidence.transaction_hash.lower()
        if evidence.receipt_block_number is not None:
            updates["receipt_block_number"] = evidence.receipt_block_number
        if evidence.receipt_block_hash:
            updates["receipt_block_hash"] = evidence.receipt_block_hash.lower()
        if evidence.receipt_success is not None:
            updates["receipt_status"] = evidence.receipt_success
        if evidence.canonical_block_hash:
            updates["canonical_block_hash"] = evidence.canonical_block_hash.lower()
        # A finalized head at/before the receipt is useful provider evidence but
        # is not the operation's finality binding.  It remains in the immutable
        # recovery artifact; freeze these columns only once the head proves the
        # receipt is strictly finalized, allowing a later recovery pass to close.
        if evidence.finalized_after_receipt is True:
            if evidence.finalized_block_number is not None:
                updates["finalized_block_number"] = evidence.finalized_block_number
            if evidence.finalized_block_hash:
                updates["finalized_block_hash"] = evidence.finalized_block_hash.lower()
        if evidence.balance_artifact_hash:
            updates["balance_evidence_hash"] = evidence.balance_artifact_hash
        if evidence.balance_artifact_id:
            updates["balance_evidence_artifact_id"] = evidence.balance_artifact_id
        if evidence.post_balance is not None:
            updates["post_balance"] = evidence.post_balance
        if updates:
            await self.chain_operations.update_evidence(
                uow.session, operation_id, updates,
                lease_owner=runtime_identity, fencing_token=fencing_token,
            )
        await self._record_recovery_observation(
            uow, operation_id=operation_id, op=op, evidence=evidence,
            status=status, contradictions=[],
        )

        # Authoritative provider failure is terminal; a transport exception or absent
        # evidence remains UNKNOWN and never creates a new signed batch.
        if evidence.relayer_state in {"INVALID", "FAILED"}:
            target = evidence.relayer_state
            if self._transition_reachable(status, target):
                await self._append_state(
                    uow, operation_id=operation_id, from_status=status, to_status=target,
                    runtime_identity=runtime_identity, fencing_token=fencing_token,
                    event_type="RELAYER_TERMINAL_FAILURE",
                    payload=evidence.model_dump(mode="json", exclude_none=True),
                )
                return {"status": target, "replayed": False, "applied": False}
            return {"status": status, "replayed": True, "applied": False}

        # Advance through the declared state graph; one recovery observation may prove
        # several adjacent states but every step remains append-only and fenced.
        desired = self._desired_state(status, evidence)
        current = status
        for target in self._path_to(current, desired):
            if target == "FINALIZED":
                break
            await self._append_state(
                uow, operation_id=operation_id, from_status=current, to_status=target,
                runtime_identity=runtime_identity, fencing_token=fencing_token,
                event_type="RECOVERY_OBSERVATION",
                payload={"observation_hash": evidence.observation_hash, "proved": target},
            )
            current = target

        if desired != "FINALIZED":
            return {"status": current, "replayed": current == status, "applied": False}
        if not self._finality_evidence_exact(op, evidence):
            raise RuntimeError("chain_finality_evidence_conflict")
        # FINALIZED state, economic facts and the effect bit commit atomically.  The
        # deferred DB guard verifies the ledger/outbox/audit set at transaction end.
        await self._append_state(
            uow, operation_id=operation_id, from_status=current, to_status="FINALIZED",
            runtime_identity=runtime_identity, fencing_token=fencing_token,
            event_type="CANONICAL_FINALITY",
            payload={
                "observation_hash": evidence.observation_hash,
                "balance_artifact_hash": evidence.balance_artifact_hash,
            },
        )
        result = await self._apply_economic_effect(
            uow, operation_id=operation_id, op={**op, **updates}, evidence=evidence
        )
        marked = await self.chain_operations.mark_economic_effect_applied(
            uow.session, operation_id=operation_id,
            lease_owner=runtime_identity, fencing_token=fencing_token,
        )
        if not marked:
            raise RuntimeError("chain_economic_effect_mark_conflict")
        return {"status": "FINALIZED", "replayed": False, **result}

    async def apply_finality(
        self, uow: UnitOfWork, operation_id: int, *, winning_token_id: int | None = None
    ) -> dict[str, Any]:
        """Legacy direct application is closed: finality must arrive as provider evidence."""
        del uow, operation_id, winning_token_id
        raise RuntimeError("chain_finality_requires_recovery_evidence")

    async def _record_recovery_observation(
        self,
        uow: UnitOfWork,
        *,
        operation_id: int,
        op: dict[str, Any],
        evidence: ChainRecoveryEvidence,
        status: str,
        contradictions: list[str],
    ) -> None:
        """Link every provider observation, including non-final ones, to the op."""
        artifact_hash = (
            await uow.session.execute(
                text("SELECT sha256 FROM trading.artifact_objects WHERE id=:id FOR SHARE"),
                {"id": evidence.provider_artifact_id},
            )
        ).scalar_one_or_none()
        if artifact_hash != evidence.provider_artifact_hash:
            raise RuntimeError("chain_recovery_provider_artifact_mismatch")
        payload = {
            "operation_key": op["operation_key"],
            "status_before": status,
            "observation_hash": evidence.observation_hash,
            "provider_artifact_hash": evidence.provider_artifact_hash,
            "contradictions": sorted(contradictions),
        }
        await self.audit.insert_workflow_event(
            uow.session,
            event_key=f"chain-recovery:{op['operation_key']}:{canonical_hash(payload)}",
            event_type="CHAIN_RECOVERY_OBSERVATION",
            aggregate_type="chain_operation",
            aggregate_id=op["operation_key"],
            payload_hash=canonical_hash(payload),
            payload=payload,
        )

    # ---------------- internal invariants ----------------

    async def _load_owned_operation(
        self,
        uow: UnitOfWork,
        operation_id: int,
        *,
        runtime_identity: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        op = await self.chain_operations.load_recovery_context(
            uow.session,
            operation_id=operation_id,
            lease_owner=runtime_identity,
            fencing_token=fencing_token,
            for_update=True,
        )
        if op is None:
            raise RuntimeError("chain_stale_fence_rejected")
        return op

    async def _append_state(
        self,
        uow: UnitOfWork,
        *,
        operation_id: int,
        from_status: str,
        to_status: str,
        runtime_identity: str,
        fencing_token: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        from app.orchestrator.trading_state_machine import (
            assert_chain_operation_transition,
        )

        assert_chain_operation_transition(from_status, to_status)
        sequence = await self.chain_operations.next_sequence(uow.session, operation_id)
        operation_key = (
            await uow.session.execute(
                text("SELECT operation_key FROM trading.chain_operations WHERE id=:oid"),
                {"oid": operation_id},
            )
        ).scalar_one()
        stable_event_material = {
            "operation_key": operation_key,
            "sequence_no": sequence,
            "transition_from": from_status,
            "transition_to": to_status,
            # ``event_type`` is the canonical state proved by this append.  The
            # business reason belongs in evidence, never in the state vocabulary.
            "event_type": to_status,
            "event_payload": {"reason": event_type, **payload},
            "fence_token": fencing_token,
            "lease_owner": runtime_identity,
        }
        event_material = {"operation_id": operation_id, **stable_event_material}
        await self.chain_operations.append_state_event(
            uow.session,
            {**event_material, "event_hash": canonical_hash(stable_event_material)},
        )

    @staticmethod
    def _transition_reachable(current: str, target: str) -> bool:
        return target in {
            "SUBMITTING": {"INVALID", "FAILED"},
            "UNKNOWN": {"INVALID", "FAILED"},
            "RELAYER_NEW": {"INVALID", "FAILED"},
            "EXECUTED": {"INVALID", "FAILED"},
            "MINED": {"INVALID", "FAILED"},
            "RELAYER_CONFIRMED": {"INVALID", "FAILED", "SETTLEMENT_CONFLICT"},
            "MINED_PROVISIONAL": {"INVALID", "FAILED", "SETTLEMENT_CONFLICT"},
        }.get(current, set())

    @staticmethod
    def _desired_state(current: str, evidence: ChainRecoveryEvidence) -> str:
        if evidence.receipt_removed or evidence.canonical is False:
            return "REORGED"
        if evidence.receipt_success is False:
            return "FAILED"
        if evidence.relayer_state in {"INVALID", "FAILED"}:
            return str(evidence.relayer_state)
        if evidence.relayer_state == "CONFIRMED" and not evidence.transaction_hash:
            return "UNKNOWN"
        if (
            evidence.relayer_state == "CONFIRMED"
            and evidence.receipt_success is True
            and evidence.canonical is True
            and evidence.finalized_after_receipt is True
            and evidence.post_balance is not None
            and evidence.balance_artifact_hash is not None
            and evidence.balance_artifact_id is not None
        ):
            return "FINALIZED"
        if evidence.receipt_success is True and evidence.canonical is True:
            return "MINED_PROVISIONAL" if evidence.relayer_state == "CONFIRMED" else "MINED"
        if evidence.relayer_state in {"MINED", "CONFIRMED"}:
            return "UNKNOWN"
        return {
            "NEW": "RELAYER_NEW",
            "EXECUTED": "EXECUTED",
            "MINED": "MINED",
            "CONFIRMED": "RELAYER_CONFIRMED",
        }.get(evidence.relayer_state or "", "UNKNOWN")

    @staticmethod
    def _path_to(current: str, desired: str) -> tuple[str, ...]:
        if current == desired:
            return ()
        graph: dict[str, tuple[str, ...]] = {
            "PREPARED": ("SUBMITTING",),
            "SUBMITTING": ("UNKNOWN", "RELAYER_NEW", "EXECUTED", "INVALID", "FAILED"),
            "UNKNOWN": (
                "RELAYER_NEW", "EXECUTED", "REORGED", "INVALID", "FAILED",
                "SETTLEMENT_CONFLICT", "REVERSED",
            ),
            "RELAYER_NEW": ("EXECUTED", "MINED", "UNKNOWN", "REORGED", "INVALID", "FAILED"),
            "EXECUTED": ("MINED", "UNKNOWN", "REORGED", "INVALID", "FAILED"),
            # A confirmed receipt must pass through RELAYER_CONFIRMED before it
            # becomes provisional/final.  Besides preserving provider semantics,
            # the deferred FINALIZED gate requires this append-only proof.
            "MINED": ("RELAYER_CONFIRMED", "UNKNOWN", "REORGED", "INVALID", "FAILED"),
            "RELAYER_CONFIRMED": (
                "MINED_PROVISIONAL", "FINALIZED", "UNKNOWN", "REORGED",
                "INVALID", "FAILED", "SETTLEMENT_CONFLICT",
            ),
            "MINED_PROVISIONAL": (
                "FINALIZED", "UNKNOWN", "REORGED", "INVALID", "FAILED",
                "SETTLEMENT_CONFLICT",
            ),
            "REORGED": ("UNKNOWN",),
        }
        queue: list[tuple[str, tuple[str, ...]]] = [(current, ())]
        visited = {current}
        while queue:
            state, path = queue.pop(0)
            for target in graph.get(state, ()):
                next_path = (*path, target)
                if target == desired:
                    return next_path
                if target not in visited:
                    visited.add(target)
                    queue.append((target, next_path))
        raise RuntimeError(f"chain_recovery_transition_unreachable:{current}->{desired}")

    @staticmethod
    def _finality_evidence_exact(op: dict[str, Any], evidence: ChainRecoveryEvidence) -> bool:
        return bool(
            evidence.transaction_hash
            and evidence.transaction_hash.lower() == str(op.get("transaction_hash") or evidence.transaction_hash).lower()
            and evidence.receipt_success is True
            and evidence.receipt_removed is False
            and evidence.receipt_block_number is not None
            and evidence.receipt_block_hash is not None
            and evidence.canonical is True
            and evidence.canonical_block_hash == evidence.receipt_block_hash
            and evidence.finalized_block_number is not None
            and evidence.finalized_block_hash is not None
            and evidence.finalized_block_number > evidence.receipt_block_number
            and evidence.finalized_after_receipt is True
            and evidence.post_balance is not None
            and evidence.balance_artifact_hash is not None
            and evidence.balance_artifact_id is not None
        )

    async def _apply_economic_effect(
        self,
        uow: UnitOfWork,
        *,
        operation_id: int,
        op: dict[str, Any],
        evidence: ChainRecoveryEvidence,
    ) -> dict[str, Any]:
        existing_rows = (
            await uow.session.execute(
                text(
                    "SELECT id, transaction_key, portfolio_namespace "
                    "FROM trading.ledger_transactions "
                    "WHERE chain_operation_id=:oid AND kind='SETTLEMENT' "
                    "ORDER BY portfolio_namespace FOR UPDATE"
                ),
                {"oid": operation_id},
            )
        ).mappings().all()
        if existing_rows:
            return {
                "applied": False,
                "ledger_transaction_id": int(existing_rows[0]["id"]),
                "ledger_transaction_ids": [int(row["id"]) for row in existing_rows],
                "transaction_key": existing_rows[0]["transaction_key"],
            }

        pre = op.get("pre_balance") or {}
        post = evidence.post_balance or {}
        pre_tokens = pre.get("tokens")
        post_tokens = post.get("tokens")
        pre_contracts = pre.get("contracts")
        post_contracts = post.get("contracts")
        if not isinstance(pre_tokens, dict) or not isinstance(post_tokens, dict):
            raise RuntimeError("chain_balance_token_set_missing")
        if (
            not isinstance(pre_contracts, dict)
            or pre_contracts != post_contracts
            or pre_contracts.get("registry_bundle_hash") != op.get("registry_bundle_content_hash")
        ):
            raise RuntimeError("chain_balance_contract_binding_mismatch")
        pusd_asset = str(pre_contracts.get("pusd") or "").lower()
        if len(pusd_asset) != 42:
            raise RuntimeError("chain_balance_pusd_binding_missing")
        normalized_pre = {
            str(key).lower(): self._base_units(value, "pre_token_balance")
            for key, value in pre_tokens.items()
        }
        normalized_post = {
            str(key).lower(): self._base_units(value, "post_token_balance")
            for key, value in post_tokens.items()
        }
        if (
            len(normalized_pre) != 2
            or set(normalized_pre) != set(normalized_post)
            or any(value != 0 for value in normalized_post.values())
        ):
            raise RuntimeError("chain_redeem_token_balance_mismatch")
        pre_cash = self._base_units(pre.get("pusd", 0), "pre_cash_balance")
        post_cash = self._base_units(post.get("pusd", 0), "post_cash_balance")
        cash_delta = post_cash - pre_cash
        if cash_delta <= 0 or sum(normalized_pre.values()) <= 0:
            raise RuntimeError("chain_redeem_zero_or_negative_effect")

        position_rows = [dict(row) for row in (
            await uow.session.execute(
                text(
                    "SELECT p.id, p.portfolio_namespace, p.contract_spec_id, p.token_id, "
                    "p.quantity, p.cost_basis, p.version, t.token_id AS external_token_id, "
                    "t.outcome_index "
                    "FROM trading.positions p JOIN trading.pm_tokens t ON t.id=p.token_id "
                    "WHERE p.account_id=:account AND p.market_id=:market "
                    "AND p.quantity > 0 "
                    "ORDER BY p.portfolio_namespace, t.outcome_index, p.id FOR UPDATE OF p"
                ),
                {"account": op["account_id"], "market": op["market_id"]},
            )
        ).mappings().all()]
        aggregates: dict[tuple[str, str], int] = {}
        for row in position_rows:
            key = (str(row["portfolio_namespace"]), str(row["external_token_id"]).lower())
            aggregates[key] = aggregates.get(key, 0) + self._base_units(
                row["quantity"], "position_quantity"
            )
        actual_by_token = {token: 0 for token in normalized_pre}
        for (_, token), quantity in aggregates.items():
            if token not in actual_by_token:
                raise RuntimeError("chain_redeem_position_token_set_conflict")
            actual_by_token[token] += quantity
        if actual_by_token != normalized_pre or not aggregates:
            raise RuntimeError("chain_redeem_position_reconciliation_conflict")

        payout_row = (
            await uow.session.execute(
                text(
                    "SELECT token_set, payout_vector FROM trading.settlement_observations "
                    "WHERE settlement_set_key=:set_key AND source_kind='ctf_payout' "
                    "AND status='COMPLETE' FOR SHARE"
                ),
                {"set_key": op["settlement_set_key"]},
            )
        ).mappings().one_or_none()
        ordered_tokens = tuple(str(token).lower() for token in (payout_row or {}).get("token_set", ()))
        if payout_row is None or len(ordered_tokens) != 2 or set(ordered_tokens) != set(normalized_pre):
            raise RuntimeError("chain_redeem_payout_evidence_missing")
        vector = payout_row["payout_vector"] or {}
        numerators = vector.get("numerators")
        denominator = self._base_units(vector.get("denominator"), "payout_denominator")
        if not isinstance(numerators, list) or len(numerators) != 2 or denominator <= 0:
            raise RuntimeError("chain_redeem_payout_vector_invalid")
        payout_by_token = {
            token: self._base_units(numerators[index], "payout_numerator")
            for index, token in enumerate(ordered_tokens)
        }
        if any(value < 0 for value in payout_by_token.values()) or sum(
            payout_by_token.values()
        ) != denominator:
            raise RuntimeError("chain_redeem_payout_vector_invalid")

        frozen_allocation = []
        for (namespace, token), quantity in sorted(aggregates.items()):
            product = quantity * payout_by_token[token]
            if product % denominator:
                raise RuntimeError("chain_redeem_fractional_base_unit_allocation")
            frozen_allocation.append({
                "portfolio_namespace": namespace,
                "token_id": token,
                "quantity_base_units": str(quantity),
                "expected_cash_base_units": str(product // denominator),
            })
        if frozen_allocation != list(op.get("settlement_allocation") or []):
            raise RuntimeError("chain_redeem_frozen_allocation_conflict")
        if canonical_hash(frozen_allocation) != op.get("settlement_allocation_hash"):
            raise RuntimeError("chain_redeem_allocation_hash_conflict")

        expected_cash = sum(
            int(row["expected_cash_base_units"]) for row in frozen_allocation
        )
        if cash_delta != expected_cash:
            raise RuntimeError("chain_redeem_cash_delta_conflict")
        namespace_cash: dict[str, int] = {namespace: 0 for namespace, _ in aggregates}
        for row in frozen_allocation:
            namespace_cash[row["portfolio_namespace"]] += int(
                row["expected_cash_base_units"]
            )
        if sum(namespace_cash.values()) != cash_delta:
            raise RuntimeError("chain_redeem_cash_allocation_conflict")

        for row in position_rows:
            result = await uow.session.execute(
                text(
                    "UPDATE trading.positions SET quantity=0, cost_basis=0, version=version+1 "
                    "WHERE id=:id AND version=:version"
                ),
                {"id": row["id"], "version": row["version"]},
            )
            if result.rowcount != 1:
                raise RuntimeError("chain_redeem_position_version_conflict")

        ledger_ids: list[int] = []
        economic_allocation: list[dict[str, Any]] = []
        for namespace in sorted(namespace_cash):
            tx_id = await self.ledger.insert_transaction(
                uow.session,
                transaction_key=f"settle-{op['operation_key']}:{namespace}",
                kind="SETTLEMENT",
                trade_decision_id=None,
                execution_id=None,
                portfolio_namespace=namespace,
                account_id=op["account_id"],
                chain_operation_id=operation_id,
            )
            postings: list[dict[str, Any]] = []
            posting_no = 0
            cash_share = namespace_cash[namespace]
            if cash_share:
                postings.extend([
                    {"posting_no": posting_no, "asset_type": "CASH", "asset_key": pusd_asset,
                     "amount": str(cash_share), "counterparty": namespace},
                    {"posting_no": posting_no + 1, "asset_type": "CASH", "asset_key": pusd_asset,
                     "amount": str(-cash_share), "counterparty": "ctf:redeem"},
                ])
                posting_no += 2
            for (row_namespace, token), quantity in sorted(aggregates.items()):
                if row_namespace != namespace:
                    continue
                postings.extend([
                    {"posting_no": posting_no, "asset_type": "TOKEN", "asset_key": token,
                     "amount": str(-quantity), "counterparty": namespace},
                    {"posting_no": posting_no + 1, "asset_type": "TOKEN", "asset_key": token,
                     "amount": str(quantity), "counterparty": "ctf:redeem"},
                ])
                posting_no += 2
                economic_allocation.append({
                    "namespace": namespace,
                    "token_id": token,
                    "token_delta": str(-quantity),
                    "cash_entitlement_numerator": str(quantity * payout_by_token[token]),
                    "cash_entitlement_denominator": str(denominator),
                    "ledger_transaction_id": tx_id,
                })
            await self.ledger.insert_postings(
                uow.session, transaction_id=tx_id, postings=postings
            )
            if not await self.ledger.mark_posted(uow.session, tx_id, posted_at=_utcnow()):
                raise RuntimeError("chain_redeem_ledger_post_conflict")
            ledger_ids.append(tx_id)

        allocation_hash = canonical_hash({
            "operation_key": op["operation_key"],
            "frozen_token_allocation": frozen_allocation,
            "namespace_cash": {key: str(value) for key, value in sorted(namespace_cash.items())},
        })
        event = create_envelope(
            topic="chain.settlement.finalized",
            schema_version=1,
            aggregate_type="chain_operation",
            aggregate_id=op["operation_key"],
            idempotency_key=f"chain-settlement:{op['operation_key']}",
            priority=32,
            release_manifest_id=op["release_manifest_id"],
            payload={
                "operation_id": operation_id,
                "operation_key": op["operation_key"],
                "market_id": op["market_id"],
                "condition_id": op["condition_id"],
                "ledger_transaction_ids": ledger_ids,
                "cash_delta": str(cash_delta),
                "namespace_cash": {key: str(value) for key, value in sorted(namespace_cash.items())},
                "allocation": economic_allocation,
                "allocation_hash": allocation_hash,
                "balance_artifact_hash": evidence.balance_artifact_hash,
            },
        )
        await self.outbox.enqueue(uow.session, event)
        await self.audit.insert_workflow_event(
            uow.session,
            event_key=f"chain-settlement:{op['operation_key']}:finalized",
            event_type="SETTLEMENT_FINALIZED",
            aggregate_type="chain_operation",
            aggregate_id=op["operation_key"],
            payload_hash=canonical_hash(event.payload),
            payload=event.payload,
        )
        return {
            "applied": True,
            "ledger_transaction_id": ledger_ids[0],
            "ledger_transaction_ids": ledger_ids,
            "transaction_key": f"settle-{op['operation_key']}",
            "allocation_hash": allocation_hash,
        }

    @staticmethod
    def _base_units(value: Any, path: str) -> int:
        try:
            decimal_value = Decimal(str(value))
        except Exception as exc:
            raise RuntimeError(f"chain_{path}_invalid") from exc
        if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
            raise RuntimeError(f"chain_{path}_not_integer")
        return int(decimal_value)

    @staticmethod
    def _prepared_from_row(
        row: dict[str, Any], *, transport_owner: bool = False
    ) -> PreparedOperation:
        deadline = row["deadline"]
        if deadline is None:
            raise RuntimeError("chain_existing_operation_wire_incomplete")
        return PreparedOperation(
            operation_id=int(row["id"]),
            operation_key=row["operation_key"],
            account_id=int(row["account_id"]),
            fencing_token=int(row["fencing_token"]),
            economic_hash=row["economic_hash"],
            expected_operation_hash=row["expected_operation_hash"],
            calldata=row["calldata"],
            body_hash=row["body_hash"],
            nonce=row["relayer_nonce"],
            deadline=deadline,
            transport_owner=transport_owner,
        )


def _utcnow() -> datetime:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
