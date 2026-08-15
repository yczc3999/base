"""后台运行时配置模型（用户拍板，WP-07C 之外先行落地）。

- ``RuntimeFlag``：可变单行开关（如 ``pipeline.ai_enabled``），PK 即 flag_key；
  值永远是 TEXT（``true``/``false``），由 Logic 解析，库内不发明类型。
- ``RuntimeFlagEvent``：append-only 变更审计（old/new/actor），绝不 UPDATE/DELETE。

两张表都不含任何 secret；模型 API key 明文只经 vault（``secret_vault_*``）存取。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TradingBase,
)
from app.models.trading.types import external_id_type, utc_timestamp_type


class RuntimeFlag(TradingBase):
    """运行时开关单行；flag_key 为主键，updated_at 由 server 维护。"""

    __tablename__ = "runtime_flags"
    __table_args__ = (
        {"schema": TRADING_SCHEMA},
    )

    flag_key: Mapped[str] = mapped_column(external_id_type(), primary_key=True)
    flag_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RuntimeFlagEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """flag 变更审计：append-only，记录 old/new/actor，不含 secret。"""

    __tablename__ = "runtime_flag_events"
    __table_args__ = (
        Index("ix_runtime_flag_events_flag", "flag_key", "id"),
        {"schema": TRADING_SCHEMA},
    )

    flag_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    actor: Mapped[Optional[str]] = mapped_column(Text)
