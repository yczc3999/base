"""Trading control 域 foundation models（WP-01A-02）。

control 对象保存 canonical JSONB + schema version + content hash + status + creator + time；
内容变更新增版本行（append-only 事实），status 为生命周期元数据。``release_manifests``
以 FK 固定 config/strategy/execution/permission 的具体版本，激活记录不依赖 "latest"。
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
    TimestampMixin,
    TradingBase,
)
from app.models.trading.types import base_unit_type, external_id_type, sha256_type

# 受控状态集合（不创建 PG ENUM；用 text + CHECK）。
_CTRL_STATUS = ("draft", "active", "retired", "rejected")


def _status_check(table: str, extra: str = "") -> CheckConstraint:
    values = ", ".join(f"'{s}'" for s in _CTRL_STATUS)
    return CheckConstraint(
        f"status IN ({values})",
        name=f"ck_{table}_status_known",
    )


def _content_check(table: str, col: str = "content") -> CheckConstraint:
    return CheckConstraint(
        f"jsonb_typeof({col}) = 'object'",
        name=f"ck_{table}_{col}_object",
    )


class RuntimeConfigVersion(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """运行配置的不可变版本行。"""

    __tablename__ = "runtime_config_versions"
    __table_args__ = (
        UniqueConstraint("config_key", "version_no", name="uq_runtime_config_versions_key_version"),
        _content_check("runtime_config_versions"),
        _status_check("runtime_config_versions"),
        {"schema": TRADING_SCHEMA},
    )

    config_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class StrategyObjectiveContract(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """策略目标契约版本行。"""

    __tablename__ = "strategy_objective_contracts"
    __table_args__ = (
        UniqueConstraint("contract_key", "version_no", name="uq_strategy_objective_contracts_key_version"),
        _content_check("strategy_objective_contracts"),
        _status_check("strategy_objective_contracts"),
        {"schema": TRADING_SCHEMA},
    )

    contract_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class StrategyVersion(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """策略实现版本行。"""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_key", "version_no", name="uq_strategy_versions_key_version"),
        _content_check("strategy_versions"),
        _status_check("strategy_versions"),
        {"schema": TRADING_SCHEMA},
    )

    strategy_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class ModelRoleBinding(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """同一 strategy version 内 role 唯一的模型绑定。"""

    __tablename__ = "model_role_bindings"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "role", name="uq_model_role_bindings_strategy_role"),
        {"schema": TRADING_SCHEMA},
    )

    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_model_role_bindings_strategy_version"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    model_ref: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class ExecutionSpecVersion(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """执行 spec 版本行。"""

    __tablename__ = "execution_spec_versions"
    __table_args__ = (
        UniqueConstraint("spec_key", "version_no", name="uq_execution_spec_versions_key_version"),
        _content_check("execution_spec_versions"),
        _status_check("execution_spec_versions"),
        {"schema": TRADING_SCHEMA},
    )

    spec_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class CapitalPermissionManifest(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """资金权限 manifest：分开 evaluation capital 与 authorized capital。"""

    __tablename__ = "capital_permission_manifests"
    __table_args__ = (
        CheckConstraint(
            "evaluation_capital >= 0",
            name="ck_capital_permission_manifests_eval_cap_nonneg",
        ),
        CheckConstraint(
            "authorized_capital >= 0",
            name="ck_capital_permission_manifests_auth_cap_nonneg",
        ),
        CheckConstraint(
            "mode IN ('shadow','canary','live')",
            name="ck_capital_permission_manifests_mode_known",
        ),
        _content_check("capital_permission_manifests", "capability"),
        _content_check("capital_permission_manifests", "limits"),
        _status_check("capital_permission_manifests"),
        {"schema": TRADING_SCHEMA},
    )

    name: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    capability: Mapped[dict] = mapped_column(JSONB, nullable=False)
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluation_capital: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    authorized_capital: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class ReleaseManifest(TradingBase, BigIntIdentityMixin, TimestampMixin):
    """release manifest：以 FK 固定 config/strategy/execution/permission 具体版本。"""

    __tablename__ = "release_manifests"
    __table_args__ = (
        _status_check("release_manifests"),
        {"schema": TRADING_SCHEMA},
    )

    release_name: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    config_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.runtime_config_versions.id", name="fk_release_manifests_config"),
        nullable=False,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.strategy_versions.id", name="fk_release_manifests_strategy"),
        nullable=False,
    )
    execution_spec_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.execution_spec_versions.id", name="fk_release_manifests_execution"),
        nullable=False,
    )
    capital_permission_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.capital_permission_manifests.id",
            name="fk_release_manifests_capital",
        ),
        nullable=False,
    )
    git_sha: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    db_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    total_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    creator: Mapped[str | None] = mapped_column(external_id_type())


class PolicyTypeScope(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """policy 类型 → scope 映射；三元组唯一。"""

    __tablename__ = "policy_type_scopes"
    __table_args__ = (
        UniqueConstraint(
            "policy_type", "scope_type", "scope_key",
            name="uq_policy_type_scopes_triple",
        ),
        {"schema": TRADING_SCHEMA},
    )

    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)


class PolicyFreeze(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """policy 冻结：引用精确 content hash + release，不读运行中 latest。"""

    __tablename__ = "policy_freezes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('frozen','released')",
            name="ck_policy_freezes_status_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    policy_content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.release_manifests.id", name="fk_policy_freezes_release"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="frozen")
