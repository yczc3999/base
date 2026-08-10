"""Trading outbox 域 foundation models（WP-01A-02）。

- ``idempotency_claims(scope,key)`` 全局唯一，非分区表。
- ``transactional_outbox``：待投递热表；event id 唯一，payload JSONB 或 artifact 二者恰一，
  ``(available_at,id) WHERE status='PENDING'`` 有 partial index。
- ``outbox_delivery_history``：按 ``completed_at`` 月 RANGE 分区、无 default partition 的
  append-only terminal history；分区唯一键含 ``completed_at``。
- ``job_completions`` 对 ``(consumer, idempotency_key)`` 唯一；完成/归档可追溯原 outbox event。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Identity,
    Integer,
    SmallInteger,
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
from app.models.trading.types import external_id_type, sha256_type, utc_timestamp_type


class IdempotencyClaim(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """跨分区幂等认领；非分区表。"""

    __tablename__ = "idempotency_claims"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_claims_scope_key"),
        {"schema": TRADING_SCHEMA},
    )

    scope: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    owner: Mapped[str | None] = mapped_column(external_id_type())


class TransactionalOutbox(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """待投递 outbox 热表。"""

    __tablename__ = "transactional_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_transactional_outbox_event_id"),
        CheckConstraint(
            "status IN ('PENDING','DISPATCHED','COMPLETED','DEAD')",
            name="ck_transactional_outbox_status_known",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_transactional_outbox_payload_object",
        ),
        CheckConstraint(
            "(payload IS NOT NULL) <> (artifact_ref IS NOT NULL)",
            name="ck_transactional_outbox_payload_xor_artifact",
        ),
        CheckConstraint(
            "priority BETWEEN 0 AND 255",
            name="ck_transactional_outbox_priority_range",
        ),
        Index(
            "ix_transactional_outbox_pending",
            "available_at",
            "id",
            postgresql_where=text("status = 'PENDING'"),
        ),
        {"schema": TRADING_SCHEMA},
    )

    event_id: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    release_manifest_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_transactional_outbox_release"),
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="128")
    payload: Mapped[dict | None] = mapped_column(JSONB)
    artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(),
        nullable=False,
        server_default=text("now()"),
    )
    deadline: Mapped[datetime | None] = mapped_column(
        utc_timestamp_type()
    )
    visibility_deadline: Mapped[datetime | None] = mapped_column(
        utc_timestamp_type()
    )
    lease_owner: Mapped[str | None] = mapped_column(external_id_type())
    lease_token: Mapped[str | None] = mapped_column(external_id_type())
    error_reason: Mapped[str | None] = mapped_column(String(128))
    error_at: Mapped[datetime | None] = mapped_column(
        utc_timestamp_type()
    )


class OutboxDeliveryHistory(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """terminal history：按月 RANGE 分区，无 default partition，分区唯一键含 completed_at。"""

    __tablename__ = "outbox_delivery_history"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DELIVERED','DEAD','EXPIRED','REQUEUED')",
            name="ck_outbox_delivery_history_status_known",
        ),
        Index("ix_outbox_delivery_history_event", "outbox_event_id"),
        {"schema": TRADING_SCHEMA, "postgresql_partition_by": "RANGE (completed_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    outbox_event_id: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    outbox_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_reason: Mapped[str | None] = mapped_column(String(128))


class JobCompletion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """消费者幂等完成记录；同一 consumer + idempotency_key 唯一。"""

    __tablename__ = "job_completions"
    __table_args__ = (
        UniqueConstraint("consumer", "idempotency_key", name="uq_job_completions_consumer_key"),
        CheckConstraint(
            "outcome IN ('success','failed','dead')",
            name="ck_job_completions_outcome_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
