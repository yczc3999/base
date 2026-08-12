"""Settlement / label typed DTO（WP-04 Checkpoint B）。

只表达 typed 输入；strict allowlist（``extra="forbid"``）防任意字段注入。
Logic 决定状态机与证据核验；本包只做严格解析/规范化。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LABEL_STATES = (
    "pending", "provisional", "disputed", "final_admissible", "final_excluded"
)
CLUSTER_SPLITS = ("train", "validation", "forward_holdout")
CLUSTER_STATUS = ("OPEN", "FROZEN", "RESOLVED")
TARGET_TYPES = ("bernoulli", "multiclass", "mean_only")


class LabelRevisionInput(BaseModel):
    """一条 label revision 的 typed 输入（identity 由 contract_spec+label_key 派生）。"""

    model_config = ConfigDict(extra="forbid")

    contract_spec_id: int = Field(gt=0)
    label_key: str = Field(min_length=1)
    state: str = Field(
        pattern="^(pending|provisional|disputed|final_admissible|final_excluded)$"
    )
    resolution_state: str | None = None
    resolution_source: str | None = None
    evidence_artifact_id: int | None = Field(default=None, gt=0)
    raw_outcome: dict | None = None
    token_cashflow: dict | None = None
    policy_code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    auditor_identity: str | None = None
    supersedes_id: int | None = Field(default=None, gt=0)
    exclusion_reason: str | None = None
    conflict_set: list | None = None


class ClusterInput(BaseModel):
    """resolution cluster 创建输入（创建时 outcome 未知）。"""

    model_config = ConfigDict(extra="forbid")

    cluster_key: str = Field(min_length=1)
    cluster_version: int = Field(gt=0)
    split: str = Field(pattern="^(train|validation|forward_holdout)$")
    time_block_start: datetime
    time_block_end: datetime
    horizon: str = Field(min_length=1)
    status: str = Field(default="OPEN", pattern="^(OPEN|FROZEN|RESOLVED)$")


class ScoreTargetInput(BaseModel):
    """canonical score target 创建输入（type/shape 互斥）。"""

    model_config = ConfigDict(extra="forbid")

    target_key: str = Field(min_length=1)
    target_type: str = Field(pattern="^(bernoulli|multiclass|mean_only)$")
    contract_spec_id: int = Field(gt=0)
    resolution_cluster_id: int = Field(gt=0)
    horizon: str = Field(min_length=1)
    target_weight: Decimal = Field(gt=0, le=1)
    payout_function_id: int | None = Field(default=None, gt=0)
    canonical_side: str | None = Field(default=None, pattern="^(YES|NO)$")
    members: list[str] | None = None
    payout_type: str = Field(pattern="^(binary|multiclass|scalar)$")


# ======================================================================
# WP-06 Checkpoint B —— chain-settlement typed DTO（revision ``b1000052``）
# 只表达 typed 输入；strict allowlist（``extra="forbid"``）；Logic 决定状态机与证据核验。
# ======================================================================

CHAIN_OPERATION_TYPES = ("SPLIT", "MERGE", "REDEEM")
SETTLEMENT_SOURCE_KINDS = (
    "gamma_clob_closed", "ctf_payout", "data_api_redeemable",
    "clob_winner_5050", "label_audit",
)
CHAIN_OPERATION_STATES = (
    "PREPARED", "SUBMITTING", "UNKNOWN", "RELAYER_NEW", "EXECUTED", "MINED",
    "RELAYER_CONFIRMED", "MINED_PROVISIONAL", "FINALIZED",
    "INVALID", "FAILED", "REORGED", "SETTLEMENT_CONFLICT", "REVERSED",
)


class ContractRegistryPublishInput(BaseModel):
    """发布一条版本化合约注册表条目（append-only；同 chain+kind 唯一 active）。"""

    model_config = ConfigDict(extra="forbid")

    registry_version: str = Field(min_length=1)
    kind: str = Field(pattern="^(pusd|ctf|deposit_wallet|ctf_adapter_standard|neg_risk_adapter)$")
    version_no: int = Field(gt=0)
    chain_id: int = Field(ge=137, le=137)
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    proxy_kind: str = Field(pattern="^(none|eip1967|beacon)$")
    runtime_keccak: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    resolved_implementation_or_beacon: str | None = Field(
        default=None, pattern=r"^0x[0-9a-fA-F]{40}$"
    )
    resolved_code_keccak: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    snapshot_block_number: int = Field(gt=0)
    snapshot_block_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    source_url: str = Field(min_length=1)
    retrieved_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extra: dict | None = None


class ChainOperationInput(BaseModel):
    """创建一次链上操作（split/merge/redeem）的 typed 输入。

    全部绑定列由 Logic 从冻结 registry/account/market/payout 推导；caller 不得覆盖
    adapter/calldata/amount。
    """

    model_config = ConfigDict(extra="forbid")

    operation_key: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    operation_type: str = Field(pattern="^(SPLIT|MERGE|REDEEM)$")
    chain_id: int = Field(ge=137, le=137)
    account_id: int = Field(gt=0)
    wallet_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    condition_id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    market_id: int = Field(gt=0)
    registry_version_id: int = Field(gt=0)
    target_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    lease_owner: str = Field(min_length=1)
    amount_base_units: Decimal = Field(ge=0)
    calldata: str = Field(min_length=2)
    calldata_keccak: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    call_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_operation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_hash1: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_hash2: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_bundle: dict[str, str]
    registry_bundle_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_evidence_artifact_id: int = Field(gt=0)
    registry_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    geo_evidence_artifact_id: int = Field(gt=0)
    geo_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    geo_allowed: bool
    geo_observed_at: datetime
    geo_source_version: str = Field(min_length=1, max_length=64)
    settlement_set_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    settlement_allocation: list[dict] = Field(min_length=1)
    settlement_allocation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pre_balance: dict[str, object]


class ChainOperationStateEvent(BaseModel):
    """append 一条状态机事件（CAS 触发校验 transition_from=fence 后推进 aggregate）。"""

    model_config = ConfigDict(extra="forbid")

    operation_id: int = Field(gt=0)
    sequence_no: int = Field(ge=0)
    transition_from: str = Field(pattern="^(PREPARED|SUBMITTING|UNKNOWN|RELAYER_NEW|EXECUTED|MINED|RELAYER_CONFIRMED|MINED_PROVISIONAL|FINALIZED|INVALID|FAILED|REORGED|SETTLEMENT_CONFLICT|REVERSED)$")
    transition_to: str = Field(pattern="^(PREPARED|SUBMITTING|UNKNOWN|RELAYER_NEW|EXECUTED|MINED|RELAYER_CONFIRMED|MINED_PROVISIONAL|FINALIZED|INVALID|FAILED|REORGED|SETTLEMENT_CONFLICT|REVERSED)$")
    event_type: str = Field(min_length=1)
    event_payload: dict
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_owner: str = Field(min_length=1)
    fence_token: int = Field(gt=0)


class SettlementObservationInput(BaseModel):
    """一条结算观察（append-only；五元组 exact set 由 deferred trigger 核验）。"""

    model_config = ConfigDict(extra="forbid")

    observation_key: str = Field(min_length=1)
    settlement_set_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: str = Field(pattern="^(gamma_clob_closed|ctf_payout|data_api_redeemable|clob_winner_5050|label_audit)$")
    condition_id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    market_id: int = Field(gt=0)
    token_set: list[str] = Field(min_length=2, max_length=2)
    token_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payout_vector: dict | None = None
    outcome_index: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    winner: str | None = None
    is_50_50_outcome: bool | None = None
    redeemable: bool | None = None
    label_audit_version: str | None = None
    source_version: str = Field(min_length=1)
    source_cutoff: datetime
    as_of: datetime
    received_at: datetime
    raw_artifact_ref: str | None = None
    raw_artifact_id: int | None = Field(default=None, gt=0)
    raw_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern="^(PENDING|COMPLETE|CONFLICT)$")


class ChainRedeemRequest(BaseModel):
    """Scheduler-owned request for one logical redeem.

    Wallet, adapter, market ``neg_risk``, release and permission identities are
    deliberately absent: the Logic derives all of them from locked database facts.
    ``registry_content_hash`` is only a deployment assertion against the active DB
    registry row; it never selects a row or overrides its address.
    """

    model_config = ConfigDict(extra="forbid")

    operation_key: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    account_id: int = Field(gt=0)
    market_id: int = Field(gt=0)
    condition_id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    fencing_token: int = Field(gt=0)


class ChainWireEvidence(BaseModel):
    """Non-secret exact wire evidence persisted by TX1.

    The signed body and signature remain only in the driver's opaque in-memory
    object.  TX1 stores their hash plus nonce/deadline/call-set and the pre-submit
    balance observation, allowing the runtime to prove that the bytes sent after
    commit were exactly the bytes authorized by the database transaction.
    """

    model_config = ConfigDict(extra="forbid")

    nonce: str = Field(pattern=r"^[0-9]+$")
    deadline: datetime
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    call_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pre_balance: dict[str, object]
    registry_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_bundle: dict[str, str]
    registry_bundle_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    geo_evidence_artifact_id: int = Field(gt=0)
    geo_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    geo_allowed: bool
    geo_observed_at: datetime
    geo_source_version: str = Field(min_length=1, max_length=64)
    registry_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_evidence_artifact_id: int = Field(gt=0)
    settlement_set_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("deadline", "geo_observed_at")
    @classmethod
    def _deadline_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("chain_wire_deadline_timezone_required")
        return value


class ChainRecoveryEvidence(BaseModel):
    """Sanitized provider observations applied by one recovery TX.

    It contains no credential, signature, raw signed body or endpoint URL.  The
    runtime obtains the observations outside a UoW and the Logic revalidates the
    operation/fence before appending state and economic effects.
    """

    model_config = ConfigDict(extra="forbid")

    relayer_state: str | None = Field(
        default=None, pattern="^(NEW|EXECUTED|MINED|CONFIRMED|INVALID|FAILED)$"
    )
    transaction_id: str | None = None
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")
    nonce: str | None = Field(default=None, pattern=r"^[0-9]+$")
    receipt_block_number: int | None = Field(default=None, ge=0)
    receipt_block_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")
    canonical_block_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")
    finalized_block_number: int | None = Field(default=None, ge=0)
    finalized_block_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")
    canonical: bool | None = None
    receipt_success: bool | None = None
    receipt_removed: bool = False
    finalized_after_receipt: bool = False
    post_balance: dict[str, object] | None = None
    balance_artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    balance_artifact_id: int | None = Field(default=None, gt=0)
    # Every provider recovery observation is retained as a CAS artifact, including
    # UNKNOWN/provisional/failure/reorg observations that have no balance proof.
    provider_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_artifact_id: int = Field(gt=0)
    observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SettlementSourceArtifact(BaseModel):
    """Persisted raw source artifact identity; never embeds provider payload."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: int = Field(gt=0)
    source_version: str = Field(min_length=1, max_length=64)
    source_cutoff: datetime

    @field_validator("source_cutoff")
    @classmethod
    def _source_cutoff_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("chain_source_cutoff_timezone_required")
        return value


class ChainSettlementEvidenceInput(BaseModel):
    """Typed provider evidence for one coherent five-source settlement cut.

    The Logic checks every identity against market/token/label facts in PostgreSQL,
    derives each observation hash itself, and appends the five rows atomically.
    """

    model_config = ConfigDict(extra="forbid")

    market_id: int = Field(gt=0)
    condition_id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    token_set: list[str] = Field(min_length=2, max_length=2)
    cutoff_at: datetime
    received_at: datetime
    gamma_closed: bool
    gamma_accepting_orders: bool
    ctf_outcome_index: str = Field(min_length=1, max_length=32)
    ctf_numerator: str = Field(pattern=r"^[0-9]+$")
    ctf_denominator: str = Field(pattern=r"^[1-9][0-9]*$")
    ctf_payout_numerators: list[str] = Field(min_length=2, max_length=2)
    data_api_redeemable: bool
    clob_winner: str | None = Field(default=None, pattern="^(YES|NO)$")
    clob_is_50_50: bool
    label_id: int = Field(gt=0)
    label_version_no: int = Field(gt=0)
    label_resolution_state: str = Field(min_length=1, max_length=64)
    artifacts: dict[str, SettlementSourceArtifact]

    @field_validator("cutoff_at", "received_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("chain_settlement_evidence_timezone_required")
        return value

    @field_validator("token_set")
    @classmethod
    def _exact_tokens(cls, value: list[str]) -> list[str]:
        normalized = [str(item).lower() for item in value]
        if len(normalized) != len(set(normalized)) or any(not item for item in normalized):
            raise ValueError("chain_settlement_token_set_invalid")
        return normalized

    @field_validator("ctf_payout_numerators")
    @classmethod
    def _payout_vector(cls, value: list[str]) -> list[str]:
        if len(value) != 2 or any(not item.isdigit() for item in value):
            raise ValueError("chain_settlement_payout_vector_invalid")
        return value

    @field_validator("artifacts")
    @classmethod
    def _exact_artifacts(
        cls, value: dict[str, SettlementSourceArtifact]
    ) -> dict[str, SettlementSourceArtifact]:
        if set(value) != set(SETTLEMENT_SOURCE_KINDS):
            raise ValueError("chain_settlement_artifact_set_invalid")
        return value

    @model_validator(mode="after")
    def _coherent_source_cut(self) -> "ChainSettlementEvidenceInput":
        denominator = int(self.ctf_denominator)
        numerators = [int(value) for value in self.ctf_payout_numerators]
        if any(value < 0 for value in numerators) or sum(numerators) != denominator:
            raise ValueError("chain_settlement_payout_vector_not_normalized")
        if self.received_at < self.cutoff_at:
            raise ValueError("chain_settlement_received_before_cutoff")
        for kind, artifact in self.artifacts.items():
            if artifact.source_cutoff != self.cutoff_at:
                raise ValueError(f"chain_settlement_source_cutoff_mismatch:{kind}")
            # ArtifactStore refs are the SHA-256 of the uncompressed source bytes.
            # Keeping a second, caller-controlled digest would make provenance
            # ambiguous, so both identities must be exactly equal.
            if artifact.artifact_ref != artifact.artifact_hash:
                raise ValueError(f"chain_settlement_artifact_hash_mismatch:{kind}")
        return self
