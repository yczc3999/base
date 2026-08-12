"""Trading settlement / label models（WP-04 Checkpoint B，revision ``b1000040``）。

5 张表：resolution_labels、resolution_clusters、resolution_cluster_memberships、
score_targets、score_target_memberships。

不变量（任务 §5.1 / §5.2）：
- label identity = ``contract_spec_id + label_key + version_no``；revision 只 INSERT，
  ``supersedes_id IS NULL ⇔ version_no=1``；supersedes 必须同 contract、version 连续、
  前一状态允许（pending/provisional）；一个 (contract_spec, label_key) 同时最多一个
  current revision（deferred trigger 核验）。
- label 状态机 ``pending → provisional → disputed | final_admissible | final_excluded``；
  final_admissible 必填 resolution_state 且无 exclusion_reason；final_excluded 必填 reason；
  disputed 必填冲突数组。
- resolution cluster 创建时 outcome 未知；OPEN cluster 不得引用 final_admissible label；
  membership append-only 不可搬移；相同 contract_spec 不得属于两个 active cluster version。
- score target 只表达 exact canonical set；type/shape 互斥（bernoulli↔canonical_side、
  multiclass↔members 数组、mean_only 二者皆空）；成员权重定点数且 deferred 归一总和=1，
  token 双计禁止。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TimestampMixin,
    TradingBase,
)
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    probability_type,
    sha256_type,
    utc_timestamp_type,
)

LABEL_STATES = ("pending", "provisional", "disputed", "final_admissible", "final_excluded")
CLUSTER_SPLITS = ("train", "validation", "forward_holdout")
CLUSTER_STATUS = ("OPEN", "FROZEN", "RESOLVED")
TARGET_TYPES = ("bernoulli", "multiclass", "mean_only")


class ResolutionLabel(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """label revision（append-only）。identity=``contract_spec_id+label_key+version_no``。"""

    __tablename__ = "resolution_labels"
    __table_args__ = (
        UniqueConstraint(
            "contract_spec_id", "label_key", "version_no",
            name="uq_resolution_labels_identity",
        ),
        CheckConstraint(
            "state IN ('pending','provisional','disputed','final_admissible','final_excluded')",
            name="ck_resolution_labels_state_known",
        ),
        CheckConstraint(
            "state <> 'final_admissible' OR "
            "(resolution_state IS NOT NULL AND exclusion_reason IS NULL)",
            name="ck_resolution_labels_admissible_shape",
        ),
        CheckConstraint(
            "state <> 'final_excluded' OR exclusion_reason IS NOT NULL",
            name="ck_resolution_labels_excluded_reason",
        ),
        CheckConstraint(
            "state <> 'disputed' OR "
            "(conflict_set IS NOT NULL AND jsonb_typeof(conflict_set) = 'array')",
            name="ck_resolution_labels_disputed_conflict",
        ),
        CheckConstraint(
            "policy_code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_labels_policy_hash_hex",
        ),
        CheckConstraint(
            "(supersedes_id IS NULL) = (version_no = 1)",
            name="ck_resolution_labels_first_version",
        ),
        UniqueConstraint("supersedes_id", name="uq_resolution_labels_supersedes"),
        Index("ix_resolution_labels_contract", "contract_spec_id"),
        Index("ix_resolution_labels_label_key", "label_key"),
        {"schema": TRADING_SCHEMA},
    )

    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_resolution_labels_spec"),
        nullable=False,
    )
    label_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution_state: Mapped[str | None] = mapped_column(String(64))
    resolution_source: Mapped[str | None] = mapped_column(String(128))
    evidence_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.artifact_objects.id", name="fk_resolution_labels_evidence"),
    )
    raw_outcome: Mapped[dict | None] = mapped_column(JSONB)
    token_cashflow: Mapped[dict | None] = mapped_column(JSONB)
    policy_code_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.resolution_labels.id", name="fk_resolution_labels_supersedes"),
    )
    auditor_identity: Mapped[str | None] = mapped_column(String(128))
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))
    conflict_set: Mapped[list | None] = mapped_column(JSONB)


class ResolutionCluster(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """resolution cluster：创建时 outcome 未知，绑定唯一 split 与 time block。"""

    __tablename__ = "resolution_clusters"
    __table_args__ = (
        UniqueConstraint(
            "cluster_key", "cluster_version",
            name="uq_resolution_clusters_key_version",
        ),
        CheckConstraint(
            "split IN ('train','validation','forward_holdout')",
            name="ck_resolution_clusters_split_known",
        ),
        CheckConstraint(
            "time_block_end > time_block_start",
            name="ck_resolution_clusters_block_order",
        ),
        CheckConstraint(
            "status IN ('OPEN','FROZEN','RESOLVED')",
            name="ck_resolution_clusters_status_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    cluster_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cluster_version: Mapped[int] = mapped_column(Integer, nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    time_block_start: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    time_block_end: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    horizon: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")


class ResolutionClusterMembership(TradingBase, BigIntIdentityMixin):
    """``cluster × contract_spec × token`` 唯一；append-only 不可搬移。"""

    __tablename__ = "resolution_cluster_memberships"
    __table_args__ = (
        UniqueConstraint(
            "resolution_cluster_id", "contract_spec_id", "token_id",
            name="uq_resolution_cluster_memberships_pair",
        ),
        Index("ix_resolution_cluster_memberships_contract", "contract_spec_id"),
        {"schema": TRADING_SCHEMA},
    )

    resolution_cluster_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.resolution_clusters.id", name="fk_resolution_cluster_memberships_cluster"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_resolution_cluster_memberships_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_resolution_cluster_memberships_token"),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(),
        nullable=False,
        server_default=func.now(),
    )


class ScoreTarget(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """canonical score target：exact set，type/shape 互斥（任务 §5.2）。"""

    __tablename__ = "score_targets"
    __table_args__ = (
        UniqueConstraint("target_key", name="uq_score_targets_key"),
        CheckConstraint(
            "target_type IN ('bernoulli','multiclass','mean_only')",
            name="ck_score_targets_type_known",
        ),
        CheckConstraint(
            "(target_type = 'bernoulli') = (canonical_side IS NOT NULL AND members IS NULL)",
            name="ck_score_targets_bernoulli_shape",
        ),
        CheckConstraint(
            "(target_type = 'multiclass') = "
            "(members IS NOT NULL AND jsonb_typeof(members) = 'array' AND canonical_side IS NULL)",
            name="ck_score_targets_multiclass_shape",
        ),
        CheckConstraint(
            "target_type <> 'mean_only' OR (canonical_side IS NULL AND members IS NULL)",
            name="ck_score_targets_mean_shape",
        ),
        CheckConstraint(
            "canonical_side IS NULL OR canonical_side IN ('YES','NO')",
            name="ck_score_targets_side_known",
        ),
        CheckConstraint(
            "target_weight > 0 AND target_weight <= 1",
            name="ck_score_targets_weight_range",
        ),
        CheckConstraint(
            "length(btrim(horizon)) > 0",
            name="ck_score_targets_horizon_nonempty",
        ),
        CheckConstraint(
            "(target_type = 'bernoulli' AND payout_type = 'binary' "
            " AND payout_function_id IS NOT NULL) OR "
            "(target_type = 'multiclass' AND payout_type = 'multiclass' "
            " AND payout_function_id IS NULL) OR "
            "(target_type = 'mean_only' AND payout_type = 'scalar' "
            " AND payout_function_id IS NOT NULL)",
            name="ck_score_targets_payout_type_pair",
        ),
        {"schema": TRADING_SCHEMA},
    )

    target_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_score_targets_spec"),
        nullable=False,
    )
    resolution_cluster_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.resolution_clusters.id", name="fk_score_targets_cluster"),
        nullable=False,
    )
    horizon: Mapped[str] = mapped_column(String(64), nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)
    payout_function_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.payout_functions.id", name="fk_score_targets_payout"),
    )
    canonical_side: Mapped[str | None] = mapped_column(String(8))
    members: Mapped[list | None] = mapped_column(JSONB)
    payout_type: Mapped[str] = mapped_column(String(32), nullable=False)


class ScoreTargetMembership(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """target 成员与定点权重；deferred 核验权重总和=1、token 双计禁止。"""

    __tablename__ = "score_target_memberships"
    __table_args__ = (
        UniqueConstraint("score_target_id", "token_id", name="uq_score_target_memberships_pair"),
        CheckConstraint("member_weight > 0", name="ck_score_target_memberships_weight_positive"),
        Index("ix_score_target_memberships_target", "score_target_id"),
        {"schema": TRADING_SCHEMA},
    )

    score_target_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.score_targets.id", name="fk_score_target_memberships_target"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_score_target_memberships_token"),
        nullable=False,
    )
    member_weight: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)




# ======================================================================
# WP-06 Checkpoint B —— Polygon/Relayer/settlement 数据层（revision ``b1000052``）
#
# 4 张表：contract_registry / chain_operations / chain_operation_state_history /
# settlement_observations。只对既有 executions / ledger_transactions 增加最小可空
# lineage 列（见 execution.py / ledger.py）。禁止平行账户/Vault/账本/label/投影表。
# 不变量（任务 §5.1）：
# - registry append-only、同 chain+kind 唯一 active、发布前 completeness trigger。
# - chain_operations 绑定列不可变；状态机 CAS 由 history 触发推进；FINALIZED 必须
#   有 relayer CONFIRMED + canonical receipt + finalized block + pre/post balance +
#   registry hash + zero-conflict evidence；effect 只产生一次。
# - settlement_observations append-only；COMPLETE 五元组 exact set 由 deferred trigger 核验。
# ORM __table_args__ 必须与 b1000052 迁移 DDL 完全镜像（alembic check modeled drift=0）。
# ======================================================================

CHAIN_OPERATION_STATES = (
    "PREPARED", "SUBMITTING", "UNKNOWN", "RELAYER_NEW", "EXECUTED", "MINED",
    "RELAYER_CONFIRMED", "MINED_PROVISIONAL", "FINALIZED",
    "INVALID", "FAILED", "REORGED", "SETTLEMENT_CONFLICT", "REVERSED",
)
CHAIN_OPERATION_ACTIVE_STATES = (
    "PREPARED", "SUBMITTING", "UNKNOWN", "RELAYER_NEW", "EXECUTED", "MINED",
    "RELAYER_CONFIRMED", "MINED_PROVISIONAL", "REORGED",
)
CHAIN_OPERATION_TYPES = ("SPLIT", "MERGE", "REDEEM")
SETTLEMENT_SOURCE_KINDS = (
    "gamma_clob_closed", "ctf_payout", "data_api_redeemable",
    "clob_winner_5050", "label_audit",
)


class ContractRegistry(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """版本化合约注册表（append-only；同 chain+kind 唯一 active）。"""

    __tablename__ = "contract_registry"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "kind", "version_no",
            name="uq_contract_registry_chain_kind_version",
        ),
        CheckConstraint(
            "kind IN ('pusd','ctf','deposit_wallet','ctf_adapter_standard','neg_risk_adapter')",
            name="ck_contract_registry_kind_known",
        ),
        CheckConstraint(
            "proxy_kind IN ('none','eip1967','beacon')",
            name="ck_contract_registry_proxy_kind_known",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','SUPERSEDED')",
            name="ck_contract_registry_status_known",
        ),
        CheckConstraint("version_no > 0", name="ck_contract_registry_version_positive"),
        CheckConstraint("snapshot_block_number > 0", name="ck_contract_registry_snapshot_positive"),
        CheckConstraint("chain_id = 137", name="ck_contract_registry_chain_id"),
        CheckConstraint(
            "address ~ '^0x[0-9a-fA-F]{40}$'", name="ck_contract_registry_address_hex",
        ),
        CheckConstraint(
            "resolved_implementation_or_beacon IS NULL "
            "OR resolved_implementation_or_beacon ~ '^0x[0-9a-fA-F]{40}$'",
            name="ck_contract_registry_resolved_address_hex",
        ),
        CheckConstraint(
            "runtime_keccak ~ '^0x[0-9a-f]{64}$'", name="ck_contract_registry_runtime_keccak_hex",
        ),
        CheckConstraint(
            "resolved_code_keccak ~ '^0x[0-9a-f]{64}$'", name="ck_contract_registry_resolved_code_hex",
        ),
        CheckConstraint(
            "snapshot_block_hash ~ '^0x[0-9a-f]{64}$'", name="ck_contract_registry_block_hash_hex",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_contract_registry_content_hash_hex",
        ),
        CheckConstraint(
            "(proxy_kind = 'none') = (resolved_implementation_or_beacon IS NULL)",
            name="ck_contract_registry_resolved_pair",
        ),
        CheckConstraint(
            "extra IS NULL OR jsonb_typeof(extra) = 'object'",
            name="ck_contract_registry_extra_object",
        ),
        Index(
            "uq_contract_registry_active_per_chain_kind",
            "chain_id", "kind",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_contract_registry_kind", "kind"),
        {"schema": TRADING_SCHEMA},
    )

    registry_version: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    address: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    proxy_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    runtime_keccak: Mapped[str] = mapped_column(String(66, collation="C"), nullable=False)
    resolved_implementation_or_beacon: Mapped[str | None] = mapped_column(external_id_type())
    resolved_code_keccak: Mapped[str] = mapped_column(
        String(66, collation="C"), nullable=False
    )
    snapshot_block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_block_hash: Mapped[str] = mapped_column(String(66, collation="C"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ACTIVE"
    )


class ChainOperation(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """一次链上操作（split/merge/redeem）的事实记录。"""

    __tablename__ = "chain_operations"
    __table_args__ = (
        UniqueConstraint("operation_key", name="uq_chain_operations_key"),
        UniqueConstraint("idempotency_key", name="uq_chain_operations_idempotency"),
        UniqueConstraint("economic_hash", name="uq_chain_operations_economic_hash"),
        UniqueConstraint("expected_operation_hash", name="uq_chain_operations_expected_hash"),
        CheckConstraint(
            "operation_type IN ('SPLIT','MERGE','REDEEM')",
            name="ck_chain_operations_type_known",
        ),
        CheckConstraint(
            "status IN (" + ",".join(f"'{s}'" for s in CHAIN_OPERATION_STATES) + ")",
            name="ck_chain_operations_status_known",
        ),
        CheckConstraint("chain_id = 137", name="ck_chain_operations_chain_id"),
        CheckConstraint("fencing_token > 0", name="ck_chain_operations_fencing_positive"),
        CheckConstraint(
            "(operation_type = 'REDEEM' AND amount_base_units = 0) OR "
            "(operation_type IN ('SPLIT','MERGE') AND amount_base_units > 0)",
            name="ck_chain_operations_amount_range",
        ),
        CheckConstraint(
            "economic_hash ~ '^[0-9a-f]{64}$' AND calldata_keccak ~ '^[0-9a-f]{64}$' "
            "AND body_hash ~ '^[0-9a-f]{64}$' AND call_set_hash ~ '^[0-9a-f]{64}$' "
            "AND expected_operation_hash ~ '^[0-9a-f]{64}$' "
            "AND preflight_hash1 ~ '^[0-9a-f]{64}$' "
            "AND preflight_hash2 ~ '^[0-9a-f]{64}$'",
            name="ck_chain_operations_hashes_hex",
        ),
        CheckConstraint(
            "wallet_address ~ '^0x[0-9a-fA-F]{40}$'", name="ck_chain_operations_wallet_hex",
        ),
        CheckConstraint(
            "target_address ~ '^0x[0-9a-fA-F]{40}$'", name="ck_chain_operations_target_hex",
        ),
        CheckConstraint(
            "condition_id ~ '^0x[0-9a-fA-F]{64}$'", name="ck_chain_operations_condition_hex",
        ),
        CheckConstraint(
            "receipt_block_hash IS NULL OR receipt_block_hash ~ '^0x[0-9a-f]{64}$'",
            name="ck_chain_operations_receipt_hash_hex",
        ),
        CheckConstraint(
            "canonical_block_hash IS NULL OR canonical_block_hash ~ '^0x[0-9a-f]{64}$'",
            name="ck_chain_operations_canonical_hash_hex",
        ),
        CheckConstraint(
            "finalized_block_hash IS NULL OR finalized_block_hash ~ '^0x[0-9a-f]{64}$'",
            name="ck_chain_operations_finalized_hash_hex",
        ),
        CheckConstraint(
            "registry_content_hash ~ '^[0-9a-f]{64}$' "
            "AND registry_bundle_content_hash ~ '^[0-9a-f]{64}$' "
            "AND registry_evidence_hash ~ '^[0-9a-f]{64}$' "
            "AND geo_evidence_hash ~ '^[0-9a-f]{64}$' "
            "AND (balance_evidence_hash IS NULL OR balance_evidence_hash ~ '^[0-9a-f]{64}$') "
            "AND (settlement_set_key IS NULL OR settlement_set_key ~ '^[0-9a-f]{64}$')",
            name="ck_chain_operations_evidence_hashes_hex",
        ),
        CheckConstraint(
            "(pre_balance IS NULL OR jsonb_typeof(pre_balance) = 'object') AND "
            "(post_balance IS NULL OR jsonb_typeof(post_balance) = 'object') AND "
            "jsonb_typeof(registry_bundle) = 'object'",
            name="ck_chain_operations_balances_object",
        ),
        CheckConstraint(
            "(balance_evidence_artifact_id IS NULL) = (balance_evidence_hash IS NULL)",
            name="ck_chain_operations_balance_artifact_pair",
        ),
        CheckConstraint(
            "length(btrim(geo_source_version)) > 0",
            name="ck_chain_operations_geo_source_nonempty",
        ),
        CheckConstraint(
            "jsonb_typeof(settlement_allocation) = 'array' "
            "AND jsonb_array_length(settlement_allocation) > 0 "
            "AND settlement_allocation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_chain_operations_allocation_shape",
        ),
        CheckConstraint(
            "NOT economic_effect_applied OR status IN "
            "('FINALIZED','SETTLEMENT_CONFLICT','REVERSED')",
            name="ck_chain_operations_economic_once",
        ),
        CheckConstraint(
            "status <> 'FINALIZED' OR ("
            "transaction_id IS NOT NULL AND transaction_hash IS NOT NULL AND "
            "receipt_block_number IS NOT NULL AND receipt_block_hash IS NOT NULL AND "
            "receipt_status IS TRUE AND canonical_block_hash = receipt_block_hash AND "
            "finalized_block_number IS NOT NULL AND finalized_block_hash IS NOT NULL AND "
            "finalized_block_number > receipt_block_number AND "
            "registry_evidence_hash IS NOT NULL AND balance_evidence_hash IS NOT NULL AND "
            "settlement_set_key IS NOT NULL AND "
            "pre_balance IS NOT NULL AND post_balance IS NOT NULL)",
            name="ck_chain_operations_finalized_evidence",
        ),
        CheckConstraint(
            "status NOT IN ('MINED_PROVISIONAL','FINALIZED') OR "
            "(transaction_hash IS NOT NULL AND receipt_block_number IS NOT NULL "
            "AND receipt_block_hash IS NOT NULL)",
            name="ck_chain_operations_mining_evidence",
        ),
        CheckConstraint(
            "status NOT IN ('RELAYER_CONFIRMED','MINED','MINED_PROVISIONAL','FINALIZED') "
            "OR (transaction_id IS NOT NULL AND transaction_hash IS NOT NULL)",
            name="ck_chain_operations_relayer_evidence",
        ),
        Index(
            "uq_chain_operations_active_redeem",
            "account_id", "wallet_address", "condition_id",
            unique=True,
            postgresql_where=text(
                "operation_type = 'REDEEM' AND status IN ("
                + ",".join(f"'{s}'" for s in CHAIN_OPERATION_ACTIVE_STATES) + ")"
            ),
        ),
        Index("ix_chain_operations_status", "status"),
        Index("ix_chain_operations_account", "account_id"),
        Index("ix_chain_operations_condition", "condition_id"),
        {"schema": TRADING_SCHEMA},
    )

    operation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    economic_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_chain_operations_account"),
        nullable=False,
    )
    wallet_address: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66, collation="C"), nullable=False)
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_chain_operations_market"),
        nullable=False,
    )
    registry_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_registry.id", name="fk_chain_operations_registry"),
        nullable=False,
    )
    target_address: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    permission_ref: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    lease_owner: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_chain_operations_release"),
        nullable=False,
    )
    capital_permission_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.capital_permission_manifests.id",
            name="fk_chain_operations_permission",
        ),
        nullable=False,
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_base_units: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    calldata: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    calldata_keccak: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    body_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    call_set_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    expected_operation_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    preflight_hash1: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    preflight_hash2: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PREPARED")
    relayer_nonce: Mapped[str | None] = mapped_column(external_id_type())
    deadline: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    transaction_id: Mapped[str | None] = mapped_column(external_id_type())
    transaction_hash: Mapped[str | None] = mapped_column(external_id_type())
    receipt_block_number: Mapped[int | None] = mapped_column(BigInteger)
    receipt_block_hash: Mapped[str | None] = mapped_column(String(66, collation="C"))
    receipt_status: Mapped[bool | None] = mapped_column(Boolean)
    canonical_block_hash: Mapped[str | None] = mapped_column(String(66, collation="C"))
    finalized_block_number: Mapped[int | None] = mapped_column(BigInteger)
    finalized_block_hash: Mapped[str | None] = mapped_column(String(66, collation="C"))
    registry_content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    registry_bundle: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registry_bundle_content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    registry_evidence_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_chain_operations_registry_evidence_artifact",
        ),
        nullable=False,
    )
    registry_evidence_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    geo_evidence_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_chain_operations_geo_evidence_artifact",
        ),
        nullable=False,
    )
    geo_evidence_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    geo_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    geo_observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    geo_source_version: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    balance_evidence_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_chain_operations_balance_evidence_artifact",
        ),
    )
    balance_evidence_hash: Mapped[str | None] = mapped_column(sha256_type())
    settlement_set_key: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    settlement_allocation: Mapped[list] = mapped_column(JSONB, nullable=False)
    settlement_allocation_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    pre_balance: Mapped[dict | None] = mapped_column(JSONB)
    post_balance: Mapped[dict | None] = mapped_column(JSONB)
    economic_effect_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class ChainOperationStateHistory(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """链上操作状态机 append-only 历史（aggregate sequence 唯一；current 由 CAS 触发推进）。"""

    __tablename__ = "chain_operation_state_history"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "sequence_no",
            name="uq_chain_op_state_history_op_seq",
        ),
        UniqueConstraint("event_hash", name="uq_chain_op_state_history_hash"),
        CheckConstraint(
            "event_type IN (" + ",".join(f"'{s}'" for s in CHAIN_OPERATION_STATES) + ")",
            name="ck_chain_op_state_history_type_known",
        ),
        CheckConstraint(
            "event_type = transition_to", name="ck_chain_op_state_history_type_matches",
        ),
        CheckConstraint(
            "fence_token > 0", name="ck_chain_op_state_history_fence_positive",
        ),
        CheckConstraint(
            "sequence_no >= 0", name="ck_chain_op_state_history_seq_nonneg",
        ),
        CheckConstraint(
            "jsonb_typeof(event_payload) = 'object'",
            name="ck_chain_op_state_history_payload_object",
        ),
        CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'", name="ck_chain_op_state_history_event_hash_hex",
        ),
        CheckConstraint(
            "(transition_from = 'PREPARED' AND transition_to = 'SUBMITTING') OR "
            "(transition_from = 'SUBMITTING' AND transition_to IN "
            "('UNKNOWN','RELAYER_NEW','EXECUTED','INVALID','FAILED')) OR "
            "(transition_from = 'UNKNOWN' AND transition_to IN "
            "('RELAYER_NEW','EXECUTED','INVALID','FAILED','REORGED','SETTLEMENT_CONFLICT','REVERSED')) OR "
            "(transition_from = 'RELAYER_NEW' AND transition_to IN "
            "('EXECUTED','MINED','INVALID','FAILED','UNKNOWN','REORGED')) OR "
            "(transition_from = 'EXECUTED' AND transition_to IN "
            "('MINED','INVALID','FAILED','UNKNOWN','REORGED')) OR "
            "(transition_from = 'MINED' AND transition_to IN "
            "('RELAYER_CONFIRMED','MINED_PROVISIONAL','INVALID','FAILED','UNKNOWN','REORGED')) OR "
            "(transition_from = 'RELAYER_CONFIRMED' AND transition_to IN "
            "('MINED_PROVISIONAL','FINALIZED','INVALID','FAILED','UNKNOWN','REORGED','SETTLEMENT_CONFLICT')) OR "
            "(transition_from = 'MINED_PROVISIONAL' AND transition_to IN "
            "('FINALIZED','INVALID','FAILED','REORGED','UNKNOWN','SETTLEMENT_CONFLICT')) OR "
            "(transition_from = 'FINALIZED' AND transition_to IN "
            "('SETTLEMENT_CONFLICT','REVERSED')) OR "
            "(transition_from = 'REORGED' AND transition_to = 'UNKNOWN') OR "
            "(transition_from = 'REVERSED' AND transition_to = 'SETTLEMENT_CONFLICT')",
            name="ck_chain_op_state_history_transition_exact",
        ),
        Index("ix_chain_op_state_history_operation", "operation_id"),
        {"schema": TRADING_SCHEMA},
    )

    operation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.chain_operations.id", name="fk_chain_op_state_history_operation"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    transition_from: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_to: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    event_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    lease_owner: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SettlementObservation(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """结算观察 append-only：绑定 source kind/condition/token set/payout/winner/label audit。"""

    __tablename__ = "settlement_observations"
    __table_args__ = (
        UniqueConstraint("observation_key", name="uq_settlement_observations_key"),
        UniqueConstraint("content_hash", name="uq_settlement_observations_hash"),
        UniqueConstraint(
            "settlement_set_key", "source_kind",
            name="uq_settlement_observations_set_source",
        ),
        CheckConstraint(
            "source_kind IN (" + ",".join(f"'{s}'" for s in SETTLEMENT_SOURCE_KINDS) + ")",
            name="ck_settlement_observations_source_known",
        ),
        CheckConstraint(
            "status IN ('PENDING','COMPLETE','CONFLICT')",
            name="ck_settlement_observations_status_known",
        ),
        CheckConstraint(
            "condition_id ~ '^0x[0-9a-fA-F]{64}$'",
            name="ck_settlement_observations_condition_hex",
        ),
        CheckConstraint(
            "raw_artifact_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_observations_artifact_hash_hex",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_observations_content_hash_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(token_set) = 'array' AND jsonb_array_length(token_set) = 2",
            name="ck_settlement_observations_token_set",
        ),
        CheckConstraint(
            "settlement_set_key ~ '^[0-9a-f]{64}$' AND token_set_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_observations_set_hashes_hex",
        ),
        CheckConstraint(
            "as_of <= received_at AND as_of <= source_cutoff",
            name="ck_settlement_observations_time_order",
        ),
        CheckConstraint(
            "length(btrim(source_version)) > 0",
            name="ck_settlement_observations_source_version_nonempty",
        ),
        CheckConstraint(
            "raw_artifact_id IS NOT NULL OR raw_artifact_ref ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_observations_artifact_ref",
        ),
        CheckConstraint(
            "status <> 'COMPLETE' OR raw_artifact_id IS NOT NULL",
            name="ck_settlement_observations_complete_artifact",
        ),
        CheckConstraint(
            "(source_kind = 'ctf_payout') = "
            "(numerator IS NOT NULL AND denominator IS NOT NULL AND payout_vector IS NOT NULL)",
            name="ck_settlement_observations_payout_pair",
        ),
        CheckConstraint(
            "payout_vector IS NULL OR (jsonb_typeof(payout_vector) = 'object' "
            "AND jsonb_typeof(payout_vector->'numerators') = 'array' "
            "AND jsonb_array_length(payout_vector->'numerators') = 2 "
            "AND payout_vector ? 'denominator')",
            name="ck_settlement_observations_payout_vector",
        ),
        CheckConstraint(
            "(source_kind = 'clob_winner_5050') = "
            "(winner IS NOT NULL OR is_50_50_outcome IS NOT NULL)",
            name="ck_settlement_observations_winner_pair",
        ),
        CheckConstraint(
            "(source_kind = 'data_api_redeemable') = (redeemable IS NOT NULL)",
            name="ck_settlement_observations_redeemable_pair",
        ),
        Index("ix_settlement_observations_condition", "condition_id"),
        Index("ix_settlement_observations_source", "source_kind"),
        {"schema": TRADING_SCHEMA},
    )

    observation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    settlement_set_key: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66, collation="C"), nullable=False)
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_settlement_observations_market"),
        nullable=False,
    )
    token_set: Mapped[list] = mapped_column(JSONB, nullable=False)
    token_set_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    payout_vector: Mapped[dict | None] = mapped_column(JSONB)
    outcome_index: Mapped[str | None] = mapped_column(String(32))
    numerator: Mapped[str | None] = mapped_column(external_id_type())
    denominator: Mapped[str | None] = mapped_column(external_id_type())
    winner: Mapped[str | None] = mapped_column(String(32))
    is_50_50_outcome: Mapped[bool | None] = mapped_column(Boolean)
    redeemable: Mapped[bool | None] = mapped_column(Boolean)
    label_audit_version: Mapped[str | None] = mapped_column(String(64, collation="C"))
    source_version: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    source_cutoff: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    raw_artifact_ref: Mapped[str | None] = mapped_column(String(64, collation="C"))
    raw_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.artifact_objects.id", name="fk_settlement_observations_artifact"),
    )
    raw_artifact_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
