"""Trading cohort models（WP-01C Checkpoint B，revision ``b1000013``）。

4 张表：evaluation_cohorts、universe_memberships、screening_episodes、audit_samples。

不变量（任务 §6 / 架构 §4.2、§10.3 A2）：
- cohort ``DRAFT→OPEN`` 前引用 active objective/strategy/release + 10 个冻结 policy；
  首个 membership 后不得改 objective/policy/seed（DB partial unique + Lifecycle guard）。
- ``cohort×market`` 唯一；``first_seen_source IN (REST_FRAME, WS_HINT)``；
  REST confirmation 只能 NULL→COMPLETE frame（not-null transition guard）。
- screening_episode 绑定 cohort + objective + R0 输入/result；disposition 唯一。
- audit_samples 保存算法/seed/stratum/inclusion probability/selected；同输入确定性可重建。
- 全部 append-only（immutable trigger 复用 0002 ``v2_reject_immutable_row``）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, Index
from sqlalchemy import ForeignKeyConstraint

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
    sha256_type,
    utc_timestamp_type,
)

COHORT_STATUS = ("DRAFT", "OPEN", "CLOSED", "SUPERSEDED")
R0_RESULTS = ("SELECT", "DEFER", "REJECT")
FIRST_SEEN_SOURCES = ("REST_FRAME", "WS_HINT")
AUDIT_TARGETS = ("r0", "r1", "contract", "component")

# cohort OPEN 前必须冻结的 policy 类型（任务 §6）
REQUIRED_COHORT_POLICIES = (
    "eligibility",
    "taxonomy",
    "horizon",
    "r0",
    "r1",
    "evidence_coverage",
    "shrinkage",
    "baseline_scoring",
    "split_inference",
    "reject_audit",
)


class EvaluationCohort(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """evaluation cohort：冻结 objective/strategy/release + 10 类 policy（任务 §6）。"""

    __tablename__ = "evaluation_cohorts"
    __table_args__ = (
        UniqueConstraint("cohort_key", name="uq_evaluation_cohorts_key"),
        CheckConstraint(
            "status IN ('DRAFT','OPEN','CLOSED','SUPERSEDED')",
            name="ck_evaluation_cohorts_status_known",
        ),
        CheckConstraint("seed_hash ~ '^[0-9a-f]{64}$'", name="ck_evaluation_cohorts_seed_hash_hex"),
        CheckConstraint(
            "jsonb_typeof(policy_hashes) = 'object'",
            name="ck_evaluation_cohorts_policy_hashes_object",
        ),
        {"schema": TRADING_SCHEMA},
    )

    cohort_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    objective_contract_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_objective_contracts.id", name="fk_evaluation_cohorts_objective"),
        nullable=False,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_evaluation_cohorts_strategy"),
        nullable=False,
    )
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_evaluation_cohorts_release"),
        nullable=False,
    )
    policy_hashes: Mapped[dict] = mapped_column(JSONB)  # {policy_type: content_hash}
    seed_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    closed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class UniverseMembership(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """``cohort×market`` 唯一发现成员；REST confirmation 只能 NULL→COMPLETE（任务 §6.2）。"""

    __tablename__ = "universe_memberships"
    __table_args__ = (
        UniqueConstraint("cohort_id", "market_id", name="uq_universe_memberships_cohort_market"),
        CheckConstraint(
            "first_seen_source IN ('REST_FRAME','WS_HINT')",
            name="ck_universe_memberships_first_seen_source_known",
        ),
        CheckConstraint("metadata_hash ~ '^[0-9a-f]{64}$'", name="ck_universe_memberships_metadata_hash_hex"),
        CheckConstraint(
            "(confirmed_frame_id IS NULL) = (confirmed_at IS NULL)",
            name="ck_universe_memberships_confirmation_pair",
        ),
        Index("ix_universe_memberships_cohort", "cohort_id"),
        {"schema": TRADING_SCHEMA},
    )

    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_universe_memberships_cohort"),
        nullable=False,
    )
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_universe_memberships_market"),
        nullable=False,
    )
    first_seen_source: Mapped[str] = mapped_column(String(16), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    first_ingested_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    metadata_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    confirmed_frame_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_universe_frames.id", name="fk_universe_memberships_confirmed_frame"),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class ScreeningEpisode(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """R0 筛选 episode：objective/G0 manifest + R0 输入/result（任务 §6.2/§7）。"""

    __tablename__ = "screening_episodes"
    __table_args__ = (
        UniqueConstraint("cohort_id", "market_id", "episode_no", name="uq_screening_episodes_cohort_market_no"),
        CheckConstraint(
            "result IN ('SELECT','DEFER','REJECT')",
            name="ck_screening_episodes_result_known",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_screening_episodes_input_hash_hex",
        ),
        CheckConstraint("episode_no > 0", name="ck_screening_episodes_no_positive"),
        CheckConstraint(
            "(result = 'SELECT' AND reason_code IS NULL "
            "AND recheck_at IS NULL AND recheck_condition IS NULL) OR "
            "(result IN ('DEFER','REJECT') AND reason_code IS NOT NULL "
            "AND (recheck_at IS NOT NULL OR recheck_condition IS NOT NULL))",
            name="ck_screening_episodes_disposition_shape",
        ),
        ForeignKeyConstraint(
            ["cohort_id", "market_id"],
            ["trading.universe_memberships.cohort_id", "trading.universe_memberships.market_id"],
            name="fk_screening_episodes_membership",
        ),
        Index("ix_screening_episodes_cohort", "cohort_id", "market_id"),
        {"schema": TRADING_SCHEMA},
    )

    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_screening_episodes_cohort"),
        nullable=False,
    )
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_screening_episodes_market"),
        nullable=False,
    )
    episode_no: Mapped[int] = mapped_column(Integer, nullable=False)
    objective_contract_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_objective_contracts.id", name="fk_screening_episodes_objective"),
        nullable=False,
    )
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)  # R0 allowlist DTO
    input_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    recheck_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    recheck_condition: Mapped[str | None] = mapped_column(String(255))
    audit_assigned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class AuditSample(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """拒绝审计抽样记录：确定性可重建（任务 §6.2/§2.8）。"""

    __tablename__ = "audit_samples"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id", "target", "content_hash",
            name="uq_audit_samples_cohort_target_hash",
        ),
        CheckConstraint(
            "target IN ('r0','r1','contract','component')",
            name="ck_audit_samples_target_known",
        ),
        CheckConstraint("seed_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_samples_seed_hash_hex"),
        CheckConstraint("algorithm_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_samples_algorithm_hash_hex"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_samples_content_hash_hex"),
        CheckConstraint("u >= 0 AND u < 1", name="ck_audit_samples_u_range"),
        CheckConstraint(
            "inclusion_probability >= 0 AND inclusion_probability <= 1",
            name="ck_audit_samples_probability_range",
        ),
        CheckConstraint(
            "selected = (u < inclusion_probability)",
            name="ck_audit_samples_selection_consistent",
        ),
        {"schema": TRADING_SCHEMA},
    )

    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_audit_samples_cohort"),
        nullable=False,
    )
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    stratum: Mapped[str] = mapped_column(String(64), nullable=False)
    seed_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    algorithm_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    u: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    inclusion_probability: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
