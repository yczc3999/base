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
    PMEventTag,
    PMMarket,
    PMMarketCurrent,
    PMMarketLifecycleEvent,
    PMMarketVersion,
    PMTag,
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
from app.models.trading.forecast import (
    CoherenceCheck,
    EvidenceBundle,
    EvidenceBundleItem,
    EvidenceCoveragePolicy,
    EvidenceRevision,
    ForecastChallenge,
    ForecastInputManifest,
    ForecastLease,
    ForecastSubmission,
    PayoutProjection,
    Prior,
)
from app.models.trading.ai import (
    AIInvocation,
    AIToolCall,
    AIValidationResult,
)
from app.models.trading.decision import (
    ActionCandidate,
    ActionSet,
    ActionSetLeg,
    DiscrepancyReview,
    EconomicActionIntent,
    MarketRelativeDecision,
    ResolutionCashflow,
    TradeDecision,
    UnderwritingPlan,
)
from app.models.trading.execution import (
    AccountReconciliation,
    ExchangeOrder,
    ExchangeOrderAttempt,
    Execution,
    ExecutionAuthorizationEnvelope,
    ExchangeTrade,
    OrderStateEvent,
    Position,
    PositionLot,
)
from app.models.trading.ledger import (
    LedgerPosting,
    LedgerTransaction,
    OperatingCostEntry,
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
    AccountFundsCurrent,
    CapitalReservation,
    ExecutionLease,
    PMAccount,
    PMBalanceAllowanceSnapshot,
    SecretAccessEvent,
    SecretVaultEntry,
    SecretVaultVersion,
)
from app.models.trading.settlement import (
    ChainOperation,
    ChainOperationStateHistory,
    ContractRegistry,
    ResolutionCluster,
    ResolutionClusterMembership,
    ResolutionLabel,
    ScoreTarget,
    ScoreTargetMembership,
    SettlementObservation,
)
from app.models.trading.evaluation import (
    AblationRun,
    ChallengerVariant,
    ErrorReview,
    Experiment,
    ExperimentVariant,
    MetricRun,
    PromotionDecision,
    ScoreObservation,
)
from app.models.trading.audit import (
    AlertEvent,
    ExternalCallAttempt,
    ReplayRun,
    WorkflowEvent,
)
from app.models.trading.projection import (
    AccountRiskCurrent,
    LatestChainSummary,
    OpsHealthCurrent,
    PipelineFunnelHourly,
    ProviderCostDaily,
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
    "PMTag",
    "PMEventTag",
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
    # forecast
    "Prior",
    "EvidenceCoveragePolicy",
    "EvidenceRevision",
    "EvidenceBundle",
    "EvidenceBundleItem",
    "ForecastInputManifest",
    "ForecastSubmission",
    "PayoutProjection",
    "CoherenceCheck",
    "ForecastChallenge",
    "ForecastLease",
    # ai
    "AIInvocation",
    "AIToolCall",
    "AIValidationResult",
    # decision
    "MarketRelativeDecision",
    "DiscrepancyReview",
    "TradeDecision",
    "ActionCandidate",
    "ResolutionCashflow",
    "ActionSet",
    "ActionSetLeg",
    "UnderwritingPlan",
    "EconomicActionIntent",
    # execution
    "Execution",
    "Position",
    "PositionLot",
    # WP-05 Checkpoint C
    "ExecutionAuthorizationEnvelope",
    "ExchangeOrder",
    "ExchangeOrderAttempt",
    "OrderStateEvent",
    "ExchangeTrade",
    "AccountReconciliation",
    # ledger
    "LedgerTransaction",
    "LedgerPosting",
    "OperatingCostEntry",
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
    # WP-05 execution plane
    "PMAccount",
    "PMBalanceAllowanceSnapshot",
    "AccountFundsCurrent",
    "CapitalReservation",
    "ExecutionLease",
    # outbox
    "IdempotencyClaim",
    "TransactionalOutbox",
    "OutboxDeliveryHistory",
    "JobCompletion",
    # settlement (WP-04)
    "ResolutionLabel",
    "ResolutionCluster",
    "ResolutionClusterMembership",
    "ScoreTarget",
    "ScoreTargetMembership",
    # evaluation (WP-04)
    "ScoreObservation",
    "Experiment",
    "ExperimentVariant",
    "ChallengerVariant",
    "MetricRun",
    "ErrorReview",
    "AblationRun",
    "PromotionDecision",
    # audit (WP-04)
    "ReplayRun",
    # WP-05 Checkpoint C audit/ops
    "WorkflowEvent",
    "ExternalCallAttempt",
    "AlertEvent",
    # read projections (WP-04)
    "OpsHealthCurrent",
    "PipelineFunnelHourly",
    "AccountRiskCurrent",
    "ProviderCostDaily",
    "LatestChainSummary",
]
