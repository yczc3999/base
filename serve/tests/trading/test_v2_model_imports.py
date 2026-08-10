"""
WP-01A-02 ORM kernel —— 显式导入验收。

``app/models/trading`` 与 ``app/models`` 只做显式 import/export；``app/models`` 必须显式
导入全部 trading metadata，供 Alembic 发现；禁止动态扫描、I/O 或重复 metadata。
"""

import app.models  # noqa: F401
from app.models import Base
from app.models.trading import (
    ArchiveManifest,
    ArtifactLineageEdge,
    ArtifactObject,
    CapitalPermissionManifest,
    ExecutionSpecVersion,
    IdempotencyClaim,
    JobCompletion,
    ModelRoleBinding,
    OutboxDeliveryHistory,
    PolicyFreeze,
    PolicyTypeScope,
    ReleaseManifest,
    RetentionManifest,
    RuntimeConfigVersion,
    SecretAccessEvent,
    SecretVaultEntry,
    SecretVaultVersion,
    StrategyObjectiveContract,
    StrategyVersion,
    TransactionalOutbox,
)
from app.models.trading.constants import TRADING_SCHEMA

EXPECTED = [
    "artifact_objects",
    "artifact_lineage_edges",
    "archive_manifests",
    "retention_manifests",
    "runtime_config_versions",
    "strategy_objective_contracts",
    "strategy_versions",
    "model_role_bindings",
    "execution_spec_versions",
    "capital_permission_manifests",
    "release_manifests",
    "policy_type_scopes",
    "policy_freezes",
    "secret_vault_entries",
    "secret_vault_versions",
    "secret_access_events",
    "idempotency_claims",
    "transactional_outbox",
    "outbox_delivery_history",
    "job_completions",
]


def test_trading_metadata_registered_on_shared_base():
    trading = {t.name for t in Base.metadata.tables.values() if t.schema == TRADING_SCHEMA}
    assert trading == set(EXPECTED)


def test_explicit_model_symbols_importable():
    symbols = {
        "artifact_objects": ArtifactObject,
        "artifact_lineage_edges": ArtifactLineageEdge,
        "archive_manifests": ArchiveManifest,
        "retention_manifests": RetentionManifest,
        "runtime_config_versions": RuntimeConfigVersion,
        "strategy_objective_contracts": StrategyObjectiveContract,
        "strategy_versions": StrategyVersion,
        "model_role_bindings": ModelRoleBinding,
        "execution_spec_versions": ExecutionSpecVersion,
        "capital_permission_manifests": CapitalPermissionManifest,
        "release_manifests": ReleaseManifest,
        "policy_type_scopes": PolicyTypeScope,
        "policy_freezes": PolicyFreeze,
        "secret_vault_entries": SecretVaultEntry,
        "secret_vault_versions": SecretVaultVersion,
        "secret_access_events": SecretAccessEvent,
        "idempotency_claims": IdempotencyClaim,
        "transactional_outbox": TransactionalOutbox,
        "outbox_delivery_history": OutboxDeliveryHistory,
        "job_completions": JobCompletion,
    }
    for tablename, model in symbols.items():
        assert model.__tablename__ == tablename
        assert model.__table__.schema == TRADING_SCHEMA


def test_app_models_exports_trading():
    exported = set(app.models.__all__)
    for model in (
        "ArtifactObject", "ArtifactLineageEdge", "ArchiveManifest", "RetentionManifest",
        "RuntimeConfigVersion", "StrategyObjectiveContract", "StrategyVersion",
        "ModelRoleBinding", "ExecutionSpecVersion", "CapitalPermissionManifest",
        "ReleaseManifest", "PolicyTypeScope", "PolicyFreeze",
        "SecretVaultEntry", "SecretVaultVersion", "SecretAccessEvent",
        "IdempotencyClaim", "TransactionalOutbox", "OutboxDeliveryHistory", "JobCompletion",
    ):
        assert model in exported, model


def test_no_duplicate_metadata_or_scan():
    # 同一物理表在 metadata 中只有一份；无动态扫描产生的重复
    from collections import Counter
    keys = Counter((t.schema, t.name) for t in Base.metadata.tables.values())
    assert all(v == 1 for v in keys.values()), [k for k, v in keys.items() if v > 1]
