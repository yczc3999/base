"""Trading ORM 包：显式导入全部模型，供 Alembic metadata 发现。

只做显式 import/export；禁止动态扫描、I/O 或重复 metadata（任务 §4.1）。
"""

from app.models.trading.artifact import (
    ArchiveManifest,
    ArtifactLineageEdge,
    ArtifactObject,
    RetentionManifest,
)
from app.models.trading.market import (
    PMEvent,
    PMMarket,
    PMMarketCurrent,
    PMMarketLifecycleEvent,
    PMMarketVersion,
    PMToken,
    PMTokenVersion,
    PUniverseFrame,
    PUniverseFramePage,
)
from app.models.trading.market_stream import (
    PMBookCheckpoint,
    PMBookCurrent,
    PMBookLevel,
    PMConnectionEpoch,
    PMQuoteBinding,
    PMSourceEventBatch,
    PMSourceEventIndex,
)
from app.models.trading.cohort import (
    AuditSample,
    EvaluationCohort,
    ScreeningEpisode,
    UniverseMembership,
)
from app.models.trading.semantics import (
    ContractSnapshot,
    ContractSpec,
    ForecastComponent,
    ForecastComponentContractSpec,
    ForecastComponentVersion,
    PayoutFunction,
    PortfolioDependencyEdge,
    WorldSchemaVersion,
)
from app.models.trading.workflow import (
    DecisionOpportunity,
    DecisionOpportunityMarket,
    EpisodeContractSpec,
    EpisodeMembership,
    ForecastEpisode,
    GateDecision,
    InformationSnapshot,
    InformationSnapshotItem,
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
    # market master
    "PUniverseFrame",
    "PUniverseFramePage",
    "PMEvent",
    "PMMarket",
    "PMMarketVersion",
    "PMToken",
    "PMTokenVersion",
    "PMMarketLifecycleEvent",
    "PMMarketCurrent",
    # market stream / book
    "PMConnectionEpoch",
    "PMSourceEventBatch",
    "PMSourceEventIndex",
    "PMBookCheckpoint",
    "PMBookLevel",
    "PMBookCurrent",
    "PMQuoteBinding",
    # semantics
    "ContractSnapshot",
    "ContractSpec",
    "PayoutFunction",
    "ForecastComponent",
    "ForecastComponentVersion",
    "ForecastComponentContractSpec",
    "WorldSchemaVersion",
    "PortfolioDependencyEdge",
    # cohort
    "EvaluationCohort",
    "UniverseMembership",
    "ScreeningEpisode",
    "AuditSample",
    # workflow
    "DecisionOpportunity",
    "DecisionOpportunityMarket",
    "ForecastEpisode",
    "EpisodeContractSpec",
    "EpisodeMembership",
    "InformationSnapshot",
    "InformationSnapshotItem",
    "GateDecision",
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
