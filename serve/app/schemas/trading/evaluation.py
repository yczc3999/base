"""Evaluation typed DTO（WP-04 Checkpoint B）。

只表达 typed 输入；strict allowlist（``extra="forbid"``）防任意字段注入。
Logic 决定统计/评价语义；本包只做严格解析/规范化。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

METRIC_RUN_STATUS = ("RUNNING", "COMPLETED", "FAILED", "INVALIDATED")
PROMOTION_TYPES = ("capital", "strategy")
PROMOTION_STATUS = ("APPROVED", "REJECTED", "DEFERRED")
REPLAY_KINDS = ("original", "new_code", "variant")


class ScoreObservationInput(BaseModel):
    """single canonical-target proper-loss observation input。"""

    model_config = ConfigDict(extra="forbid")

    observation_key: str = Field(min_length=1)
    score_target_id: int = Field(gt=0)
    submission_id: int = Field(gt=0)
    trade_decision_id: int | None = Field(default=None, gt=0)
    label_version_id: int = Field(gt=0)
    baseline_quote: Decimal = Field(gt=0)
    baseline_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: str = Field(pattern="^(train|validation|forward_holdout)$")
    algorithm_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_id: str = Field(min_length=1)
    score_value: Decimal = Field(ge=0)


class ExperimentInput(BaseModel):
    """pre-registered experiment input（唯一变化字段 + frozen manifest）。"""

    model_config = ConfigDict(extra="forbid")

    experiment_key: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    hypothesis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_metric: str = Field(min_length=1)
    guardrails: dict = Field(default_factory=dict)
    unique_change_field: str = Field(min_length=1)
    champion_input_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenger_input_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_policy: dict = Field(default_factory=dict)
    stopping_rule: dict = Field(default_factory=dict)
    seed: int = Field(ge=0)
    status: str = Field(default="PLANNED", pattern="^(PLANNED|RUNNING|COMPLETED|INVALIDATED)$")
    time_block_start: datetime
    time_block_end: datetime


class MetricRunInput(BaseModel):
    """metric run 创建输入（RUNNING 起；terminal 由 DB lifecycle guard 约束）。"""

    model_config = ConfigDict(extra="forbid")

    run_key: str = Field(min_length=1)
    cohort_query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_version_id: int = Field(gt=0)
    release_manifest_id: int = Field(gt=0)
    label_versions: dict = Field(default_factory=dict)
    split: str = Field(pattern="^(train|validation|forward_holdout)$")
    time_blocks: dict = Field(default_factory=dict)
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0)
    n_market: int = Field(ge=0)
    n_episode: int = Field(ge=0)
    n_resolution_cluster: int = Field(ge=0)
    n_eff: Decimal = Field(ge=0)
    results: dict = Field(default_factory=dict)
    ci: dict = Field(default_factory=dict)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromotionDecisionInput(BaseModel):
    """promotion 决策输入（capital 恒 fail closed，由 DB CHECK 强制）。"""

    model_config = ConfigDict(extra="forbid")

    promotion_key: str = Field(min_length=1)
    metric_run_id: int = Field(gt=0)
    promotion_type: str = Field(pattern="^(capital|strategy)$")
    from_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    to_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern="^(APPROVED|REJECTED|DEFERRED)$")
    reason_code: str | None = None
    future_effective_at: datetime | None = None
    capital_amount: Decimal = Field(default=Decimal("0"), ge=0)


class ReplayRunInput(BaseModel):
    """科学回放输入（只读原事实，输出新 artifact）。"""

    model_config = ConfigDict(extra="forbid")

    run_key: str = Field(min_length=1)
    replay_kind: str = Field(pattern="^(original|new_code|variant)$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0)
    input_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict | None = None
