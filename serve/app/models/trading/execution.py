"""Trading shadow execution models（WP-03 Checkpoint C，revision ``b1000031``）。

3 张表：executions、positions、position_lots。

不变量（任务 §4.12 / §5.4）：
- execution 状态只允许 ``PENDING→PARTIAL|FILLED|REJECTED|FAILED``，四结果 terminal。
- execution/lot/ledger/outbox 同一 UoW；partial/failed 只影响实际 shadow fill。
- positions 是可重建 current projection（乐观锁更新）；position_lots 与 fill 事实 append-only。
- 本期仅 shadow：无 canary/live、无真实 order、无凭据。
- 不提前创建 pm_accounts/balance/authorization envelope/exchange order（属 WP-05）。
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
    OptimisticVersionMixin,
    TimestampMixin,
    TradingBase,
)
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

EXECUTION_STATUS = ("PENDING", "PARTIAL", "FILLED", "REJECTED", "FAILED")
FILL_ROLE = ("open", "close", "reduce")


class Execution(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次 shadow fill（PENDING→PARTIAL|FILLED|REJECTED|FAILED）。"""

    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("execution_key", name="uq_executions_key"),
        CheckConstraint(
            "status IN ('PENDING','PARTIAL','FILLED','REJECTED','FAILED')",
            name="ck_executions_status_known",
        ),
        CheckConstraint(
            "(status IN ('PENDING','REJECTED','FAILED')) = (filled_quantity = 0)",
            name="ck_executions_filled_pair",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_executions_quantity_positive",
        ),
        CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity",
            name="ck_executions_filled_range",
        ),
        CheckConstraint(
            "vwap IS NULL OR vwap > 0",
            name="ck_executions_vwap_positive",
        ),
        CheckConstraint(
            "fee >= 0",
            name="ck_executions_fee_nonneg",
        ),
        CheckConstraint(
            "unfilled_reason IS NOT NULL = (filled_quantity < quantity AND status <> 'PENDING')",
            name="ck_executions_unfilled_pair",
        ),
        Index("ix_executions_intent", "economic_action_intent_id"),
        {"schema": TRADING_SCHEMA},
    )

    execution_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    economic_action_intent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.economic_action_intents.id", name="fk_executions_intent"),
        nullable=False,
    )
    action_set_leg_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.action_set_legs.id", name="fk_executions_leg"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_executions_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_executions_token"),
        nullable=False,
    )
    fill_role: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    vwap: Mapped[Decimal | None] = mapped_column(base_unit_type())
    fee: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    unfilled_reason: Mapped[str | None] = mapped_column(String(128))
    quote_checkpoint_id: Mapped[int | None] = mapped_column(BigInteger)
    # 组合 namespace（不同 shadow variant 不互相合并 PnL/风险）
    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)


class Position(TradingBase, BigIntIdentityMixin, OptimisticVersionMixin, TimestampMixin):
    """可重建 current position projection（乐观锁；可由 lots 重建）。"""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_namespace", "contract_spec_id", "token_id",
            name="uq_positions_namespace_spec_token",
        ),
        CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonneg"),
        CheckConstraint("cost_basis >= 0", name="ck_positions_cost_basis_nonneg"),
        {"schema": TRADING_SCHEMA},
    )

    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_positions_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_positions_token"),
        nullable=False,
    )
    market_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_positions_market"),
    )
    component_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_components.id", name="fk_positions_component"),
    )
    quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    cost_basis: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")


class PositionLot(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """不可变 fill lot（position 重建来源；append-only）。"""

    __tablename__ = "position_lots"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_position_lots_execution"),
        CheckConstraint("quantity > 0", name="ck_position_lots_quantity_positive"),
        CheckConstraint(
            "entry_vwap IS NULL OR entry_vwap > 0",
            name="ck_position_lots_vwap_positive",
        ),
        Index("ix_position_lots_position", "portfolio_namespace", "contract_spec_id", "token_id"),
        {"schema": TRADING_SCHEMA},
    )

    execution_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.executions.id", name="fk_position_lots_execution"),
        nullable=False,
    )
    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_position_lots_spec"),
        nullable=False,
    )
    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_position_lots_token"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    entry_vwap: Mapped[Decimal | None] = mapped_column(base_unit_type())
    fill_role: Mapped[str] = mapped_column(String(16), nullable=False)
