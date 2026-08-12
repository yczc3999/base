"""Trading ledger models（WP-03 Checkpoint C，revision ``b1000031``）。

3 张表：ledger_transactions、ledger_postings、operating_cost_entries。

不变量（任务 §4.13 / §5.4）：
- ledger posting 使用整数 base units；POSTED 前每个 (asset_type, asset_key) signed postings
  合计为 0 且至少两条，由 deferred trigger/封账函数强制。
- posted transaction/posting 禁 UPDATE/DELETE；纠错只写 exact reversal。
- operating cost append-only，类别仅 DATA|LLM|SEARCH|INFRASTRUCTURE|HUMAN|OPERATIONAL_LOSS；
  禁止把缺失成本写成 0。
- 系统收益三层：trading_pnl、operating_cost、system_net_profit。
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
    TradingBase,
)
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

LEDGER_STATUS = ("PENDING", "POSTED")
OPERATING_COST_KINDS = (
    "DATA", "LLM", "SEARCH", "INFRASTRUCTURE", "HUMAN", "OPERATIONAL_LOSS"
)


class LedgerTransaction(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一笔账务交易（一组 postings 的容器；POSTED 后不可变）。"""

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("transaction_key", name="uq_ledger_transactions_key"),
        CheckConstraint(
            "status IN ('PENDING','POSTED')",
            name="ck_ledger_transactions_status_known",
        ),
        CheckConstraint(
            "posted_at IS NOT NULL = (status = 'POSTED')",
            name="ck_ledger_transactions_posted_pair",
        ),
        CheckConstraint(
            "kind IN ('FILL','REVERSAL','SETTLEMENT','OPERATING_COST')",
            name="ck_ledger_transactions_kind_known",
        ),
        CheckConstraint(
            "(kind = 'REVERSAL') = (reference_transaction_id IS NOT NULL)",
            name="ck_ledger_transactions_reversal_ref_pair",
        ),
        Index("ix_ledger_transactions_decision", "trade_decision_id"),
        Index("ix_ledger_transactions_execution", "execution_id"),
        Index("ix_ledger_transactions_chain_operation", "chain_operation_id"),
        {"schema": TRADING_SCHEMA},
    )

    transaction_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_decision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_ledger_transactions_decision"),
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.executions.id", name="fk_ledger_transactions_execution"),
    )
    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    reference_transaction_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.ledger_transactions.id", name="fk_ledger_transactions_reversal_ref"),
    )
    # WP-05 Checkpoint C lineage：account/envelope/order/trade 引用（shadow 旧行可为 NULL）
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_ledger_transactions_account"),
    )
    envelope_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.execution_authorization_envelopes.id",
            name="fk_ledger_transactions_envelope",
        ),
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_ledger_transactions_exchange_order"),
    )
    trade_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_trades.id", name="fk_ledger_transactions_exchange_trade"),
    )
    # WP-06 Checkpoint B lineage：chain settlement 记账引用（FINALIZED 经济 effect 唯一）
    chain_operation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.chain_operations.id", name="fk_ledger_transactions_chain_operation"),
    )


class LedgerPosting(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """双分录 posting（base-unit signed；POSTED 前按 asset 归零）。"""

    __tablename__ = "ledger_postings"
    __table_args__ = (
        UniqueConstraint("transaction_id", "posting_no", name="uq_ledger_postings_tx_no"),
        CheckConstraint(
            "asset_type IN ('CASH','TOKEN')",
            name="ck_ledger_postings_asset_type_known",
        ),
        CheckConstraint(
            "amount <> 0",
            name="ck_ledger_postings_amount_nonzero",
        ),
        CheckConstraint("posting_no >= 0", name="ck_ledger_postings_no_nonneg"),
        Index("ix_ledger_postings_asset", "asset_type", "asset_key"),
        {"schema": TRADING_SCHEMA},
    )

    transaction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.ledger_transactions.id", name="fk_ledger_postings_transaction"),
        nullable=False,
    )
    posting_no: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    counterparty: Mapped[str] = mapped_column(external_id_type(), nullable=False)


class OperatingCostEntry(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """allocated operating cost（append-only；禁止把缺失成本写成 0）。"""

    __tablename__ = "operating_cost_entries"
    __table_args__ = (
        UniqueConstraint("cost_key", name="uq_operating_cost_entries_key"),
        CheckConstraint(
            "cost_kind IN ('DATA','LLM','SEARCH','INFRASTRUCTURE','HUMAN','OPERATIONAL_LOSS')",
            name="ck_operating_cost_entries_kind_known",
        ),
        CheckConstraint("amount >= 0", name="ck_operating_cost_entries_amount_nonneg"),
        CheckConstraint(
            "jsonb_typeof(allocation_policy) = 'object'",
            name="ck_operating_cost_entries_allocation_object",
        ),
        {"schema": TRADING_SCHEMA},
    )

    cost_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    release_manifest_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_operating_cost_entries_release"),
    )
    episode_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_episodes.id", name="fk_operating_cost_entries_episode"),
    )
    trade_decision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.trade_decisions.id", name="fk_operating_cost_entries_decision"),
    )
    period_start: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    period_end: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    allocation_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
