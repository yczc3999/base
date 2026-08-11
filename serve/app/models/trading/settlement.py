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
    CheckConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TradingBase,
)
from app.models.trading.types import (
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
