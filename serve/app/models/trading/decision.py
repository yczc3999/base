"""Trading decision models（WP-03 Checkpoint B，revision ``b1000030``）。

9 张表：market_relative_decisions、discrepancy_reviews、trade_decisions、
action_candidates、resolution_cashflows、action_sets、action_set_legs、
underwriting_plans、economic_action_intents。

不变量（任务 §4 / 架构 §5、§6）：
- trade_decision 状态严格 ``CREATED→QUOTE_BOUND→G7A→G7B→ACTION|WAIT|ABSTAIN``，三结果 terminal；
  HOLD/RISK_REVIEW 不另造 status。episode/submission/lease/objective/strategy/release/
  execution spec/permission 全等。
- action_candidates 的 token 属于 episode exact contract spec set；resolution_cashflows 的
  world state 属于 component schema。
- action_set 至少 1 leg（非 HOLD ACTION）；HOLD 零 leg 且须已有 position；WAIT/ABSTAIN 无 action set。
- ``(action_set_id,contract_spec_id,token_id,leg_role)`` 唯一；quantity>0；BUY/ADD 正 exposure，
  REDUCE/CLOSE 负，FLIP close/open 成对。
- terminal decision/action/cashflow/underwriting/intent 禁 UPDATE/DELETE。
- economic_action_intent 的 intent hash 通过非分区 idempotency_claims 全局唯一。
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
    decimal_measure_type,
    external_id_type,
    probability_type,
    sha256_type,
    utc_timestamp_type,
)

DECISION_STATUS = ("CREATED", "QUOTE_BOUND", "G7A", "G7B", "ACTION", "WAIT", "ABSTAIN")
DECISION_MODE = ("BLIND_ONLY", "LINEAR_SHRINKAGE")
DECISION_CLASS = ("CHAMPION", "RISK_REVIEW")
ACTION_TYPES = (
    "BUY_TOKEN", "ADD_TOKEN", "SELL_TOKEN_TO_REDUCE", "SELL_TOKEN_TO_CLOSE",
    "HOLD", "FLIP",
)
LEG_ROLES = ("open", "close", "reduce")


class MarketRelativeDecision(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """揭价后的 market-relative belief（每 trade decision 至多一条；不覆盖 blind submission）。"""

    __tablename__ = "market_relative_decisions"
    __table_args__ = (
        UniqueConstraint("trade_decision_id", name="uq_market_relative_decisions_trade_decision"),
        CheckConstraint(
            "decision_mode IN ('BLIND_ONLY','LINEAR_SHRINKAGE')",
            name="ck_market_relative_decisions_mode_known",
        ),
        CheckConstraint(
            "w_blind IS NULL OR (w_blind >= 0 AND w_blind <= 1)",
            name="ck_market_relative_decisions_w_range",
        ),
        CheckConstraint(
            "jsonb_typeof(q_blind) = 'object'",
            name="ck_market_relative_decisions_q_blind_object",
        ),
        CheckConstraint(
            "jsonb_typeof(q_decision) = 'object'",
            name="ck_market_relative_decisions_q_decision_object",
        ),
        CheckConstraint(
            "jsonb_typeof(u_decision) = 'array'",
            name="ck_market_relative_decisions_u_decision_array",
        ),
        CheckConstraint(
            "jsonb_typeof(token_gaps) = 'object'",
            name="ck_market_relative_decisions_gaps_object",
        ),
        CheckConstraint(
            "jsonb_typeof(reference_identifiability) = 'object'",
            name="ck_market_relative_decisions_reference_object",
        ),
        {"schema": TRADING_SCHEMA},
    )

    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_market_relative_decisions_decision"),
        nullable=False,
    )
    decision_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    w_blind: Mapped[Decimal | None] = mapped_column(probability_type())
    q_blind: Mapped[dict] = mapped_column(JSONB, nullable=False)
    q_decision: Mapped[dict] = mapped_column(JSONB, nullable=False)
    u_decision: Mapped[list] = mapped_column(JSONB, nullable=False)
    u_blind_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    u_decision_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    token_gaps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reference_identifiability: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    output_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class DiscrepancyReview(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """确定性 reveal 检查与结构化 reason（本期不调 LLM、不静默改 belief）。"""

    __tablename__ = "discrepancy_reviews"
    __table_args__ = (
        UniqueConstraint("trade_decision_id", "review_key", name="uq_discrepancy_reviews_decision_key"),
        CheckConstraint(
            "kind IN ('materiality','staleness','rule_schema','book_integrity')",
            name="ck_discrepancy_reviews_kind_known",
        ),
        CheckConstraint(
            "result IN ('PASS','FAIL')",
            name="ck_discrepancy_reviews_result_known",
        ),
        CheckConstraint(
            "jsonb_typeof(findings) = 'object'",
            name="ck_discrepancy_reviews_findings_object",
        ),
        {"schema": TRADING_SCHEMA},
    )

    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_discrepancy_reviews_decision"),
        nullable=False,
    )
    review_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    findings: Mapped[dict] = mapped_column(JSONB, nullable=False)


class TradeDecision(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """揭价后的决策主表；状态机 CREATED→QUOTE_BOUND→G7A→G7B→ACTION|WAIT|ABSTAIN。"""

    __tablename__ = "trade_decisions"
    __table_args__ = (
        UniqueConstraint("decision_key", name="uq_trade_decisions_key"),
        CheckConstraint(
            "status IN ('CREATED','QUOTE_BOUND','G7A','G7B','ACTION','WAIT','ABSTAIN')",
            name="ck_trade_decisions_status_known",
        ),
        CheckConstraint(
            "decision_class IN ('CHAMPION','RISK_REVIEW')",
            name="ck_trade_decisions_class_known",
        ),
        CheckConstraint(
            "experiment_variant <> ''",
            name="ck_trade_decisions_variant_nonempty",
        ),
        CheckConstraint(
            "quote_bound_at IS NOT NULL = (status IN ('QUOTE_BOUND','G7A','G7B','ACTION','WAIT','ABSTAIN'))",
            name="ck_trade_decisions_quote_bound_pair",
        ),
        CheckConstraint(
            "(status IN ('ACTION','WAIT','ABSTAIN')) = (decided_at IS NOT NULL)",
            name="ck_trade_decisions_terminal_pair",
        ),
        CheckConstraint(
            "quote_bound_at IS NULL OR "
            "(trigger_at < quote_bound_at AND "
            " (decided_at IS NULL OR quote_bound_at <= decided_at))",
            name="ck_trade_decisions_timeline_order",
        ),
        CheckConstraint(
            "status <> 'ABSTAIN' OR reason_code IS NOT NULL",
            name="ck_trade_decisions_abstain_reason",
        ),
        CheckConstraint(
            "selected_action_type IS NULL OR selected_action_type IN "
            "('BUY_TOKEN','ADD_TOKEN','SELL_TOKEN_TO_REDUCE','SELL_TOKEN_TO_CLOSE','HOLD','FLIP')",
            name="ck_trade_decisions_selected_action_known",
        ),
        Index("ix_trade_decisions_episode", "episode_id"),
        Index("ix_trade_decisions_submission", "forecast_submission_id"),
        {"schema": TRADING_SCHEMA},
    )

    decision_key: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    episode_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_trade_decisions_episode"),
        nullable=False,
    )
    forecast_submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_submissions.id", name="fk_trade_decisions_submission"),
        nullable=False,
    )
    forecast_lease_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_leases.id", name="fk_trade_decisions_lease"),
        nullable=False,
    )
    objective_contract_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_objective_contracts.id", name="fk_trade_decisions_objective"),
        nullable=False,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_trade_decisions_strategy"),
        nullable=False,
    )
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_trade_decisions_release"),
        nullable=False,
    )
    execution_spec_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.execution_spec_versions.id", name="fk_trade_decisions_exec_spec"),
        nullable=False,
    )
    capital_permission_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.capital_permission_manifests.id", name="fk_trade_decisions_capital"),
        nullable=False,
    )
    experiment_variant: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_class: Mapped[str] = mapped_column(String(32), nullable=False, server_default="CHAMPION")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="CREATED")
    selected_action_type: Mapped[str | None] = mapped_column(String(32))
    trigger_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    quote_bound_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    decided_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    input_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(sha256_type())
    reason_code: Mapped[str | None] = mapped_column(String(128))


class ActionCandidate(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """单个 token/action 的可执行深度与全成本价值（任务 §4.6 / 架构 §5.2）。"""

    __tablename__ = "action_candidates"
    __table_args__ = (
        UniqueConstraint(
            "trade_decision_id", "contract_spec_id", "token_id", "action_type",
            name="uq_action_candidates_decision_token_action",
        ),
        CheckConstraint(
            "action_type IN ('BUY_TOKEN','ADD_TOKEN','SELL_TOKEN_TO_REDUCE',"
            "'SELL_TOKEN_TO_CLOSE','HOLD','FLIP')",
            name="ck_action_candidates_action_known",
        ),
        CheckConstraint(
            "jsonb_typeof(executable_depth) = 'object'",
            name="ck_action_candidates_depth_object",
        ),
        CheckConstraint(
            "jsonb_typeof(cost_components) = 'object'",
            name="ck_action_candidates_cost_object",
        ),
        CheckConstraint(
            "cashflow_reconciliation_residual = 0",
            name="ck_action_candidates_recon_zero",
        ),
        CheckConstraint(
            "fill_quantity >= 0 AND vwap > 0",
            name="ck_action_candidates_fill_positive",
        ),
        CheckConstraint(
            "gross_edge IS NULL OR gross_edge >= 0",
            name="ck_action_candidates_gross_nonneg",
        ),
        Index("ix_action_candidates_decision", "trade_decision_id"),
        {"schema": TRADING_SCHEMA},
    )

    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_action_candidates_decision"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_action_candidates_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_action_candidates_token"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # executable depth walk
    fill_quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    vwap: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)
    executable_depth: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cost_components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cashflow_reconciliation_residual: Mapped[Decimal] = mapped_column(
        base_unit_type(), nullable=False, server_default="0"
    )
    # valuation
    gross_edge: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    break_even_payout_probability: Mapped[Decimal | None] = mapped_column(probability_type())
    net_edge: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    robust_ev: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    point_ev: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    roi: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    expected_log_growth: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    worst_loss: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    capital_days: Mapped[Decimal | None] = mapped_column(decimal_measure_type())
    edge_delay_erosion: Mapped[Decimal | None] = mapped_column(decimal_measure_type())


class ResolutionCashflow(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """action candidate 的逐 world-state cashflow（world state 属于 component schema）。"""

    __tablename__ = "resolution_cashflows"
    __table_args__ = (
        UniqueConstraint(
            "action_candidate_id", "world_state_id",
            name="uq_resolution_cashflows_candidate_state",
        ),
        Index("ix_resolution_cashflows_candidate", "action_candidate_id"),
        {"schema": TRADING_SCHEMA},
    )

    action_candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.action_candidates.id", name="fk_resolution_cashflows_candidate"),
        nullable=False,
    )
    world_state_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cashflow: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    signed_flag: Mapped[str] = mapped_column(String(8), nullable=False)


class ActionSet(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """被选择的完整 condition/component action（原子、不可变）。"""

    __tablename__ = "action_sets"
    __table_args__ = (
        UniqueConstraint("action_set_key", name="uq_action_sets_key"),
        UniqueConstraint("trade_decision_id", name="uq_action_sets_trade_decision"),
        CheckConstraint(
            "disposition IN ('ACTION','WAIT','ABSTAIN')",
            name="ck_action_sets_disposition_known",
        ),
        CheckConstraint(
            "disposition <> 'ABSTAIN' OR reason_code IS NOT NULL",
            name="ck_action_sets_abstain_reason",
        ),
        CheckConstraint(
            "disposition <> 'WAIT' OR (wake_condition IS NOT NULL OR recheck_at IS NOT NULL)",
            name="ck_action_sets_wait_wake",
        ),
        {"schema": TRADING_SCHEMA},
    )

    action_set_key: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_action_sets_decision"),
        nullable=False,
    )
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    wake_condition: Mapped[str | None] = mapped_column(String(255))
    recheck_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    action_set_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class ActionSetLeg(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """action set 的 leg；FLIP close/open 成对，BUY/ADD 正 exposure，REDUCE/CLOSE 负。"""

    __tablename__ = "action_set_legs"
    __table_args__ = (
        UniqueConstraint(
            "action_set_id", "contract_spec_id", "token_id", "leg_role",
            name="uq_action_set_legs_set_spec_token_role",
        ),
        CheckConstraint(
            "leg_role IN ('open','close','reduce')",
            name="ck_action_set_legs_role_known",
        ),
        CheckConstraint("quantity > 0", name="ck_action_set_legs_quantity_positive"),
        CheckConstraint(
            "(leg_role = 'open' AND signed_quantity = quantity) OR "
            "(leg_role IN ('close','reduce') AND signed_quantity = -quantity)",
            name="ck_action_set_legs_role_sign",
        ),
        Index("ix_action_set_legs_set", "action_set_id"),
        {"schema": TRADING_SCHEMA},
    )

    action_set_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.action_sets.id", name="fk_action_set_legs_set"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_action_set_legs_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_action_set_legs_token"),
        nullable=False,
    )
    leg_role: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    signed_quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    entry_vwap: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)


class UnderwritingPlan(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """预承诺承保计划（架构 §6.3）。"""

    __tablename__ = "underwriting_plans"
    __table_args__ = (
        UniqueConstraint("trade_decision_id", "plan_version", name="uq_underwriting_plans_decision_version"),
        CheckConstraint("plan_version > 0", name="ck_underwriting_plans_version_positive"),
        CheckConstraint(
            "jsonb_typeof(entry_range) = 'object'",
            name="ck_underwriting_plans_entry_range_object",
        ),
        CheckConstraint(
            "jsonb_typeof(invalidation) = 'object'",
            name="ck_underwriting_plans_invalidation_object",
        ),
        CheckConstraint("thesis_hash ~ '^[0-9a-f]{64}$'", name="ck_underwriting_plans_thesis_hex"),
        {"schema": TRADING_SCHEMA},
    )

    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_underwriting_plans_decision"),
        nullable=False,
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_range: Mapped[dict] = mapped_column(JSONB, nullable=False)
    hold_to_resolution: Mapped[bool] = mapped_column(Boolean, nullable=False)
    thesis_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    invalidation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    wake_condition: Mapped[str | None] = mapped_column(String(255))
    edge_close_threshold: Mapped[Decimal | None] = mapped_column(probability_type())
    time_stop_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())


class EconomicActionIntent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """mode-independent 不可变 intent；intent hash 全局唯一（idempotency_claims）。"""

    __tablename__ = "economic_action_intents"
    __table_args__ = (
        UniqueConstraint("intent_key", name="uq_economic_action_intents_key"),
        UniqueConstraint("intent_hash", name="uq_economic_action_intents_hash"),
        CheckConstraint("intent_hash ~ '^[0-9a-f]{64}$'", name="ck_economic_action_intents_hash_hex"),
        CheckConstraint(
            "status IN ('PLANNED','COMMITTED','SUPERSEDED')",
            name="ck_economic_action_intents_status_known",
        ),
        CheckConstraint(
            "jsonb_typeof(preflight) = 'object'",
            name="ck_economic_action_intents_preflight_object",
        ),
        CheckConstraint(
            "ttl_at IS NULL OR ttl_at > created_at",
            name="ck_economic_action_intents_ttl_future",
        ),
        Index("ix_economic_action_intents_action_set", "action_set_id"),
        {"schema": TRADING_SCHEMA},
    )

    intent_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    intent_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    trade_decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_economic_action_intents_decision"),
        nullable=False,
    )
    action_set_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.action_sets.id", name="fk_economic_action_intents_action_set"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PLANNED")
    ttl_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    preflight: Mapped[dict] = mapped_column(JSONB, nullable=False)
