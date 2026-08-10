"""Trading market master models（WP-01B Checkpoint B，revision ``b1000010``）。

9 张表：universe frame/page、event/market/token master 与 version、lifecycle、current projection。

不变量（DB 强制，任务 §5.2）：
- frame/page append-only；``(frame_id,page_no)`` 与 ``(frame_id,endpoint,cursor_input)`` 唯一；
  frame 状态 ``OPEN|COMPLETE|FAILED`` 只有合法 transition，且不可 DELETE。
- event/market/token 的 provider ID / condition / token 唯一；version/lifecycle append-only。
- 每个二元 market 的 token：``outcome_index IN (0,1)`` + ``(market_id,outcome_index)`` 唯一
  （至多一个 per index）；完整 YES/NO 双 token → ``pm_market_current.eligible``（Logic 判定）。
- ``pm_market_current`` 是可重建 projection：``observed_at`` CAS 单调（旧帧/乱序不得覆盖）。
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
    text,
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

# 定标价格列（Decimal，非 base-unit；禁止 float）
PRICE_PRECISION = 38
PRICE_SCALE = 12


def _price_column() -> Numeric:
    return Numeric(PRICE_PRECISION, PRICE_SCALE)


FRAME_STATUS = ("OPEN", "COMPLETE", "FAILED")
PAGE_ENDPOINTS = ("events_open", "events_closed", "markets_open", "markets_closed")
LIFECYCLE_TYPES = ("created", "updated", "closed", "reopened", "resolved", "incomplete")
MAPPING_STATES = ("complete", "incomplete", "conflict")


class PUniverseFrame(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一轮 universe 扫描 frame；CAS lease 驱动，终态完全不可变。"""

    __tablename__ = "pm_universe_frames"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','COMPLETE','FAILED')", name="ck_pm_universe_frames_status_known"),
        CheckConstraint("page_count >= 0", name="ck_pm_universe_frames_page_count_nonneg"),
        CheckConstraint("total_events >= 0", name="ck_pm_universe_frames_total_events_nonneg"),
        CheckConstraint("total_markets >= 0", name="ck_pm_universe_frames_total_markets_nonneg"),
        CheckConstraint("length(owner) > 0", name="ck_pm_universe_frames_owner_nonempty"),
        CheckConstraint("fencing_token > 0", name="ck_pm_universe_frames_fencing_positive"),
        CheckConstraint(
            "lease_expires_at > started_at",
            name="ck_pm_universe_frames_lease_after_start",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_pm_universe_frames_completed_after_start",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND completed_at IS NULL AND error_reason IS NULL) OR "
            "(status = 'COMPLETE' AND completed_at IS NOT NULL "
            " AND content_hash IS NOT NULL AND artifact_id IS NOT NULL "
            " AND error_reason IS NULL) OR "
            "(status = 'FAILED' AND completed_at IS NOT NULL AND error_reason IS NOT NULL)",
            name="ck_pm_universe_frames_terminal_shape",
        ),
        Index(
            "ix_pm_universe_frames_open_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
        {"schema": TRADING_SCHEMA},
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="OPEN")
    started_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    owner: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_markets: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    content_hash: Mapped[str | None] = mapped_column(sha256_type())
    artifact_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.artifact_objects.id", name="fk_pm_universe_frames_artifact"),
    )
    artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    error_reason: Mapped[str | None] = mapped_column(String(128))


class PUniverseFramePage(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """frame 内一页 keyset 响应；append-only，cursor 单调链。"""

    __tablename__ = "pm_universe_frame_pages"
    __table_args__ = (
        UniqueConstraint("frame_id", "page_no", name="uq_pm_universe_frame_pages_frame_page"),
        UniqueConstraint(
            "frame_id", "endpoint", "cursor_input",
            name="uq_pm_universe_frame_pages_frame_endpoint_cursor",
        ),
        CheckConstraint(
            "endpoint IN ('events_open','events_closed','markets_open','markets_closed')",
            name="ck_pm_universe_frame_pages_endpoint_known",
        ),
        CheckConstraint("page_no >= 0", name="ck_pm_universe_frame_pages_page_no_nonneg"),
        CheckConstraint("item_count >= 0", name="ck_pm_universe_frame_pages_item_count_nonneg"),
        Index("ix_pm_universe_frame_pages_frame", "frame_id", "page_no"),
        {"schema": TRADING_SCHEMA},
    )

    frame_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_universe_frames.id", name="fk_pm_universe_frame_pages_frame"),
        nullable=False,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(16), nullable=False)
    cursor_input: Mapped[str | None] = mapped_column(Text)
    cursor_output: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    raw_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_pm_universe_frame_pages_raw_artifact",
        ),
        nullable=False,
    )
    raw_artifact_ref: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    raw_artifact_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class PMEvent(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """Gamma event current projection（Master；upsert，非 append-only）。"""

    __tablename__ = "pm_events"
    __table_args__ = (
        UniqueConstraint("gamma_event_id", name="uq_pm_events_gamma_event_id"),
        Index("ix_pm_events_slug", "slug"),
        Index("ix_pm_events_closed", "closed"),
        {"schema": TRADING_SCHEMA},
    )

    gamma_event_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    slug: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    end_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    active: Mapped[bool | None] = mapped_column(Boolean)
    closed: Mapped[bool | None] = mapped_column(Boolean)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    volume: Mapped[Decimal | None] = mapped_column(_price_column())
    liquidity: Mapped[Decimal | None] = mapped_column(_price_column())
    content_hash: Mapped[str | None] = mapped_column(sha256_type())
    raw_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())


class PMMarket(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """Gamma/CLOB market current projection（Master；upsert，非 append-only）。"""

    __tablename__ = "pm_markets"
    __table_args__ = (
        UniqueConstraint("gamma_market_id", name="uq_pm_markets_gamma_market_id"),
        UniqueConstraint("condition_id", name="uq_pm_markets_condition_id"),
        Index("ix_pm_markets_gamma_event", "gamma_event_id"),
        Index(
            "ix_pm_markets_tradeable",
            "end_date",
            "gamma_market_id",
            postgresql_where=text(
                "active AND NOT closed AND accepting_orders AND enable_order_book"
            ),
        ),
        {"schema": TRADING_SCHEMA},
    )

    gamma_market_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    gamma_event_id: Mapped[str | None] = mapped_column(
        external_id_type(),
        ForeignKey("trading.pm_events.gamma_event_id", name="fk_pm_markets_gamma_event"),
    )
    condition_id: Mapped[str | None] = mapped_column(external_id_type())
    question: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(Text)

    active: Mapped[bool | None] = mapped_column(Boolean)
    closed: Mapped[bool | None] = mapped_column(Boolean)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    accepting_orders: Mapped[bool | None] = mapped_column(Boolean)
    enable_order_book: Mapped[bool | None] = mapped_column(Boolean)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean)

    start_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    end_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    closed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())

    volume: Mapped[Decimal | None] = mapped_column(_price_column())
    liquidity: Mapped[Decimal | None] = mapped_column(_price_column())
    spread: Mapped[Decimal | None] = mapped_column(_price_column())
    best_bid: Mapped[Decimal | None] = mapped_column(_price_column())
    best_ask: Mapped[Decimal | None] = mapped_column(_price_column())
    last_trade_price: Mapped[Decimal | None] = mapped_column(_price_column())

    content_hash: Mapped[str | None] = mapped_column(sha256_type())
    raw_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())


class PMMarketVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """market 不可变版本（append-only）：question/规则/截止/状态 + normalized hash。"""

    __tablename__ = "pm_market_versions"
    __table_args__ = (
        UniqueConstraint("market_id", "version_no", name="uq_pm_market_versions_market_version"),
        CheckConstraint("version_no > 0", name="ck_pm_market_versions_version_positive"),
        Index("ix_pm_market_versions_market", "market_id"),
        {"schema": TRADING_SCHEMA},
    )

    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_pm_market_versions_market"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[str | None] = mapped_column(Text)
    resolution_source: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    end_date: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    active: Mapped[bool | None] = mapped_column(Boolean)
    closed: Mapped[bool | None] = mapped_column(Boolean)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    accepting_orders: Mapped[bool | None] = mapped_column(Boolean)
    enable_order_book: Mapped[bool | None] = mapped_column(Boolean)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean)
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    raw_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    normalized_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class PMToken(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """token current projection（Master）：YES(0)/NO(1) 唯一 per market。"""

    __tablename__ = "pm_tokens"
    __table_args__ = (
        UniqueConstraint("token_id", name="uq_pm_tokens_token_id"),
        UniqueConstraint("market_id", "outcome_index", name="uq_pm_tokens_market_index"),
        CheckConstraint("outcome_index IN (0,1)", name="ck_pm_tokens_outcome_index_binary"),
        {"schema": TRADING_SCHEMA},
    )

    token_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_pm_tokens_market"),
        nullable=False,
    )
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_label: Mapped[str | None] = mapped_column(String(64))
    price_hint: Mapped[Decimal | None] = mapped_column(_price_column())


class PMTokenVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """token 不可变版本（append-only）：outcome/index/price hint。"""

    __tablename__ = "pm_token_versions"
    __table_args__ = (
        UniqueConstraint("token_id", "version_no", name="uq_pm_token_versions_token_version"),
        CheckConstraint("version_no > 0", name="ck_pm_token_versions_version_positive"),
        CheckConstraint("outcome_index IN (0,1)", name="ck_pm_token_versions_outcome_index_binary"),
        Index("ix_pm_token_versions_token", "token_id"),
        {"schema": TRADING_SCHEMA},
    )

    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_pm_token_versions_token"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_label: Mapped[str | None] = mapped_column(String(64))
    price_hint: Mapped[Decimal | None] = mapped_column(_price_column())
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class PMMarketLifecycleEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """market lifecycle 事件（append-only）；内容相同时刻去重。"""

    __tablename__ = "pm_market_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "market_id", "event_type", "observed_at", "payload_hash",
            name="uq_pm_market_lifecycle_events_dedup",
        ),
        CheckConstraint(
            "event_type IN ('created','updated','closed','reopened','resolved','incomplete')",
            name="ck_pm_market_lifecycle_events_type_known",
        ),
        Index("ix_pm_market_lifecycle_events_market", "market_id"),
        {"schema": TRADING_SCHEMA},
    )

    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_pm_market_lifecycle_events_market"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_time: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    raw_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())


class PMMarketCurrent(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """market current 可重建 projection；``observed_at`` CAS 单调（旧帧不得覆盖）。"""

    __tablename__ = "pm_market_current"
    __table_args__ = (
        UniqueConstraint("market_id", name="uq_pm_market_current_market"),
        UniqueConstraint("condition_id", name="uq_pm_market_current_condition"),
        CheckConstraint(
            "mapping_state IN ('complete','incomplete','conflict')",
            name="ck_pm_market_current_mapping_state_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_markets.id", name="fk_pm_market_current_market"),
        nullable=False,
    )
    condition_id: Mapped[str | None] = mapped_column(external_id_type())
    gamma_market_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    tokens_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    mapping_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="incomplete")
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sha256_type())
