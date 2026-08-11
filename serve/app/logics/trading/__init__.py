"""Trading Logic 包（WP-01C / WP-02）。

Logic 拥有业务规则；Repository 只做 SQL；orchestrator 定义跨 Gate 顺序。
"""

from app.logics.trading.component import ComponentLogic, G2Result
from app.logics.trading.contract import ContractLogic, G1Result
from app.logics.trading.evidence import (
    EvidenceLogic,
    G4Result,
    G5AResult,
    G5BResult,
)
from app.logics.trading.forecast import ForecastLogic, G6Result, InputManifestMaterial
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
    "EvidenceLogic",
    "G4Result",
    "G5AResult",
    "G5BResult",
    "ForecastLogic",
    "G6Result",
    "InputManifestMaterial",
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
