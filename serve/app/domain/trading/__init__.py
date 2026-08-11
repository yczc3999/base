"""Trading domain 纯函数包（WP-01C / WP-02 / WP-03）。

无数据库、无网络、无隐式 clock 的确定性函数（实施合同 §6）。Logic 负责编排并生成 reason code。
"""

from app.domain.trading.hashing import (
    canonical_bytes,
    canonical_hash,
    deterministic_sample,
)
from app.domain.trading.gates import assert_frozen_gate_binding
from app.domain.trading.payout import (
    apply_payout_lookup,
    validate_payout_ir,
)
from app.domain.trading.probability import (
    bernoulli_p_blind,
    expected_payout,
    normalize_q,
    payout_bounds,
    push_forward_mu,
    validate_u,
)
from app.domain.trading.rounding import (
    cash_to_share_value,
    floor_quantity,
    round_cash,
    round_price,
    round_quantity,
    shares_to_cash,
    tick_ceil,
    tick_floor,
)
from app.domain.trading.valuation import (
    CostComponents,
    DepthFill,
    ValuationResult,
    break_even_payout_probability,
    capital_days,
    depth_walk,
    edge_delay_erosion,
    expected_log_growth,
    full_cost_delta,
    robust_ev,
    roi,
    world_delta_w,
    worst_loss,
)
from app.domain.trading.portfolio import (
    CapCheck,
    LegExposure,
    cap_check,
    marginal_log_growth_delta,
    net_risk_capital,
    worst_loss_cvar,
)
from app.domain.trading.ledger import (
    ASSET_CASH,
    ASSET_TOKEN,
    Posting,
    build_buy_postings,
    build_reversal,
    imbalance,
    net_cash_flow,
    postings_balanced,
)

__all__ = [
    "canonical_bytes",
    "canonical_hash",
    "deterministic_sample",
    "assert_frozen_gate_binding",
    "apply_payout_lookup",
    "validate_payout_ir",
    "normalize_q",
    "validate_u",
    "push_forward_mu",
    "expected_payout",
    "payout_bounds",
    "bernoulli_p_blind",
    "round_price",
    "round_cash",
    "round_quantity",
    "floor_quantity",
    "shares_to_cash",
    "cash_to_share_value",
    "tick_floor",
    "tick_ceil",
    "CostComponents",
    "DepthFill",
    "ValuationResult",
    "depth_walk",
    "full_cost_delta",
    "world_delta_w",
    "robust_ev",
    "roi",
    "expected_log_growth",
    "worst_loss",
    "break_even_payout_probability",
    "capital_days",
    "edge_delay_erosion",
    "CapCheck",
    "LegExposure",
    "cap_check",
    "marginal_log_growth_delta",
    "net_risk_capital",
    "worst_loss_cvar",
    "ASSET_CASH",
    "ASSET_TOKEN",
    "Posting",
    "build_buy_postings",
    "build_reversal",
    "imbalance",
    "net_cash_flow",
    "postings_balanced",
]
