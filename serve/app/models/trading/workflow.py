"""Trading workflow models（WP-01C Checkpoint C，revision ``b1000013``；WP-02 b1000020 强化）。

8 张表：decision_opportunities、decision_opportunity_markets、episode_memberships、
forecast_episodes、episode_contract_specs、information_snapshots、information_snapshot_items、
gate_decisions。

不变量（任务 §7 / 架构 §1.1、§4.2、§10.3 A3）：
- parent opportunity 由 R0 SELECT/audit 创建；G1-fail child 独立 terminal；
  G2 fail child=PRE_COMMIT_TERMINAL、episode 数=0。
- forecast_episode 引用恰一个 component version + 一个 parent opportunity；episode key 唯一。
- deferred trigger 核验 ``episode_contract_specs`` 与 component membership 完全相等。
- gate_decisions append-only；G0/R0 只绑 screening，G1/G2 绑 opportunity，
  R1/G4/G5A/G5B/G6 绑 episode。
- episode 终态：ROUTED→BLIND_COMMITTED（WP-02 G6 原子提交）或 PRE_COMMIT_TERMINAL。
- forecast_episodes 携带 cognition_status（PENDING→…→COMMITTED|PRE_COMMIT_TERMINAL）与
  prior/evidence/commit 时间戳。
- information_snapshots 本期只冻结 Gate 结构化输入；不含 forecast/quote-derived blind 内容。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
from app.models.trading.types import external_id_type, sha256_type, utc_timestamp_type

CHAIN_TYPES = ("DECISION", "RESEARCH_EVAL")
OPP_STATUS = ("OPEN", "PRE_COMMIT_TERMINAL", "ROUTED", "SUPERSEDED")
OPP_DISPOSITION = ("completed", "rejected", "deferred", "failed", "expired", "superseded")
# WP-02：G6 原子 blind commit 允许 ROUTED→BLIND_COMMITTED。
EPISODE_STATUS = ("DRAFT", "ROUTED", "BLIND_COMMITTED", "PRE_COMMIT_TERMINAL")
# WP-02：gate allowlist 扩展 G4/G5A/G5B/G6（cognition gates，绑 episode）。
GATE_NAMES = ("G0", "R0", "G1", "G2", "R1", "G4", "G5A", "G5B", "G6")
ROUTE_CHANNELS = ("reject", "shallow", "standard", "deep")


class DecisionOpportunity(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """decision opportunity：parent（批次容器）与 G1/G2 child 共用（任务 §7）。"""

    __tablename__ = "decision_opportunities"
    __table_args__ = (
        UniqueConstraint("opportunity_key", name="uq_decision_opportunities_key"),
        CheckConstraint(
            "chain_type IN ('DECISION','RESEARCH_EVAL')",
            name="ck_decision_opportunities_chain_type_known",
        ),
        CheckConstraint(
            "status IN ('OPEN','PRE_COMMIT_TERMINAL','ROUTED','SUPERSEDED')",
            name="ck_decision_opportunities_status_known",
        ),
        CheckConstraint(
            "disposition IN ('completed','rejected','deferred','failed','expired','superseded')",
            name="ck_decision_opportunities_disposition_known",
        ),
        Index("ix_decision_opportunities_cohort", "cohort_id"),
        {"schema": TRADING_SCHEMA},
    )

    opportunity_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_decision_opportunities_cohort"),
        nullable=False,
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.decision_opportunities.id", name="fk_decision_opportunities_parent"),
    )
    chain_type: Mapped[str] = mapped_column(String(16), nullable=False)
    objective_contract_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_objective_contracts.id", name="fk_decision_opportunities_objective"),
        nullable=False,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_decision_opportunities_strategy"),
        nullable=False,
    )
    source_screening_episode_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.screening_episodes.id", name="fk_decision_opportunities_screening"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="OPEN")
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, server_default="deferred")
    terminal_reason: Mapped[str | None] = mapped_column(String(128))
    g0_manifest_hash: Mapped[str | None] = mapped_column(sha256_type())
    audit_tag: Mapped[str | None] = mapped_column(String(32))  # R0_REJECT_AUDIT 等
    triggered_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class DecisionOpportunityMarket(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """opportunity ↔ 触发 market（任务 §7 / A3）。"""

    __tablename__ = "decision_opportunity_markets"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "market_id", name="uq_decision_opportunity_markets_pair"),
        {"schema": TRADING_SCHEMA},
    )

    opportunity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.decision_opportunities.id", name="fk_decision_opportunity_markets_opp"),
        nullable=False,
    )
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_decision_opportunity_markets_market"),
        nullable=False,
    )


class ForecastEpisode(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """component-level forecast episode；恰属一个 component version（任务 §7/A3）。

    WP-02（b1000020）：新增 cognition_status 与 cognition 时间戳，支持
    ROUTED→BLIND_COMMITTED 原子提交。
    """

    __tablename__ = "forecast_episodes"
    __table_args__ = (
        UniqueConstraint("episode_key", name="uq_forecast_episodes_key"),
        CheckConstraint(
            "status IN ('DRAFT','ROUTED','BLIND_COMMITTED','PRE_COMMIT_TERMINAL')",
            name="ck_forecast_episodes_status_known",
        ),
        CheckConstraint(
            "cognition_status IN "
            "('PENDING','PRIOR_READY','EVIDENCE_READY','FORECAST_READY',"
            " 'COMMITTED','PRE_COMMIT_TERMINAL')",
            name="ck_forecast_episodes_cognition_status_known",
        ),
        CheckConstraint("episode_key ~ '^[0-9a-f]{64}$'", name="ck_forecast_episodes_key_hex"),
        Index("ix_forecast_episodes_opportunity", "decision_opportunity_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_key: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    decision_opportunity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.decision_opportunities.id", name="fk_forecast_episodes_opportunity"),
        nullable=False,
    )
    component_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_component_versions.id", name="fk_forecast_episodes_component_version"),
        nullable=False,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_forecast_episodes_strategy"),
        nullable=False,
    )
    objective_contract_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_objective_contracts.id", name="fk_forecast_episodes_objective"),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    horizon: Mapped[str] = mapped_column(String(64), nullable=False)
    experiment_variant: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    drop_reason: Mapped[str | None] = mapped_column(String(128))
    # WP-02 cognition 状态与时间戳
    cognition_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="PENDING"
    )
    prior_frozen_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    evidence_bundle_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    forecast_committed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class EpisodeContractSpec(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """``episode × contract_spec_id`` 唯一；集合必须与 component membership 全等（任务 §7）。"""

    __tablename__ = "episode_contract_specs"
    __table_args__ = (
        UniqueConstraint("episode_id", "contract_spec_id", name="uq_episode_contract_specs_pair"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_episode_contract_specs_episode"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_episode_contract_specs_spec"),
        nullable=False,
    )


class EpisodeMembership(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """G2 后的 episode 路由：route channel、首个拒绝 Gate、reason、disposition（任务 §7）。"""

    __tablename__ = "episode_memberships"
    __table_args__ = (
        UniqueConstraint("episode_id", name="uq_episode_memberships_episode"),
        CheckConstraint(
            "route_channel IN ('reject','shallow','standard','deep')",
            name="ck_episode_memberships_route_known",
        ),
        CheckConstraint(
            "processing_disposition IN ('completed','rejected','deferred','failed','expired','superseded')",
            name="ck_episode_memberships_disposition_known",
        ),
        CheckConstraint(
            "NOT action_eligible AND NOT qualification_eligible AND NOT capital_evidence_eligible",
            name="ck_episode_memberships_wp01c_no_eligibility",
        ),
        CheckConstraint(
            "route_channel <> 'reject' OR "
            "(reason_code IS NOT NULL AND (recheck_at IS NOT NULL OR recheck_condition IS NOT NULL))",
            name="ck_episode_memberships_reject_shape",
        ),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_episode_memberships_episode"),
        nullable=False,
    )
    route_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    first_rejected_gate: Mapped[str | None] = mapped_column(String(8))
    reason_code: Mapped[str | None] = mapped_column(String(128))
    recheck_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    recheck_condition: Mapped[str | None] = mapped_column(String(255))
    processing_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    action_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    qualification_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    capital_evidence_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    audit_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class InformationSnapshot(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """Gate 结构化输入冻结（本期不做 evidence bundle；任务 §7）。"""

    __tablename__ = "information_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_key", name="uq_information_snapshots_key"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_information_snapshots_hash_hex"),
        CheckConstraint(
            "gate IN ('G0','R0','G1','G2','R1','G4','G5A','G5B','G6')",
            name="ck_information_snapshots_gate_known",
        ),
        CheckConstraint("jsonb_typeof(content) = 'object'", name="ck_information_snapshots_content_object"),
        CheckConstraint(
            "(episode_id IS NULL) <> (opportunity_id IS NULL)",
            name="ck_information_snapshots_one_target",
        ),
        {"schema": TRADING_SCHEMA},
    )

    snapshot_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    episode_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_information_snapshots_episode"),
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.decision_opportunities.id", name="fk_information_snapshots_opportunity"),
    )
    gate: Mapped[str] = mapped_column(String(8), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class InformationSnapshotItem(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """snapshot 条目：artifact/version/hash（任务 §7）。"""

    __tablename__ = "information_snapshot_items"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "item_no", name="uq_information_snapshot_items_snapshot_no"),
        {"schema": TRADING_SCHEMA},
    )

    snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.information_snapshots.id", name="fk_information_snapshot_items_snapshot"),
        nullable=False,
    )
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    version_hash: Mapped[str | None] = mapped_column(sha256_type())
    item_data: Mapped[dict] = mapped_column(JSONB)


class GateDecision(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """每道 Gate 的输入/策略/版本/hash、结果、reason、committed_at；append-only（任务 §7）。"""

    __tablename__ = "gate_decisions"
    __table_args__ = (
        UniqueConstraint("gate", "target_id", "target_kind", name="uq_gate_decisions_target"),
        CheckConstraint(
            "gate IN ('G0','R0','G1','G2','R1','G4','G5A','G5B','G6')",
            name="ck_gate_decisions_gate_known",
        ),
        CheckConstraint(
            "target_kind IN ('screening','opportunity','episode')",
            name="ck_gate_decisions_target_kind_known",
        ),
        CheckConstraint(
            "(gate IN ('G0','R0') AND target_kind = 'screening') OR "
            "(gate IN ('G1','G2') AND target_kind = 'opportunity') OR "
            "(gate IN ('R1','G4','G5A','G5B','G6') AND target_kind = 'episode')",
            name="ck_gate_decisions_gate_target_pair",
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_gate_decisions_input_hash_hex"),
        CheckConstraint("policy_hash ~ '^[0-9a-f]{64}$'", name="ck_gate_decisions_policy_hash_hex"),
        Index("ix_gate_decisions_target", "gate", "target_kind", "target_id"),
        {"schema": TRADING_SCHEMA},
    )

    gate: Mapped[str] = mapped_column(String(8), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    policy_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    version_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_gate_decisions_release"),
        nullable=False,
    )
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    committed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
