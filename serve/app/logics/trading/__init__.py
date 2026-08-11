"""Trading Logic 包（WP-01C）。

Logic 拥有业务规则；Repository 只做 SQL；orchestrator 定义跨 Gate 顺序。
"""

from app.logics.trading.component import ComponentLogic, G2Result
from app.logics.trading.contract import ContractLogic, G1Result
from app.logics.trading.market_data import (
    BookState,
    FreshnessDecision,
    FreshnessPolicy,
    apply_delta,
    freshness,
    snapshot_book,
)
from app.logics.trading.screening import (
    G0Result,
    R0Result,
    ScreeningLogic,
)
from app.logics.trading.universe import (
    ApplyDiffResult,
    UniverseLogic,
    UniversePolicy,
    canonical_hash,
    market_is_eligible,
    market_normalized_content,
)

__all__ = [
    "ComponentLogic",
    "G2Result",
    "ContractLogic",
    "G1Result",
    "ScreeningLogic",
    "G0Result",
    "R0Result",
    "BookState",
    "FreshnessDecision",
    "FreshnessPolicy",
    "apply_delta",
    "freshness",
    "snapshot_book",
    "ApplyDiffResult",
    "UniverseLogic",
    "UniversePolicy",
    "canonical_hash",
    "market_is_eligible",
    "market_normalized_content",
]
