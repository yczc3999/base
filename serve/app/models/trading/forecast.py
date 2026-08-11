"""Trading cognition / forecast models（WP-02 Checkpoint A，revision ``b1000020``）。

11 张表：priors、evidence_coverage_policies、evidence_revisions、evidence_bundles、
evidence_bundle_items、forecast_input_manifests、forecast_submissions、payout_projections、
coherence_checks、forecast_challenges、forecast_leases。

不变量（任务 §2 / 架构 §2.3、§3、§4）：
- prior 必填、显式、市场盲；保存 reference class/hazard、适用性、样本/选择规则、宽度、失效条件。
- evidence revision 保存 ``event_at/published_at/observed_at/ingested_at`` 四时态 + raw Artifact +
  source/type/branch + 前一 revision + 污染状态；``published_at/observed_at`` 不得晚于 episode cutoff
  （由 G5A Logic 校验 + deferred trigger 强约束）。
- bundle 引用 exact eligible revision；``forecast_input_manifest`` 绑定 bundle/spec/schema/prior/
  strategy/model/prompt/code 各 hash，输入乱序不改变 hash。
- ``Q`` 是 world-state→decimal-string 联合分布；``U`` 是非空、有限、去重的 coherent distribution 集
  且必须包含 ``Q``；``μ/V/bounds`` 由 Decimal 确定性代码计算（domain.probability）。
- submission 只允许 ``DRAFT→BLIND_COMMITTED``；commit 后禁止 UPDATE/DELETE（lifecycle guard）。
- payout projection 对每个 component 的每个 contract spec × token 恰一条；只有 Bernoulli 才派生
  nullable ``p_blind``。
- ``forecast_challenges`` 仅 append-only schema 骨架，不进入 champion 路径。
- ``forecast_lease`` 保存 ``valid_until`` + 结构化 invalidation conditions + evidence/schema/spec hashes；
  纯 quote/depth/cost/position 变化不得使 lease 失效。
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
    sha256_type,
    utc_timestamp_type,
)

# 认知状态（forecast_episodes.cognition_status，b1000020 强化）
COGNITION_STATUS = (
    "PENDING",
    "PRIOR_READY",
    "EVIDENCE_READY",
    "FORECAST_READY",
    "COMMITTED",
    "PRE_COMMIT_TERMINAL",
)

EVIDENCE_KINDS = ("observation", "source_claim", "inference", "conflict", "missing")
TAINT_STATUS = ("none", "market", "odds", "crowd", "label", "future_fact", "quarantined")


class Prior(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """显式市场盲先验；按 episode 版本化（任务 §2.2）。"""

    __tablename__ = "priors"
    __table_args__ = (
        UniqueConstraint("episode_id", "version_no", name="uq_priors_episode_version"),
        CheckConstraint("version_no > 0", name="ck_priors_version_positive"),
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_priors_content_object",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_priors_hash_hex"),
        CheckConstraint(
            "status IN ('draft','active','superseded')",
            name="ck_priors_status_known",
        ),
        CheckConstraint(
            "market_blind_declaration",
            name="ck_priors_market_blind_declared",
        ),
        CheckConstraint(
            "reference_class IS NOT NULL OR hazard_ref IS NOT NULL",
            name="ck_priors_reference_or_hazard",
        ),
        Index("ix_priors_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_priors_episode"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 参照类 / 时间型 hazard/survival 参照（架构 §2.3）
    reference_class: Mapped[str | None] = mapped_column(Text)
    hazard_ref: Mapped[str | None] = mapped_column(Text)
    applicability: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sample_rule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    width: Mapped[dict] = mapped_column(JSONB, nullable=False)
    failure_conditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    market_blind_declaration: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")


class EvidenceCoveragePolicy(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """每个 cohort 首个 assignment 前冻结的 evidence coverage policy（架构 §3.3）。"""

    __tablename__ = "evidence_coverage_policies"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id", "policy_version",
            name="uq_evidence_coverage_policies_cohort_version",
        ),
        CheckConstraint("policy_version > 0", name="ck_evidence_coverage_policies_version_positive"),
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_evidence_coverage_policies_content_object",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_evidence_coverage_policies_hash_hex"),
        CheckConstraint(
            "status IN ('draft','active','superseded')",
            name="ck_evidence_coverage_policies_status_known",
        ),
        Index("ix_evidence_coverage_policies_cohort", "cohort_id"),
        {"schema": TRADING_SCHEMA},
    )

    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_evidence_coverage_policies_cohort"),
        nullable=False,
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")


class EvidenceRevision(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """evidence 四时态 revision（架构 §3.2）；append-only。"""

    __tablename__ = "evidence_revisions"
    __table_args__ = (
        UniqueConstraint("episode_id", "revision_key", name="uq_evidence_revisions_episode_key"),
        UniqueConstraint("episode_id", "content_hash", name="uq_evidence_revisions_episode_hash"),
        CheckConstraint("revision_key <> ''", name="ck_evidence_revisions_key_nonempty"),
        CheckConstraint(
            "kind IN ('observation','source_claim','inference','conflict','missing')",
            name="ck_evidence_revisions_kind_known",
        ),
        CheckConstraint(
            "taint_status IN ('none','market','odds','crowd','label','future_fact','quarantined')",
            name="ck_evidence_revisions_taint_known",
        ),
        CheckConstraint(
            "published_at <= observed_at",
            name="ck_evidence_revisions_publish_le_observe",
        ),
        CheckConstraint(
            "observed_at <= ingested_at",
            name="ck_evidence_revisions_observe_le_ingest",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_revisions_hash_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_evidence_revisions_content_object",
        ),
        CheckConstraint(
            "market_conditioned_discovery = false OR taint_status <> 'none'",
            name="ck_evidence_revisions_taint_conditioned_pair",
        ),
        Index("ix_evidence_revisions_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_evidence_revisions_episode"),
        nullable=False,
    )
    revision_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    published_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_revision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.evidence_revisions.id", name="fk_evidence_revisions_prev"),
    )
    raw_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.artifact_objects.id", name="fk_evidence_revisions_raw_artifact"),
        nullable=False,
    )
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    taint_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="none")
    market_conditioned_discovery: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class EvidenceBundle(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """cutoff 后生成的不可变 as-of evidence bundle（架构 §3.3）。"""

    __tablename__ = "evidence_bundles"
    __table_args__ = (
        UniqueConstraint("episode_id", "bundle_key", name="uq_evidence_bundles_episode_key"),
        UniqueConstraint("bundle_hash", name="uq_evidence_bundles_hash"),
        CheckConstraint("bundle_hash ~ '^[0-9a-f]{64}$'", name="ck_evidence_bundles_hash_hex"),
        CheckConstraint(
            "status IN ('draft','frozen')",
            name="ck_evidence_bundles_status_known",
        ),
        Index("ix_evidence_bundles_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_evidence_bundles_episode"),
        nullable=False,
    )
    bundle_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    information_cutoff_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    bundle_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")


class EvidenceBundleItem(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """bundle × eligible revision 引用（架构 §3.3）；item_no 顺序稳定。"""

    __tablename__ = "evidence_bundle_items"
    __table_args__ = (
        UniqueConstraint("bundle_id", "revision_id", name="uq_evidence_bundle_items_bundle_revision"),
        UniqueConstraint("bundle_id", "item_no", name="uq_evidence_bundle_items_bundle_no"),
        CheckConstraint("item_no >= 0", name="ck_evidence_bundle_items_no_nonneg"),
        Index("ix_evidence_bundle_items_bundle", "bundle_id"),
        {"schema": TRADING_SCHEMA},
    )

    bundle_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evidence_bundles.id", name="fk_evidence_bundle_items_bundle"),
        nullable=False,
    )
    revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evidence_revisions.id", name="fk_evidence_bundle_items_revision"),
        nullable=False,
    )
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    eligibility_reason: Mapped[str | None] = mapped_column(String(128))


class ForecastInputManifest(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """完整 as-of 输入包：bundle/spec/schema/prior/taxonomy/model/prompt/code hash（架构 §3.3）。"""

    __tablename__ = "forecast_input_manifests"
    __table_args__ = (
        UniqueConstraint("episode_id", "manifest_key", name="uq_forecast_input_manifests_episode_key"),
        UniqueConstraint("manifest_hash", name="uq_forecast_input_manifests_hash"),
        CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_forecast_input_manifests_hash_hex"),
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_forecast_input_manifests_content_object",
        ),
        Index("ix_forecast_input_manifests_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_forecast_input_manifests_episode"),
        nullable=False,
    )
    manifest_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    evidence_bundle_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    contract_spec_set_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    world_schema_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    prior_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    taxonomy_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    model_binding_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    code_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ForecastSubmission(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """不可变 blind submission；commit 后禁更新/删除（任务 §2.9）。"""

    __tablename__ = "forecast_submissions"
    __table_args__ = (
        UniqueConstraint("episode_id", "submission_key", name="uq_forecast_submissions_episode_key"),
        CheckConstraint(
            "status IN ('DRAFT','BLIND_COMMITTED')",
            name="ck_forecast_submissions_status_known",
        ),
        CheckConstraint(
            "jsonb_typeof(Q) = 'object'",
            name="ck_forecast_submissions_q_object",
        ),
        CheckConstraint(
            "jsonb_typeof(U) = 'array'",
            name="ck_forecast_submissions_u_array",
        ),
        CheckConstraint("jsonb_array_length(U) > 0", name="ck_forecast_submissions_u_nonempty"),
        CheckConstraint(
            "committed_at IS NOT NULL = (status = 'BLIND_COMMITTED')",
            name="ck_forecast_submissions_commit_pair",
        ),
        Index("ix_forecast_submissions_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_forecast_submissions_episode"),
        nullable=False,
    )
    submission_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    # 概念名 Q/U（架构 §1.0）；物理列名 q/u（与迁移 DDL 一致，避免 quoting 漂移）。
    q: Mapped[dict] = mapped_column(JSONB, nullable=False)
    u: Mapped[list] = mapped_column(JSONB, nullable=False)
    forecast_input_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_input_manifests.id", name="fk_forecast_submissions_manifest"),
        nullable=False,
    )
    contract_schema_prior_evidence_hash: Mapped[str] = mapped_column(
        sha256_type(), nullable=False
    )
    algorithm_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class PayoutProjection(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """每个 submission × contract spec × token 的确定性 payout projection（架构 §4.3）。"""

    __tablename__ = "payout_projections"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "contract_spec_id", "pm_token_id",
            name="uq_payout_projections_submission_spec_token",
        ),
        CheckConstraint(
            "jsonb_typeof(mu) = 'object'",
            name="ck_payout_projections_mu_object",
        ),
        CheckConstraint("v >= 0 AND v <= 1", name="ck_payout_projections_v_range"),
        CheckConstraint("u_lower >= 0 AND u_lower <= 1", name="ck_payout_projections_lower_range"),
        CheckConstraint("u_upper >= 0 AND u_upper <= 1", name="ck_payout_projections_upper_range"),
        CheckConstraint("u_lower <= v AND v <= u_upper", name="ck_payout_projections_bounds_order"),
        CheckConstraint(
            "p_blind IS NULL OR (p_blind >= 0 AND p_blind <= 1)",
            name="ck_payout_projections_pblind_range",
        ),
        CheckConstraint("algorithm_hash ~ '^[0-9a-f]{64}$'", name="ck_payout_projections_alg_hash_hex"),
        CheckConstraint("g_hash ~ '^[0-9a-f]{64}$'", name="ck_payout_projections_g_hash_hex"),
        Index("ix_payout_projections_submission", "submission_id"),
        {"schema": TRADING_SCHEMA},
    )

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_submissions.id", name="fk_payout_projections_submission"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_payout_projections_spec"),
        nullable=False,
    )
    pm_token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_payout_projections_token"),
        nullable=False,
    )
    # {payout_decimal_string: probability_decimal_string}
    mu: Mapped[dict] = mapped_column(JSONB, nullable=False)
    v: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    u_lower: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    u_upper: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    p_blind: Mapped[Decimal | None] = mapped_column(base_unit_type())
    algorithm_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    h_c_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    g_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class CoherenceCheck(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """G6 确定性 Q/U/投影校验结果（append-only）。"""

    __tablename__ = "coherence_checks"
    __table_args__ = (
        UniqueConstraint("submission_id", "check_name", name="uq_coherence_checks_submission_check"),
        Index("ix_coherence_checks_submission", "submission_id"),
        {"schema": TRADING_SCHEMA},
    )

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_submissions.id", name="fk_coherence_checks_submission"),
        nullable=False,
    )
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    details_artifact_hash: Mapped[str | None] = mapped_column(sha256_type())


class ForecastChallenge(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """challenger 骨架（append-only；不进入 champion 路径，任务 §2.12）。"""

    __tablename__ = "forecast_challenges"
    __table_args__ = (
        UniqueConstraint("episode_id", "challenge_key", name="uq_forecast_challenges_episode_key"),
        CheckConstraint(
            "status IN ('planned','running','accepted','rejected','superseded')",
            name="ck_forecast_challenges_status_known",
        ),
        Index("ix_forecast_challenges_episode", "episode_id"),
        {"schema": TRADING_SCHEMA},
    )

    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_forecast_challenges_episode"),
        nullable=False,
    )
    challenge_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    challenger_role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="planned")


class ForecastLease(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """forecast lease：valid_until + 结构化 invalidation + evidence/schema/spec hash（架构 §4.4）。"""

    __tablename__ = "forecast_leases"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_forecast_leases_submission"),
        CheckConstraint(
            "jsonb_typeof(invalidation_conditions) = 'object'",
            name="ck_forecast_leases_conditions_object",
        ),
        CheckConstraint("evidence_hash ~ '^[0-9a-f]{64}$'", name="ck_forecast_leases_evidence_hash_hex"),
        CheckConstraint("schema_hash ~ '^[0-9a-f]{64}$'", name="ck_forecast_leases_schema_hash_hex"),
        CheckConstraint("spec_hash ~ '^[0-9a-f]{64}$'", name="ck_forecast_leases_spec_hash_hex"),
        {"schema": TRADING_SCHEMA},
    )

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_submissions.id", name="fk_forecast_leases_submission"),
        nullable=False,
    )
    valid_until: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    invalidation_conditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    schema_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    spec_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
