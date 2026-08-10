"""Trading vault 域 foundation models（WP-01A-02）。

本期只建加密数据模型，不实现解密服务。数据库/API/log/Redis 中不得出现明文 secret：
entry 是稳定 identity（无 value/token/password 字段），version 只保存 key id/version、
nonce、ciphertext、AAD hash、algorithm，access event 只保存主体/用途/reason code，不含
secret 内容。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TradingBase,
)
from app.models.trading.types import external_id_type, sha256_type


class SecretVaultEntry(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """vault 稳定 identity；只含名称与生命周期状态，绝不含 secret 内容。"""

    __tablename__ = "secret_vault_entries"
    __table_args__ = (
        UniqueConstraint("name", name="uq_secret_vault_entries_name"),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_secret_vault_entries_status_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    name: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")


class SecretVaultVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一个 entry 的加密版本；仅密文与密钥元数据，无明文。"""

    __tablename__ = "secret_vault_versions"
    __table_args__ = (
        CheckConstraint(
            "algorithm = 'aes-256-gcm'",
            name="ck_secret_vault_versions_algorithm_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_secret_vault_versions_entry"),
        nullable=False,
    )
    key_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    key_version: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    nonce: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    aad_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.secret_vault_versions.id",
            ondelete="SET NULL",
            name="fk_secret_vault_versions_supersedes",
        ),
    )


class SecretAccessEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """vault 访问审计：append-only，只保存主体/用途/reason，不保存 secret 内容。"""

    __tablename__ = "secret_access_events"
    __table_args__ = (
        {"schema": TRADING_SCHEMA},
    )

    entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_secret_access_events_entry"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    result_reason: Mapped[str] = mapped_column(String(64), nullable=False)
