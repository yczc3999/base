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

__all__ = [
    "canonical_bytes",
    "canonical_hash",
    "deterministic_sample",
    "assert_frozen_gate_binding",
    "apply_payout_lookup",
    "validate_payout_ir",
]
