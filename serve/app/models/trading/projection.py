"""Trading read projection models（WP-04 Checkpoint D，revision ``b1000041``）。

5 张可重建只读投影：ops_health_current / pipeline_funnel_hourly /
account_risk_current / provider_cost_daily / latest_chain_summary。

不变量（任务 §5.5 / §5.6）：
- 每行保存 ``as_of / source_high_watermark / projection_version / projection_hash``；
  consumer 幂等，乱序/重复 event effect=0。
- 每行 INSERT 后禁止 UPDATE/DELETE（append-only 投影行；新快照=新行）。
- 投影不是事实源：无 FK 引用业务表，不连账本/label/permission；重建 = 整表清空重插。
- keyset 固定 ``(as_of, id)``；filter/sort 走 typed allowlist；禁止 OFFSET、深页 COUNT(*)、
  raw artifact/prompt/book levels/大 JSON 默认加载。
- 金额/概率用 ``NUMERIC(38,12)``；时间用 ``TIMESTAMPTZ``；hash 用 ``VARCHAR(64) COLLATE "C"``。
  禁止 float 与 naive datetime。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import BigIntIdentityMixin, CreatedAtMixin, TradingBase
from app.models.trading.types import (
    decimal_measure_type,
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

# pipeline_funnel_hourly 的 stage 固定枚举（universe→screen→cohort→episode→forecast→decision→execution）。
PROJECTION_STAGES = (
    "universe", "screen", "cohort", "episode", "forecast", "decision", "execution",
)
# ops_health_current 的健康状态枚举。
HEALTH_STATUS = ("ok", "degraded", "stale", "error")
# provider_cost_daily 的 cost_kind 白名单（与 operating_cost_entries 一致）。
PROVIDER_COST_KINDS = (
    "DATA", "LLM", "SEARCH", "INFRASTRUCTURE", "HUMAN", "OPERATIONAL_LOSS",
)


class OpsHealthCurrent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """运维健康当前投影（metric_name × as_of 唯一；append-only 行）。"""

    __tablename__ = "ops_health_current"
    __table_args__ = (
        UniqueConstraint(
            "metric_name", "as_of", name="uq_ops_health_current_metric_asof"
        ),
        CheckConstraint(
            "status IN ('ok','degraded','stale','error')",
            name="ck_ops_health_current_status_known",
        ),
        Index("ix_ops_health_current_as_of", "as_of", "id"),
        {"schema": TRADING_SCHEMA},
    )

    metric_name: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class PipelineFunnelHourly(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """pipeline 漏斗按小时计数（stage × hour_start 唯一）。"""

    __tablename__ = "pipeline_funnel_hourly"
    __table_args__ = (
        UniqueConstraint(
            "stage", "hour_start", name="uq_pipeline_funnel_hourly_stage_hour"
        ),
        CheckConstraint(
            "stage IN ('universe','screen','cohort','episode','forecast','decision','execution')",
            name="ck_pipeline_funnel_hourly_stage_known",
        ),
        CheckConstraint("event_count >= 0", name="ck_pipeline_funnel_hourly_count_nonneg"),
        Index("ix_pipeline_funnel_hourly_as_of", "as_of", "id"),
        {"schema": TRADING_SCHEMA},
    )

    hour_start: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class AccountRiskCurrent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """账户风险当前投影（WP-04 仅 shadow portfolio namespace）。"""

    __tablename__ = "account_risk_current"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_namespace", "market_id", "component_id",
            name="uq_account_risk_current_namespace_market_component",
        ),
        CheckConstraint(
            "exposure >= 0", name="ck_account_risk_current_exposure_nonneg"
        ),
        CheckConstraint(
            "net_risk_capital >= 0", name="ck_account_risk_current_net_risk_nonneg"
        ),
        CheckConstraint(
            "capital_days >= 0", name="ck_account_risk_current_capital_days_nonneg"
        ),
        Index("ix_account_risk_current_as_of", "as_of", "id"),
        {"schema": TRADING_SCHEMA},
    )

    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    market_id: Mapped[int | None] = mapped_column(BigInteger)
    component_id: Mapped[int | None] = mapped_column(BigInteger)
    exposure: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    net_risk_capital: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    cvar: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    capital_days: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class ProviderCostDaily(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """provider 成本按日聚合（provider × cost_kind × cost_date 唯一）。"""

    __tablename__ = "provider_cost_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider", "cost_kind", "cost_date",
            name="uq_provider_cost_daily_provider_kind_date",
        ),
        CheckConstraint(
            "cost_kind IN ('DATA','LLM','SEARCH','INFRASTRUCTURE','HUMAN','OPERATIONAL_LOSS')",
            name="ck_provider_cost_daily_kind_known",
        ),
        CheckConstraint("amount >= 0", name="ck_provider_cost_daily_amount_nonneg"),
        Index("ix_provider_cost_daily_as_of", "as_of", "id"),
        {"schema": TRADING_SCHEMA},
    )

    cost_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class LatestChainSummary(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """最新链摘要（chain_key × period_end 唯一）。"""

    __tablename__ = "latest_chain_summary"
    __table_args__ = (
        UniqueConstraint(
            "chain_key", "period_end", name="uq_latest_chain_summary_key_period"
        ),
        Index("ix_latest_chain_summary_as_of", "as_of", "id"),
        {"schema": TRADING_SCHEMA},
    )

    chain_key: Mapped[str] = mapped_column(String(128, collation="C"), nullable=False)
    chain_value: Mapped[Decimal] = mapped_column(decimal_measure_type(), nullable=False)
    period_end: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    source_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
