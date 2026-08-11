"""Trading 内部 typed command/query DTO（WP-01C）。

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
]
