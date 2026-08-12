"""Settlement / label typed DTO（WP-04 Checkpoint B）。

只表达 typed 输入；strict allowlist（``extra="forbid"``）防任意字段注入。
Logic 决定状态机与证据核验；本包只做严格解析/规范化。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

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
    market_id: int | None = Field(default=None, gt=0)
    registry_version_id: int = Field(gt=0)
    target_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    amount_base_units: Decimal = Field(ge=0)
    calldata: str = Field(min_length=2)
    calldata_keccak: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    call_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_operation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_hash1: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_hash2: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    fence_token: int = Field(gt=0)


class SettlementObservationInput(BaseModel):
    """一条结算观察（append-only；五元组 exact set 由 deferred trigger 核验）。"""

    model_config = ConfigDict(extra="forbid")

    observation_key: str = Field(min_length=1)
    source_kind: str = Field(pattern="^(gamma_clob_closed|ctf_payout|data_api_redeemable|clob_winner_5050|label_audit)$")
    condition_id: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    market_id: int | None = Field(default=None, gt=0)
    token_set: list[str] = Field(min_length=1)
    outcome_index: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    winner: str | None = None
    is_50_50_outcome: bool | None = None
    redeemable: bool | None = None
    label_audit_version: str | None = None
    as_of: datetime
    received_at: datetime
    raw_artifact_ref: str | None = None
    raw_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern="^(PENDING|COMPLETE|CONFLICT)$")
