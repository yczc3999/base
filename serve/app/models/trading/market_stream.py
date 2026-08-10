"""Trading market stream / book evidence models（WP-01B Checkpoint C，revision ``b1000011``）。

7 张表：connection epoch、source batch/index、book checkpoint/levels、book current、quote binding。

不变量（DB 强制，任务 §5.3）：
- source batch/index 与 book checkpoint/levels 按 ``received_at`` UTC **日 RANGE** 分区，无 default
  partition；分区唯一/PK 必须含时间；全局幂等走 foundation ``idempotency_claims``。
- epoch 状态机 ``CONNECTING→SYNCING→LIVE→STALE|CLOSED``（DB guard）；STALE 后必须新建 epoch；
  同一 shard/provider 同时最多一个 CONNECTING/SYNCING/LIVE epoch（partial unique）。
- checkpoint/levels append-only；level 以 ``(checkpoint_id, side, price, received_at)`` 唯一。
- ``pm_book_current`` 是可重建 projection：``observed_at`` CAS 单调，原子替换完整 snapshot。
- ``pm_quote_bindings`` append-only pin 精确 checkpoint；禁止 crossed/零价 quote。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TimestampMixin,
    TradingBase,
)
from app.models.trading.types import external_id_type, sha256_type, utc_timestamp_type

PRICE_PRECISION = 38
PRICE_SCALE = 12

EPOCH_STATUSES = ("CONNECTING", "SYNCING", "LIVE", "STALE", "CLOSED")
SOURCE_KINDS = ("gamma", "clob_public", "market_ws")
PARSE_STATUSES = ("parsed", "invalid", "unknown")
CHECKPOINT_SOURCES = ("ws_initial", "ws_delta_aggregate", "rest_full", "rest_batch")
BOOK_VALIDITY = ("VALID", "STALE", "CROSSED")
CURRENT_VALIDITY = ("VALID", "STALE", "CROSSED", "SYNCING")


def _price_column() -> Numeric:
    return Numeric(PRICE_PRECISION, PRICE_SCALE)


def _size_column() -> Numeric:
    return Numeric(PRICE_PRECISION, PRICE_SCALE)


class PMConnectionEpoch(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """一次 provider 连接的本地 epoch；状态机由 DB guard 强制。"""

    __tablename__ = "pm_connection_epochs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CONNECTING','SYNCING','LIVE','STALE','CLOSED')",
            name="ck_pm_connection_epochs_status_known",
        ),
        CheckConstraint(
            "provider IN ('gamma','clob_public','market_ws')",
            name="ck_pm_connection_epochs_provider_known",
        ),
        CheckConstraint(
            "fencing_token IS NULL OR fencing_token > 0",
            name="ck_pm_connection_epochs_fencing_positive",
        ),
        # STALE 是失效证据而非活连接；重连必须创建新 epoch。
        Index(
            "uq_pm_connection_epochs_active_shard",
            "shard_key",
            "provider",
            unique=True,
            postgresql_where=text("status IN ('CONNECTING','SYNCING','LIVE')"),
        ),
        Index("ix_pm_connection_epochs_shard", "shard_key", "provider", "started_at"),
        {"schema": TRADING_SCHEMA},
    )

    shard_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    config_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_pm_connection_epochs_release"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="CONNECTING")
    owner: Mapped[str | None] = mapped_column(external_id_type())
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    live_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    stale_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    closed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    closed_reason: Mapped[str | None] = mapped_column(String(128))


class PMSourceEventBatch(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """source event 压缩 batch（按 received_at 日 RANGE 分区，无 default）。"""

    __tablename__ = "pm_source_event_batches"
    __table_args__ = (
        UniqueConstraint(
            "connection_epoch_id", "batch_no", "received_at",
            name="uq_pm_source_event_batches_epoch_batch",
        ),
        CheckConstraint("batch_no >= 0", name="ck_pm_source_event_batches_batch_no_nonneg"),
        CheckConstraint("event_count >= 0", name="ck_pm_source_event_batches_event_count_nonneg"),
        Index(
            "ix_pm_source_event_batches_epoch_seq",
            "connection_epoch_id",
            "first_receive_seq",
        ),
        {"schema": TRADING_SCHEMA, "postgresql_partition_by": "RANGE (received_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    connection_epoch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_connection_epochs.id", name="fk_pm_source_event_batches_epoch"),
        nullable=False,
    )
    batch_no: Mapped[int] = mapped_column(Integer, nullable=False)
    first_receive_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    last_receive_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    last_received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    batch_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    prev_batch_hash: Mapped[str | None] = mapped_column(sha256_type())
    raw_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_pm_source_event_batches_raw_artifact",
        ),
        nullable=False,
    )
    raw_artifact_ref: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), primary_key=True)


class PMSourceEventIndex(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """material source event index；epoch/seq 全局幂等由 idempotency claim 原子占位。"""

    __tablename__ = "pm_source_event_index"
    __table_args__ = (
        CheckConstraint(
            "source IN ('gamma','clob_public','market_ws')",
            name="ck_pm_source_event_index_source_known",
        ),
        CheckConstraint(
            "parse_status IN ('parsed','invalid','unknown')",
            name="ck_pm_source_event_index_parse_status_known",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_pm_source_event_index_latency_nonneg",
        ),
        CheckConstraint(
            "retry_count IS NULL OR retry_count >= 0",
            name="ck_pm_source_event_index_retry_nonneg",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_pm_source_event_index_http_status_range",
        ),
        Index(
            "ix_pm_source_event_index_token",
            "token_id",
            "received_at",
            "batch_id",
            "batch_ordinal",
        ),
        Index(
            "ix_pm_source_event_index_condition_kind",
            "condition_id",
            "kind",
            "received_at",
        ),
        Index("ix_pm_source_event_index_batch", "batch_id", "batch_ordinal"),
        Index(
            "ix_pm_source_event_index_epoch_seq",
            "connection_epoch_id",
            "local_receive_seq",
            "received_at",
        ),
        ForeignKeyConstraint(
            ["batch_id", "received_at"],
            ["trading.pm_source_event_batches.id", "trading.pm_source_event_batches.received_at"],
            name="fk_pm_source_event_index_batch",
        ),
        {"schema": TRADING_SCHEMA, "postgresql_partition_by": "RANGE (received_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(sha256_type())
    response_hash: Mapped[str | None] = mapped_column(sha256_type())
    retry_count: Mapped[int | None] = mapped_column(Integer)
    connection_epoch_id: Mapped[int | None] = mapped_column(BigInteger)
    local_receive_seq: Mapped[int | None] = mapped_column(Integer)
    provider_time: Mapped[int | None] = mapped_column(BigInteger)
    event_time: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    payload_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    batch_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="parsed")
    parse_reason: Mapped[str | None] = mapped_column(String(128))
    condition_id: Mapped[str | None] = mapped_column(external_id_type())
    token_id: Mapped[str | None] = mapped_column(external_id_type())
    gamma_market_id: Mapped[str | None] = mapped_column(external_id_type())


class PMBookCheckpoint(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """book checkpoint（按 received_at 日 RANGE 分区）；append-only。"""

    __tablename__ = "pm_book_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "id", "received_at", "token_id",
            name="uq_pm_book_checkpoints_identity_token",
        ),
        CheckConstraint(
            "source_kind IN ('ws_initial','ws_delta_aggregate','rest_full','rest_batch')",
            name="ck_pm_book_checkpoints_source_known",
        ),
        CheckConstraint(
            "validity IN ('VALID','STALE','CROSSED')",
            name="ck_pm_book_checkpoints_validity_known",
        ),
        Index(
            "ix_pm_book_checkpoints_token_valid",
            "token_id",
            "received_at",
            postgresql_where=text("validity = 'VALID'"),
        ),
        Index("ix_pm_book_checkpoints_epoch", "connection_epoch_id", "received_at"),
        {"schema": TRADING_SCHEMA, "postgresql_partition_by": "RANGE (received_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    token_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    connection_epoch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_connection_epochs.id", name="fk_pm_book_checkpoints_epoch"),
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    book_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    best_bid: Mapped[Decimal | None] = mapped_column(_price_column())
    best_ask: Mapped[Decimal | None] = mapped_column(_price_column())
    tick_size: Mapped[Decimal | None] = mapped_column(_price_column())
    min_order_size: Mapped[Decimal | None] = mapped_column(_size_column())
    provider_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    raw_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            name="fk_pm_book_checkpoints_raw_artifact",
        ),
        nullable=False,
    )
    artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    completeness: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    validity: Mapped[str] = mapped_column(String(16), nullable=False, server_default="VALID")
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), primary_key=True)


class PMBookLevel(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """book depth level（按 received_at 日 RANGE 分区）；append-only。"""

    __tablename__ = "pm_book_levels"
    __table_args__ = (
        UniqueConstraint(
            "checkpoint_id", "side", "price", "received_at",
            name="uq_pm_book_levels_checkpoint_side_price",
        ),
        CheckConstraint("side IN ('bid','ask')", name="ck_pm_book_levels_side_known"),
        CheckConstraint("price > 0", name="ck_pm_book_levels_price_positive"),
        CheckConstraint("size >= 0", name="ck_pm_book_levels_size_nonneg"),
        Index("ix_pm_book_levels_checkpoint", "checkpoint_id", "ordinal"),
        ForeignKeyConstraint(
            ["checkpoint_id", "received_at"],
            ["trading.pm_book_checkpoints.id", "trading.pm_book_checkpoints.received_at"],
            name="fk_pm_book_levels_checkpoint",
        ),
        {"schema": TRADING_SCHEMA, "postgresql_partition_by": "RANGE (received_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), primary_key=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(_price_column(), nullable=False)
    size: Mapped[Decimal] = mapped_column(_size_column(), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class PMBookCurrent(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """book current 可重建 projection；``observed_at`` CAS 单调。"""

    __tablename__ = "pm_book_current"
    __table_args__ = (
        UniqueConstraint("token_id", name="uq_pm_book_current_token"),
        CheckConstraint(
            "validity IN ('VALID','STALE','CROSSED','SYNCING')",
            name="ck_pm_book_current_validity_known",
        ),
        CheckConstraint(
            "(checkpoint_id IS NULL) = (checkpoint_received_at IS NULL)",
            name="ck_pm_book_current_checkpoint_pair",
        ),
        ForeignKeyConstraint(
            ["checkpoint_id", "checkpoint_received_at", "token_id"],
            [
                "trading.pm_book_checkpoints.id",
                "trading.pm_book_checkpoints.received_at",
                "trading.pm_book_checkpoints.token_id",
            ],
            name="fk_pm_book_current_checkpoint",
        ),
        {"schema": TRADING_SCHEMA},
    )

    token_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    connection_epoch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_connection_epochs.id", name="fk_pm_book_current_epoch"),
    )
    checkpoint_id: Mapped[int | None] = mapped_column(BigInteger)
    checkpoint_received_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    best_bid: Mapped[Decimal | None] = mapped_column(_price_column())
    best_ask: Mapped[Decimal | None] = mapped_column(_price_column())
    tick_size: Mapped[Decimal | None] = mapped_column(_price_column())
    min_order_size: Mapped[Decimal | None] = mapped_column(_size_column())
    depth_hash: Mapped[str | None] = mapped_column(sha256_type())
    validity: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SYNCING")
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)


class PMQuoteBinding(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """quote binding：append-only pin 精确 checkpoint / price convention / as-of / staleness。"""

    __tablename__ = "pm_quote_bindings"
    __table_args__ = (
        UniqueConstraint(
            "token_id", "checkpoint_id", "checkpoint_received_at",
            name="uq_pm_quote_bindings_token_checkpoint",
        ),
        CheckConstraint(
            "best_bid > 0 AND best_ask > 0 AND best_bid <= 1 AND best_ask <= 1",
            name="ck_pm_quote_bindings_price_positive",
        ),
        CheckConstraint(
            "best_ask > best_bid",
            name="ck_pm_quote_bindings_not_crossed",
        ),
        CheckConstraint(
            "stale_at > as_of AND stale_at > received_at",
            name="ck_pm_quote_bindings_stale_after_evidence",
        ),
        ForeignKeyConstraint(
            ["checkpoint_id", "checkpoint_received_at", "token_id"],
            [
                "trading.pm_book_checkpoints.id",
                "trading.pm_book_checkpoints.received_at",
                "trading.pm_book_checkpoints.token_id",
            ],
            name="fk_pm_quote_bindings_checkpoint",
        ),
        Index("ix_pm_quote_bindings_token", "token_id", "as_of"),
        {"schema": TRADING_SCHEMA},
    )

    token_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    checkpoint_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checkpoint_received_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(), nullable=False
    )
    best_bid: Mapped[Decimal] = mapped_column(_price_column(), nullable=False)
    best_ask: Mapped[Decimal] = mapped_column(_price_column(), nullable=False)
    price_convention: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    staleness_policy_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    stale_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    decision_ref: Mapped[str | None] = mapped_column(external_id_type())
