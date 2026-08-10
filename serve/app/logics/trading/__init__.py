"""Trading Logic 包（WP-01B Checkpoint D）。

Logic 拥有业务规则；Repository 只做 SQL；runtime 只编排。
"""

from app.logics.trading.universe import (
    ApplyDiffResult,
    UniverseLogic,
    UniversePolicy,
    canonical_hash,
    market_is_eligible,
    market_normalized_content,
)
from app.logics.trading.market_data import (
    BookState,
    FreshnessDecision,
    FreshnessPolicy,
    apply_delta,
    freshness,
    price_change_freshness,
    snapshot_book,
)

__all__ = [
    "ApplyDiffResult",
    "UniverseLogic",
    "UniversePolicy",
    "canonical_hash",
    "market_is_eligible",
    "market_normalized_content",
    "BookState",
    "FreshnessDecision",
    "FreshnessPolicy",
    "apply_delta",
    "freshness",
    "price_change_freshness",
    "snapshot_book",
]
