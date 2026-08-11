"""Trading domain 纯函数包（WP-01C Checkpoint A）。

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
]
