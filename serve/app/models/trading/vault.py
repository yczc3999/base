"""Trading vault / accounts / funds / reservations / fencing models（WP-01A-02 + WP-05）。

本期在 0002 vault skeleton 之上强化三张 vault 表（不重建平行 vault），并新增 WP-05
Checkpoint B 的五张 execution-plane 表。

加密数据模型原则不变：entry 是稳定 identity（无 value/token/password 字段），version 只保存
key id/version、nonce、ciphertext、AAD context/hash、ciphertext hash 与算法，access event
只保存主体/用途/reason/结果码，不含 secret 内容。数据库/API/log/Redis 中不得出现明文 secret。
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, ForeignKeyConstraint, Index

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

_SECRET_KIND_VALUES = (
    "('generic','api_credential','signer_private_key','l2_secret','passphrase')"
)


class SecretVaultEntry(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """vault 稳定 identity；只含名称、secret kind、运行时身份与生命周期状态，绝不含 secret 内容。"""

    __tablename__ = "secret_vault_entries"
    __table_args__ = (
        UniqueConstraint("name", name="uq_secret_vault_entries_name"),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_secret_vault_entries_status_known",
        ),
        CheckConstraint(
            f"secret_kind IN {_SECRET_KIND_VALUES}",
            name="ck_secret_vault_entries_secret_kind_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    name: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    secret_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime_identity: Mapped[str] = mapped_column(external_id_type(), nullable=False)


class SecretVaultVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一个 entry 的加密版本；仅密文、密钥元数据、AAD 上下文/哈希与生命周期，无明文。"""

    __tablename__ = "secret_vault_versions"
    __table_args__ = (
        UniqueConstraint(
            "entry_id", "version_no",
            name="uq_secret_vault_versions_entry_version",
        ),
        UniqueConstraint(
            "entry_id", "id",
            name="uq_secret_vault_versions_entry_id",
        ),
        UniqueConstraint(
            "key_id", "key_version", "nonce",
            name="uq_secret_vault_versions_key_nonce",
        ),
        CheckConstraint(
            "algorithm = 'aes-256-gcm'",
            name="ck_secret_vault_versions_algorithm_known",
        ),
        CheckConstraint(
            "status IN ('active','retired')",
            name="ck_secret_vault_versions_status_known",
        ),
        CheckConstraint(
            "ciphertext_hash ~ '^[0-9a-f]{64}$'",
            name="ck_secret_vault_versions_ciphertext_hash_hex",
        ),
        {"schema": TRADING_SCHEMA},
    )

    entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_secret_vault_versions_entry"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    key_version: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    nonce: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    aad_context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    aad_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    ciphertext_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    supersedes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.secret_vault_versions.id",
            ondelete="SET NULL",
            name="fk_secret_vault_versions_supersedes",
        ),
    )


class SecretAccessEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """vault 访问审计：append-only，只保存主体/用途/版本/结果/原因，不保存 secret 内容。"""

    __tablename__ = "secret_access_events"
    __table_args__ = (
        {"schema": TRADING_SCHEMA},
    )

    entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_secret_access_events_entry"),
        nullable=False,
    )
    secret_version_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.secret_vault_versions.id",
            ondelete="SET NULL",
            name="fk_secret_access_events_version",
        ),
    )
    subject: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    identity: Mapped[Optional[str]] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    key_version: Mapped[Optional[str]] = mapped_column(String(64))
    result: Mapped[Optional[str]] = mapped_column(String(64))
    result_reason: Mapped[str] = mapped_column(String(64), nullable=False)


class PMAccount(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """type-3 执行账户：Deposit Wallet funder/maker + EOA signing identity + secret refs。

    WP-05 只允许 ``identity_type='FIXTURE_ONLY'``（DB CHECK 硬 gate）；signer/L2 凭据只以
    ``secret_vault_entries`` 引用 + version 保存，明文绝不入库。
    """

    __tablename__ = "pm_accounts"
    __table_args__ = (
        UniqueConstraint("account_key", name="uq_pm_accounts_key"),
        CheckConstraint(
            "identity_type IN ('FIXTURE_ONLY','CANARY','LIVE')",
            name="ck_pm_accounts_identity_type_known",
        ),
        CheckConstraint(
            "wallet_type = 'deposit_wallet'",
            name="ck_pm_accounts_wallet_type_known",
        ),
        CheckConstraint(
            "signature_type = '3'",
            name="ck_pm_accounts_signature_type_known",
        ),
        CheckConstraint(
            "network_mode IN ('mainnet','matic_mumbai','amoy','fixture')",
            name="ck_pm_accounts_network_mode_known",
        ),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_pm_accounts_status_known",
        ),
        CheckConstraint(
            "identity_type = 'FIXTURE_ONLY'",
            name="ck_pm_accounts_wp05_fixture_only",
        ),
        CheckConstraint(
            "identity_type = 'FIXTURE_ONLY' AND network_mode = 'fixture'",
            name="ck_pm_accounts_fixture_network_pair",
        ),
        CheckConstraint(
            "provider = 'polymarket' AND chain_id = 137 "
            "AND funder_address IS NOT NULL AND maker_address IS NOT NULL "
            "AND signing_identity IS NOT NULL AND funder_address = maker_address "
            "AND signing_identity <> maker_address",
            name="ck_pm_accounts_type3_identity_shape",
        ),
        CheckConstraint(
            "(signer_secret_entry_id IS NULL) = (signer_secret_version_id IS NULL)",
            name="ck_pm_accounts_signer_secret_pair",
        ),
        CheckConstraint(
            "(l2_secret_entry_id IS NULL) = (l2_secret_version_id IS NULL)",
            name="ck_pm_accounts_l2_secret_pair",
        ),
        ForeignKeyConstraint(
            ("signer_secret_entry_id", "signer_secret_version_id"),
            ("trading.secret_vault_versions.entry_id", "trading.secret_vault_versions.id"),
            name="fk_pm_accounts_signer_version",
        ),
        ForeignKeyConstraint(
            ("l2_secret_entry_id", "l2_secret_version_id"),
            ("trading.secret_vault_versions.entry_id", "trading.secret_vault_versions.id"),
            name="fk_pm_accounts_l2_version",
        ),
        {"schema": TRADING_SCHEMA},
    )

    account_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    funder_address: Mapped[Optional[str]] = mapped_column(external_id_type())
    maker_address: Mapped[Optional[str]] = mapped_column(external_id_type())
    signing_identity: Mapped[Optional[str]] = mapped_column(external_id_type())
    wallet_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="3")
    signer_secret_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_pm_accounts_signer_entry"),
    )
    signer_secret_version_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    l2_secret_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("trading.secret_vault_entries.id", name="fk_pm_accounts_l2_entry"),
    )
    l2_secret_version_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_pm_accounts_release"),
        nullable=False,
    )
    capital_permission_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.capital_permission_manifests.id",
            name="fk_pm_accounts_capital",
        ),
        nullable=False,
    )
    network_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")


class PMBalanceAllowanceSnapshot(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only provider/fixture 余额观察；绝不 UPDATE/DELETE。"""

    __tablename__ = "pm_balance_allowance_snapshots"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_pm_balance_allowance_snapshots_balance_nonneg"),
        CheckConstraint(
            "allowance >= 0",
            name="ck_pm_balance_allowance_snapshots_allowance_nonneg",
        ),
        CheckConstraint(
            "provider_reserved >= 0",
            name="ck_pm_balance_allowance_snapshots_reserved_nonneg",
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_pm_balance_allowance_snapshots_fencing_positive",
        ),
        CheckConstraint(
            "completeness IN ('COMPLETE','PARTIAL','STALE')",
            name="ck_pm_balance_allowance_snapshots_completeness_known",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pm_balance_allowance_snapshots_request_hash_hex",
        ),
        Index(
            "ix_pm_balance_allowance_snapshots_account",
            "account_id", "asset_key", "observed_at",
        ),
        UniqueConstraint(
            "account_id", "asset_key", "id",
            name="uq_pm_balance_snapshots_account_asset_id",
        ),
        {"schema": TRADING_SCHEMA},
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.pm_accounts.id",
            name="fk_pm_balance_allowance_snapshots_account",
        ),
        nullable=False,
    )
    asset_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    spender: Mapped[Optional[str]] = mapped_column(external_id_type())
    balance: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    allowance: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    provider_reserved: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    request_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False)


class AccountFundsCurrent(TradingBase, BigIntIdentityMixin, OptimisticVersionMixin, TimestampMixin):
    """可由 snapshot + reservation 重建的 CAS funds 投影；恒等式非负。"""

    __tablename__ = "account_funds_current"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "asset_key",
            name="uq_account_funds_current_account_asset",
        ),
        CheckConstraint("confirmed >= 0", name="ck_account_funds_current_confirmed_nonneg"),
        CheckConstraint(
            "provider_reserved >= 0",
            name="ck_account_funds_current_provider_reserved_nonneg",
        ),
        CheckConstraint(
            "local_reserved >= 0",
            name="ck_account_funds_current_local_reserved_nonneg",
        ),
        CheckConstraint("available >= 0", name="ck_account_funds_current_available_nonneg"),
        CheckConstraint(
            "available = confirmed - provider_reserved - local_reserved",
            name="ck_account_funds_current_identity",
        ),
        CheckConstraint(
            "reconcile_watermark >= 0",
            name="ck_account_funds_current_reconcile_watermark_nonneg",
        ),
        ForeignKeyConstraint(
            ("account_id", "asset_key", "source_snapshot_id"),
            (
                "trading.pm_balance_allowance_snapshots.account_id",
                "trading.pm_balance_allowance_snapshots.asset_key",
                "trading.pm_balance_allowance_snapshots.id",
            ),
            name="fk_account_funds_current_source_snapshot",
        ),
        {"schema": TRADING_SCHEMA},
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_account_funds_current_account"),
        nullable=False,
    )
    asset_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    confirmed: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    provider_reserved: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    local_reserved: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    available: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reconcile_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CapitalReservation(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """资金预留：intent/account/asset/idempotency 唯一；状态机 DB 强制。"""

    __tablename__ = "capital_reservations"
    __table_args__ = (
        UniqueConstraint("reservation_key", name="uq_capital_reservations_key"),
        UniqueConstraint(
            "account_id", "asset_key", "idempotency_key",
            name="uq_capital_reservations_idempotency",
        ),
        CheckConstraint("amount > 0", name="ck_capital_reservations_amount_positive"),
        CheckConstraint(
            "consumed_amount >= 0",
            name="ck_capital_reservations_consumed_nonneg",
        ),
        CheckConstraint(
            "released_amount >= 0",
            name="ck_capital_reservations_released_nonneg",
        ),
        CheckConstraint(
            "consumed_amount + released_amount <= amount",
            name="ck_capital_reservations_amount_accounted",
        ),
        CheckConstraint(
            "status NOT IN ('CONSUMED','RELEASED') OR "
            "consumed_amount + released_amount = amount",
            name="ck_capital_reservations_terminal_accounted",
        ),
        CheckConstraint(
            "status IN ('HELD','UNKNOWN','PROVIDER_BOUND','CONSUMED','RELEASED')",
            name="ck_capital_reservations_status_known",
        ),
        Index("ix_capital_reservations_intent", "intent_id"),
        Index("ix_capital_reservations_account", "account_id"),
        {"schema": TRADING_SCHEMA},
    )

    reservation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    intent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.economic_action_intents.id",
            name="fk_capital_reservations_intent",
        ),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_capital_reservations_account"),
        nullable=False,
    )
    asset_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    consumed_amount: Mapped[Decimal] = mapped_column(
        base_unit_type(), nullable=False, server_default="0"
    )
    released_amount: Mapped[Decimal] = mapped_column(
        base_unit_type(), nullable=False, server_default="0"
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="HELD")


class ExecutionLease(TradingBase, BigIntIdentityMixin, OptimisticVersionMixin, TimestampMixin):
    """per-account 单一 execution/heartbeat leader 租约；fencing token 单调。"""

    __tablename__ = "execution_leases"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "lease_role",
            name="uq_execution_leases_account_role",
        ),
        CheckConstraint(
            "lease_role IN ('EXECUTION','HEARTBEAT')",
            name="ck_execution_leases_role_known",
        ),
        CheckConstraint("fencing_token > 0", name="ck_execution_leases_fencing_positive"),
        CheckConstraint(
            "length(btrim(owner)) > 0",
            name="ck_execution_leases_owner_nonempty",
        ),
        {"schema": TRADING_SCHEMA},
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_accounts.id", name="fk_execution_leases_account"),
        nullable=False,
    )
    lease_role: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latest_heartbeat_id: Mapped[Optional[str]] = mapped_column(String(255))
    latest_heartbeat_hash: Mapped[Optional[str]] = mapped_column(sha256_type())
