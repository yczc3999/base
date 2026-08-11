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
    func,
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
    probability_type,
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
        UniqueConstraint(
            "economic_action_intent_id", "action_set_leg_id",
            name="uq_executions_intent_leg",
        ),
        CheckConstraint(
            "status IN ('PENDING','PARTIAL','FILLED','REJECTED','FAILED')",
            name="ck_executions_status_known",
        ),
        CheckConstraint(
            "fill_role IN ('open','close','reduce')",
            name="ck_executions_fill_role_known",
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
        UniqueConstraint(
            "order_id", "trade_id",
            name="uq_executions_outer_lineage",
        ),
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
    vwap: Mapped[Decimal | None] = mapped_column(probability_type())
    fee: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    unfilled_reason: Mapped[str | None] = mapped_column(String(128))
    quote_checkpoint_id: Mapped[int | None] = mapped_column(BigInteger)
    # 组合 namespace（不同 shadow variant 不互相合并 PnL/风险）
    portfolio_namespace: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    # WP-05 Checkpoint C lineage：account/envelope/order/trade 引用（shadow 旧行可为 NULL）
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_executions_account"),
    )
    envelope_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.execution_authorization_envelopes.id", name="fk_executions_envelope"),
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_executions_exchange_order"),
    )
    trade_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_trades.id", name="fk_executions_exchange_trade"),
    )


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
    # WP-05 Checkpoint C lineage
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_positions_account"),
    )
    envelope_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.execution_authorization_envelopes.id", name="fk_positions_envelope"),
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_positions_exchange_order"),
    )


class PositionLot(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """不可变 fill lot（position 重建来源；append-only）。"""

    __tablename__ = "position_lots"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_position_lots_execution"),
        CheckConstraint("quantity <> 0", name="ck_position_lots_quantity_nonzero"),
        CheckConstraint(
            "(fill_role = 'open' AND quantity > 0) OR "
            "(fill_role IN ('close','reduce') AND quantity < 0)",
            name="ck_position_lots_role_sign",
        ),
        CheckConstraint(
            "fill_role IN ('open','close','reduce')",
            name="ck_position_lots_fill_role_known",
        ),
        CheckConstraint(
            "entry_vwap IS NULL OR entry_vwap > 0",
            name="ck_position_lots_vwap_positive",
        ),
        Index("ix_position_lots_position", "portfolio_namespace", "contract_spec_id", "token_id"),
        {"schema": TRADING_SCHEMA},
    )

    execution_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.executions.id", name="fk_position_lots_execution"),
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
    entry_vwap: Mapped[Decimal | None] = mapped_column(probability_type())
    fill_role: Mapped[str] = mapped_column(String(16), nullable=False)
    # WP-05 Checkpoint C lineage
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_position_lots_account"),
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_position_lots_exchange_order"),
    )
    trade_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_trades.id", name="fk_position_lots_exchange_trade"),
    )


# ---------------- WP-05 Checkpoint C：authorization envelopes / orders / trades / reconcile ----------------

_ENVELOPE_STATUS = ("ACTIVE", "USED", "EXPIRED", "REVOKED", "SUPERSEDED")
_ATTEMPT_RESULT = (
    "SUBMITTED", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN", "FAILED",
)
_ORDER_STATUS = (
    "OPEN", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN", "RECONCILED",
)
_ORDER_EVENT_TYPE = (
    "INTENT", "SUBMITTED", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN",
    "RECONCILED",
)
_RECONCILIATION_STATUS = ("RECONCILING", "COMPLETED", "FAILED")


class ExecutionAuthorizationEnvelope(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """authorization envelope：绑定 intent/account/release/spec/permission/fencing/preflight。

    稳定 idempotency key 与 envelope hash 全局唯一；authority 仅 FAKE_CONFORMANCE；
    permission twin + shadow/0 由 deferred trigger 校验。
    """

    __tablename__ = "execution_authorization_envelopes"
    __table_args__ = (
        UniqueConstraint("envelope_key", name="uq_execution_authorization_envelopes_key"),
        UniqueConstraint("envelope_hash", name="uq_execution_authorization_envelopes_hash"),
        UniqueConstraint(
            "idempotency_key",
            name="uq_execution_authorization_envelopes_idempotency",
        ),
        CheckConstraint(
            "authority IN ('FAKE_CONFORMANCE')",
            name="ck_execution_authorization_envelopes_authority_known",
        ),
        CheckConstraint(
            f"status IN {tuple(repr(s) for s in _ENVELOPE_STATUS)}",
            name="ck_execution_authorization_envelopes_status_known",
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_execution_authorization_envelopes_fencing_positive",
        ),
        CheckConstraint(
            "intent_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_authorization_envelopes_intent_hash_hex",
        ),
        CheckConstraint(
            "preflight_hash1 ~ '^[0-9a-f]{64}$'",
            name="ck_execution_authorization_envelopes_preflight1_hex",
        ),
        CheckConstraint(
            "preflight_hash2 ~ '^[0-9a-f]{64}$'",
            name="ck_execution_authorization_envelopes_preflight2_hex",
        ),
        CheckConstraint(
            "envelope_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_authorization_envelopes_hash_hex",
        ),
        Index("ix_execution_authorization_envelopes_intent", "intent_id"),
        Index("ix_execution_authorization_envelopes_account", "account_id"),
        {"schema": TRADING_SCHEMA},
    )

    envelope_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    intent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.economic_action_intents.id", name="fk_execution_authorization_envelopes_intent"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_execution_authorization_envelopes_account"),
        nullable=False,
    )
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_execution_authorization_envelopes_release"),
        nullable=False,
    )
    execution_spec_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.execution_spec_versions.id", name="fk_execution_authorization_envelopes_exec_spec"),
        nullable=False,
    )
    capital_permission_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.capital_permission_manifests.id",
            name="fk_execution_authorization_envelopes_permission",
        ),
        nullable=False,
    )
    authority: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    intent_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    preflight_hash1: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    preflight_hash2: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    envelope_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ACTIVE")


class ExchangeOrder(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """current order projection（CAS 从 event 重建；UNKNOWN 保留 reservation + hard stop）。"""

    __tablename__ = "exchange_orders"
    __table_args__ = (
        UniqueConstraint("order_key", name="uq_exchange_orders_key"),
        UniqueConstraint("account_id", "external_order_id", name="uq_exchange_orders_account_ext"),
        CheckConstraint(
            f"status IN {tuple(repr(s) for s in _ORDER_STATUS)}",
            name="ck_exchange_orders_status_known",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_exchange_orders_side_known"),
        CheckConstraint("price > 0 AND price <= 1", name="ck_exchange_orders_price_range"),
        CheckConstraint("size > 0", name="ck_exchange_orders_size_positive"),
        CheckConstraint(
            "filled_size >= 0 AND filled_size <= size",
            name="ck_exchange_orders_filled_range",
        ),
        Index("ix_exchange_orders_account", "account_id", "status"),
        {"schema": TRADING_SCHEMA},
    )

    order_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    # attempt_id 为普通列（避免 order→attempt→event→order 环形 FK；由 Logic 维护投影）。
    attempt_id: Mapped[int | None] = mapped_column(BigInteger)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_exchange_orders_account"),
        nullable=False,
    )
    external_order_id: Mapped[str | None] = mapped_column(external_id_type())
    token_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)
    size: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    filled_size: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="OPEN")
    updated_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


class ExchangeOrderAttempt(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次 fake send 的持久化 attempt：send 前已提交 body hash/order hash/sdk hash/salt/fence。

    signed body/signature 原文不入 DB。
    """

    __tablename__ = "exchange_order_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_key", name="uq_exchange_order_attempts_key"),
        UniqueConstraint(
            "envelope_id", "attempt_no",
            name="uq_exchange_order_attempts_envelope_no",
        ),
        CheckConstraint(
            f"result IN {tuple(repr(s) for s in _ATTEMPT_RESULT)}",
            name="ck_exchange_order_attempts_result_known",
        ),
        CheckConstraint(
            "body_hash ~ '^[0-9a-f]{64}$'",
            name="ck_exchange_order_attempts_body_hash_hex",
        ),
        CheckConstraint(
            "expected_order_hash ~ '^[0-9a-f]{64}$'",
            name="ck_exchange_order_attempts_order_hash_hex",
        ),
        CheckConstraint(
            "sdk_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_exchange_order_attempts_sdk_hash_hex",
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_exchange_order_attempts_fencing_positive",
        ),
        CheckConstraint("salt > 0", name="ck_exchange_order_attempts_salt_positive"),
        CheckConstraint("timestamp > 0", name="ck_exchange_order_attempts_timestamp_positive"),
        CheckConstraint("attempt_no >= 1", name="ck_exchange_order_attempts_no_positive"),
        Index("ix_exchange_order_attempts_envelope", "envelope_id", "attempt_no"),
        {"schema": TRADING_SCHEMA},
    )

    attempt_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    envelope_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.execution_authorization_envelopes.id",
            name="fk_exchange_order_attempts_envelope",
        ),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    expected_order_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    sdk_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    salt: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False, server_default="SUBMITTED")
    state_event_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.order_state_events.id", name="fk_exchange_order_attempts_state_event"),
    )


class OrderStateEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only 订单状态事件（唯一 idempotency key；禁 UPDATE/DELETE）。"""

    __tablename__ = "order_state_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_order_state_events_key"),
        CheckConstraint(
            f"event_type IN {tuple(repr(s) for s in _ORDER_EVENT_TYPE)}",
            name="ck_order_state_events_type_known",
        ),
        CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="ck_order_state_events_hash_hex"),
        CheckConstraint("fence_token > 0", name="ck_order_state_events_fence_positive"),
        Index("ix_order_state_events_order", "order_id", "event_type"),
        {"schema": TRADING_SCHEMA},
    )

    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_order_state_events_order"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_from: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_to: Mapped[str] = mapped_column(String(32), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    event_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ExchangeTrade(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only 成交事实（(account_id, external_trade_id) 唯一；禁 UPDATE/DELETE）。"""

    __tablename__ = "exchange_trades"
    __table_args__ = (
        UniqueConstraint("trade_key", name="uq_exchange_trades_key"),
        UniqueConstraint("account_id", "external_trade_id", name="uq_exchange_trades_account_ext"),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_exchange_trades_side_known"),
        CheckConstraint("price > 0 AND price <= 1", name="ck_exchange_trades_price_range"),
        CheckConstraint("size > 0", name="ck_exchange_trades_size_positive"),
        CheckConstraint("fee >= 0", name="ck_exchange_trades_fee_nonneg"),
        Index("ix_exchange_trades_order", "order_id"),
        {"schema": TRADING_SCHEMA},
    )

    trade_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.exchange_orders.id", name="fk_exchange_trades_order"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_exchange_trades_account"),
        nullable=False,
    )
    external_trade_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(probability_type(), nullable=False)
    size: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    fee: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    trade_time: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class AccountReconciliation(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次账户对账：只完整 pages + diff=0 可 COMPLETED；manifest 持久化。"""

    __tablename__ = "account_reconciliations"
    __table_args__ = (
        UniqueConstraint("reconciliation_key", name="uq_account_reconciliations_key"),
        CheckConstraint(
            f"status IN {tuple(repr(s) for s in _RECONCILIATION_STATUS)}",
            name="ck_account_reconciliations_status_known",
        ),
        CheckConstraint("fencing_token > 0", name="ck_account_reconciliations_fencing_positive"),
        CheckConstraint("ws_watermark >= 0", name="ck_account_reconciliations_ws_watermark_nonneg"),
        CheckConstraint(
            "rest_page_hash ~ '^[0-9a-f]{64}$'",
            name="ck_account_reconciliations_page_hash_hex",
        ),
        CheckConstraint(
            "input_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_account_reconciliations_input_hash_hex",
        ),
        CheckConstraint(
            "output_manifest_hash IS NULL OR output_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_account_reconciliations_output_hash_hex",
        ),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="ck_account_reconciliations_completed_pair",
        ),
        Index("ix_account_reconciliations_account", "account_id", "created_at"),
        {"schema": TRADING_SCHEMA},
    )

    reconciliation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_account_reconciliations_account"),
        nullable=False,
    )
    trigger_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    ws_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rest_page_cursor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rest_page_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    unknown_queries: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    output_manifest_hash: Mapped[str | None] = mapped_column(sha256_type())
    differences: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="RECONCILING")
    completed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
