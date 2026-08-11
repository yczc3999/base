"""Trading 内部 typed command/query DTO（WP-01C / WP-02 / WP-03）。

只表达 typed input，不复用 ORM class、不发网络、不实现 Gate（实施合同 §5）。
Gate 判定在 Logic；本包只做严格解析/规范化。
"""

from app.schemas.trading.semantics import (
    ContractSpecInput,
    PayoutIRInput,
    WorldStateAssignmentInput,
    WorldSchemaInput,
)
from app.schemas.trading.workflow import (
    G0ObjectiveInput,
    HydratedFrameMarketInput,
    HydratedUniverseFrameInput,
    R0Input,
    R0BatchItemInput,
    R0PolicyInput,
    RejectAuditPolicyInput,
)
from app.schemas.trading.evidence import (
    PriorInput,
    EvidenceRevisionInput,
    EvidenceCoveragePolicyInput,
    EvidenceBundleInput,
)
from app.schemas.trading.forecast import (
    QDistributionInput,
    ForecastSubmissionInput,
    PayoutProjectionInput,
    ForecastLeaseInput,
    CoherenceCheckInput,
)
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    QuoteRevealInput,
    UnderwritingInput,
)
from app.schemas.trading.execution import (
    AccountInput,
    CancelOrderInput,
    EnvelopeInput,
    FenceAssertInput,
    FundsUpsertInput,
    LeaseAcquireInput,
    LeaseRenewInput,
    PositionUpdateInput,
    ReconcileInput,
    ReservationAdvanceInput,
    ReservationInput,
    ShadowFillInput,
    SubmitOrderInput,
)
from app.schemas.trading.settlement import (
    ClusterInput,
    LabelRevisionInput,
    ScoreTargetInput,
)
from app.schemas.trading.evaluation import (
    ExperimentInput,
    MetricRunInput,
    PromotionDecisionInput,
    ReplayRunInput,
    ScoreObservationInput,
)

__all__ = [
    "ContractSpecInput",
    "PayoutIRInput",
    "WorldStateAssignmentInput",
    "WorldSchemaInput",
    "G0ObjectiveInput",
    "HydratedFrameMarketInput",
    "HydratedUniverseFrameInput",
    "R0Input",
    "R0BatchItemInput",
    "R0PolicyInput",
    "RejectAuditPolicyInput",
    "PriorInput",
    "EvidenceRevisionInput",
    "EvidenceCoveragePolicyInput",
    "EvidenceBundleInput",
    "QDistributionInput",
    "ForecastSubmissionInput",
    "PayoutProjectionInput",
    "ForecastLeaseInput",
    "CoherenceCheckInput",
    "QuoteRevealInput",
    "MarketRelativeInput",
    "ActionCandidateInput",
    "PortfolioGateInput",
    "ActionSetInput",
    "UnderwritingInput",
    "ShadowFillInput",
    "PositionUpdateInput",
    # WP-05 execution plane
    "AccountInput",
    "FundsUpsertInput",
    "ReservationInput",
    "ReservationAdvanceInput",
    "LeaseAcquireInput",
    "LeaseRenewInput",
    "FenceAssertInput",
    # WP-05 Checkpoint C private CLOB
    "EnvelopeInput",
    "SubmitOrderInput",
    "CancelOrderInput",
    "ReconcileInput",
    # settlement (WP-04)
    "LabelRevisionInput",
    "ClusterInput",
    "ScoreTargetInput",
    # evaluation (WP-04)
    "ScoreObservationInput",
    "ExperimentInput",
    "MetricRunInput",
    "PromotionDecisionInput",
    "ReplayRunInput",
]
