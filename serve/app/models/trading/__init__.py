"""Trading ORM 包：显式导入全部模型，供 Alembic metadata 发现。

只做显式 import/export；禁止动态扫描、I/O 或重复 metadata（任务 §4.1）。
"""

from app.models.trading.artifact import (
    ArchiveManifest,
    ArtifactLineageEdge,
    ArtifactObject,
    RetentionManifest,
)
from app.models.trading.control import (
    CapitalPermissionManifest,
    ExecutionSpecVersion,
    ModelRoleBinding,
    PolicyFreeze,
    PolicyTypeScope,
    ReleaseManifest,
    RuntimeConfigVersion,
    StrategyObjectiveContract,
    StrategyVersion,
)
from app.models.trading.outbox import (
    IdempotencyClaim,
    JobCompletion,
    OutboxDeliveryHistory,
    TransactionalOutbox,
)
from app.models.trading.vault import (
    SecretAccessEvent,
    SecretVaultEntry,
    SecretVaultVersion,
)

__all__ = [
    # artifact
    "ArtifactObject",
    "ArtifactLineageEdge",
    "ArchiveManifest",
    "RetentionManifest",
    # control
    "RuntimeConfigVersion",
    "StrategyObjectiveContract",
    "StrategyVersion",
    "ModelRoleBinding",
    "ExecutionSpecVersion",
    "CapitalPermissionManifest",
    "ReleaseManifest",
    "PolicyTypeScope",
    "PolicyFreeze",
    # vault
    "SecretVaultEntry",
    "SecretVaultVersion",
    "SecretAccessEvent",
    # outbox
    "IdempotencyClaim",
    "TransactionalOutbox",
    "OutboxDeliveryHistory",
    "JobCompletion",
]
