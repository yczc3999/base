"""Trading evaluation models（WP-04 Checkpoint B，revision ``b1000040``）。

8 张表：score_observations、experiments、experiment_variants、challenger_variants、
metric_runs、error_reviews、ablation_runs、promotion_decisions。

不变量（任务 §5.3 / §5.4）：
- score observation 必须引用 final_admissible label、exact blind submission/decision、target、
  baseline quote/policy、split 与算法 hash；同一 ``submission × target × label × metric`` 唯一。
- metric run 固定 cohort query、strategy/release、label versions、split、time blocks、
  code/config、seed、n_* 与 n_eff；``(status='COMPLETED') ⇔ completed_at``；RUNNING 起
  ``RUNNING→COMPLETED|FAILED|INVALIDATED``，terminal 后禁改。
- experiment 在 assignment 前冻结 hypothesis、唯一变化项、primary metric、guardrails、
  sample/time/stopping/rollback；champion 与 challenger 的 immutable input manifest 必须不同
  （除唯一变化字段外全等由 logic 校验，DB 至少强制两 hash 不等）。
- promotion 引用单一 metric run；``promotion_type='capital'`` 恒 fail closed（不得 APPROVED）。
- 全部 append-only（immutable trigger 复用 0002 ``v2_reject_immutable_row``）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
    TradingBase,
)
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    probability_type,
    sha256_type,
    utc_timestamp_type,
)

METRIC_RUN_STATUS = ("RUNNING", "COMPLETED", "FAILED", "INVALIDATED")
PROMOTION_TYPES = ("capital", "strategy")
PROMOTION_STATUS = ("APPROVED", "REJECTED", "DEFERRED")


class ScoreObservation(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """单条 canonical target 的 proper-loss 观察（任务 §5.3）。"""

    __tablename__ = "score_observations"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "score_target_id", "label_version_id", "metric_id",
            name="uq_score_observations_submission_target_label_metric",
        ),
        UniqueConstraint("observation_key", name="uq_score_observations_key"),
        CheckConstraint(
            "baseline_quote IS NULL OR (baseline_quote > 0 AND baseline_quote <= 1)",
            name="ck_score_observations_baseline_range",
        ),
        CheckConstraint(
            "baseline_quote_binding_ids IS NULL OR "
            "(jsonb_typeof(baseline_quote_binding_ids) = 'array' "
            "AND jsonb_array_length(baseline_quote_binding_ids) > 0)",
            name="ck_score_observations_binding_ids_array",
        ),
        CheckConstraint(
            "baseline_value IS NULL OR jsonb_typeof(baseline_value) = 'object'",
            name="ck_score_observations_baseline_value_object",
        ),
        CheckConstraint(
            "baseline_value_hash IS NULL OR baseline_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_score_observations_baseline_value_hash_hex",
        ),
        CheckConstraint(
            "split IN ('train','validation','forward_holdout')",
            name="ck_score_observations_split_known",
        ),
        CheckConstraint(
            "status IN ('INCLUDED','EXCLUDED')",
            name="ck_score_observations_status_known",
        ),
        CheckConstraint(
            "(status='INCLUDED' AND exclusion_reason IS NULL "
            " AND baseline_quote_binding_ids IS NOT NULL AND baseline_value IS NOT NULL "
            " AND baseline_value_hash IS NOT NULL AND baseline_checkpoint_received_at IS NOT NULL "
            " AND score_value IS NOT NULL) OR "
            "(status='EXCLUDED' AND exclusion_reason IS NOT NULL "
            " AND baseline_quote IS NULL AND baseline_quote_binding_ids IS NULL "
            " AND baseline_value IS NULL AND baseline_value_hash IS NULL "
            " AND baseline_checkpoint_received_at IS NULL AND score_value IS NULL)",
            name="ck_score_observations_disposition_shape",
        ),
        CheckConstraint(
            "baseline_policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_score_observations_baseline_hash_hex",
        ),
        CheckConstraint(
            "algorithm_hash ~ '^[0-9a-f]{64}$'",
            name="ck_score_observations_algorithm_hash_hex",
        ),
        Index("ix_score_observations_submission", "submission_id"),
        Index("ix_score_observations_target", "score_target_id"),
        {"schema": TRADING_SCHEMA},
    )

    observation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    score_target_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.score_targets.id", name="fk_score_observations_target"),
        nullable=False,
    )
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_submissions.id", name="fk_score_observations_submission"),
        nullable=False,
    )
    trade_decision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_score_observations_decision"),
    )
    label_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.resolution_labels.id", name="fk_score_observations_label"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="INCLUDED")
    exclusion_reason: Mapped[str | None] = mapped_column(String(128))
    baseline_quote_binding_ids: Mapped[list | None] = mapped_column(JSONB)
    baseline_value: Mapped[dict | None] = mapped_column(JSONB)
    baseline_value_hash: Mapped[str | None] = mapped_column(sha256_type())
    baseline_checkpoint_received_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    baseline_quote: Mapped[Decimal | None] = mapped_column(probability_type())
    baseline_policy_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    score_value: Mapped[Decimal | None] = mapped_column(probability_type())


class Experiment(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """预注册实验：唯一变化字段 + frozen manifest（任务 §5.4）。"""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("experiment_key", name="uq_experiments_key"),
        CheckConstraint(
            "status IN ('PLANNED','RUNNING','COMPLETED','INVALIDATED')",
            name="ck_experiments_status_known",
        ),
        CheckConstraint(
            "hypothesis_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiments_hypothesis_hash_hex",
        ),
        CheckConstraint(
            "champion_input_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiments_champion_hash_hex",
        ),
        CheckConstraint(
            "challenger_input_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiments_challenger_hash_hex",
        ),
        CheckConstraint(
            "champion_input_manifest_hash <> challenger_input_manifest_hash",
            name="ck_experiments_distinct_manifests",
        ),
        CheckConstraint(
            "jsonb_typeof(guardrails) = 'object'",
            name="ck_experiments_guardrails_object",
        ),
        CheckConstraint(
            "jsonb_typeof(sample_policy) = 'object'",
            name="ck_experiments_sample_policy_object",
        ),
        CheckConstraint(
            "jsonb_typeof(stopping_rule) = 'object'",
            name="ck_experiments_stopping_rule_object",
        ),
        CheckConstraint(
            "time_block_end > time_block_start",
            name="ck_experiments_block_order",
        ),
        {"schema": TRADING_SCHEMA},
    )

    experiment_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    guardrails: Mapped[dict] = mapped_column(JSONB, nullable=False)
    unique_change_field: Mapped[str] = mapped_column(String(128), nullable=False)
    champion_input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    challenger_input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    sample_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stopping_rule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PLANNED")
    time_block_start: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    time_block_end: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class ExperimentVariant(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """champion/challenger 的 frozen variant（任务 §5.4）。"""

    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "variant_key", name="uq_experiment_variants_experiment_key"),
        CheckConstraint(
            "variant_type IN ('champion','challenger')",
            name="ck_experiment_variants_type_known",
        ),
        CheckConstraint(
            "input_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_experiment_variants_manifest_hex",
        ),
        {"schema": TRADING_SCHEMA},
    )

    experiment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.experiments.id", name="fk_experiment_variants_experiment"),
        nullable=False,
    )
    variant_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    variant_type: Mapped[str] = mapped_column(String(16), nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_experiment_variants_strategy"),
        nullable=False,
    )
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_experiment_variants_release"),
        nullable=False,
    )
class ChallengerVariant(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """challenger 变更清单（ACTIVE|SUPERSEDED；append-only）。"""

    __tablename__ = "challenger_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "variant_key", name="uq_challenger_variants_experiment_key"),
        CheckConstraint(
            "status IN ('ACTIVE','SUPERSEDED')",
            name="ck_challenger_variants_status_known",
        ),
        CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_challenger_variants_policy_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(changed_fields) = 'object'",
            name="ck_challenger_variants_changed_object",
        ),
        {"schema": TRADING_SCHEMA},
    )

    experiment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.experiments.id", name="fk_challenger_variants_experiment"),
        nullable=False,
    )
    variant_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    challenger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")


class MetricRun(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次完整 metric 计算（RUNNING→COMPLETED|FAILED|INVALIDATED；terminal 禁改）。"""

    __tablename__ = "metric_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_metric_runs_key"),
        CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED','INVALIDATED')",
            name="ck_metric_runs_status_known",
        ),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="ck_metric_runs_completed_pair",
        ),
        CheckConstraint(
            "cohort_query_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_cohort_hash_hex",
        ),
        CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_code_hash_hex",
        ),
        CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_config_hash_hex",
        ),
        CheckConstraint(
            "artifact_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_artifact_hash_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(label_versions) = 'object'",
            name="ck_metric_runs_label_versions_object",
        ),
        CheckConstraint(
            "jsonb_typeof(observation_ids) = 'array' AND jsonb_array_length(observation_ids) > 0",
            name="ck_metric_runs_observation_ids_array",
        ),
        CheckConstraint(
            "observation_set_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_observation_set_hash_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(time_blocks) = 'object'",
            name="ck_metric_runs_time_blocks_object",
        ),
        CheckConstraint(
            "jsonb_typeof(results) = 'object'",
            name="ck_metric_runs_results_object",
        ),
        CheckConstraint(
            "jsonb_typeof(ci) = 'object'",
            name="ck_metric_runs_ci_object",
        ),
        CheckConstraint(
            "split IN ('train','validation','forward_holdout')",
            name="ck_metric_runs_split_known",
        ),
        CheckConstraint(
            "n_market >= 0 AND n_episode >= 0 AND n_resolution_cluster > 0 "
            "AND n_eff > 0 AND n_eff <= n_resolution_cluster",
            name="ck_metric_runs_counts_valid",
        ),
        Index("ix_metric_runs_strategy", "strategy_version_id"),
        Index("ix_metric_runs_release", "release_manifest_id"),
        {"schema": TRADING_SCHEMA},
    )

    run_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cohort_query_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_metric_runs_strategy"),
        nullable=False,
    )
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_metric_runs_release"),
        nullable=False,
    )
    cohort_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.evaluation_cohorts.id", name="fk_metric_runs_cohort"),
        nullable=False,
    )
    observation_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    observation_set_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    label_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    time_blocks: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    config_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    n_market: Mapped[int] = mapped_column(Integer, nullable=False)
    n_episode: Mapped[int] = mapped_column(Integer, nullable=False)
    n_resolution_cluster: Mapped[int] = mapped_column(Integer, nullable=False)
    n_eff: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ci: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="RUNNING")
    completed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class ErrorReview(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """top-loss/top-regret/random-success 评审样本（任务 §6.3）。"""

    __tablename__ = "error_reviews"
    __table_args__ = (
        UniqueConstraint("review_key", name="uq_error_reviews_key"),
        CheckConstraint(
            "review_type IN ('top_loss','top_regret','random_success')",
            name="ck_error_reviews_type_known",
        ),
        Index("ix_error_reviews_metric_run", "metric_run_id"),
        {"schema": TRADING_SCHEMA},
    )

    review_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    review_type: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.metric_runs.id", name="fk_error_reviews_metric_run"),
        nullable=False,
    )
    observation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_taxonomy: Mapped[str] = mapped_column(String(128), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AblationRun(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次消融实验（append-only 产物）。"""

    __tablename__ = "ablation_runs"
    __table_args__ = (
        UniqueConstraint("ablation_key", name="uq_ablation_runs_key"),
        CheckConstraint(
            "bundle_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ablation_runs_bundle_hash_hex",
        ),
        CheckConstraint(
            "result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ablation_runs_result_hash_hex",
        ),
        {"schema": TRADING_SCHEMA},
    )

    ablation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    metric_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.metric_runs.id", name="fk_ablation_runs_metric_run"),
        nullable=False,
    )
    bundle_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    ablation_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class PromotionDecision(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """promotion 决策（capital 恒 fail closed；append-only）。"""

    __tablename__ = "promotion_decisions"
    __table_args__ = (
        UniqueConstraint("promotion_key", name="uq_promotion_decisions_key"),
        CheckConstraint(
            "promotion_type IN ('capital','strategy')",
            name="ck_promotion_decisions_type_known",
        ),
        CheckConstraint(
            "status IN ('APPROVED','REJECTED','DEFERRED')",
            name="ck_promotion_decisions_status_known",
        ),
        CheckConstraint(
            "NOT (promotion_type = 'capital' AND status = 'APPROVED')",
            name="ck_promotion_decisions_capital_never_approved",
        ),
        CheckConstraint("from_ref <> to_ref", name="ck_promotion_decisions_distinct_refs"),
        CheckConstraint(
            "(status = 'APPROVED') = (future_effective_at IS NOT NULL)",
            name="ck_promotion_decisions_future_pair",
        ),
        CheckConstraint(
            "(status = 'APPROVED' AND reason_code IS NULL) OR "
            "(status IN ('REJECTED','DEFERRED') AND reason_code IS NOT NULL)",
            name="ck_promotion_decisions_reason_pair",
        ),
        CheckConstraint("capital_amount = 0", name="ck_promotion_decisions_wp04_zero_capital"),
        CheckConstraint(
            "from_ref ~ '^[0-9a-f]{64}$'",
            name="ck_promotion_decisions_from_hex",
        ),
        CheckConstraint(
            "to_ref ~ '^[0-9a-f]{64}$'",
            name="ck_promotion_decisions_to_hex",
        ),
        CheckConstraint(
            "evidence_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_promotion_decisions_evidence_hex",
        ),
        Index("ix_promotion_decisions_metric_run", "metric_run_id"),
        {"schema": TRADING_SCHEMA},
    )

    promotion_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    metric_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.metric_runs.id", name="fk_promotion_decisions_metric_run"),
        nullable=False,
    )
    promotion_type: Mapped[str] = mapped_column(String(16), nullable=False)
    from_ref: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    to_ref: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    evidence_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    future_effective_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    capital_amount: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
