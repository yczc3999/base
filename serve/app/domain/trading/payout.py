"""Payout truth-table 纯函数（WP-01C Checkpoint A）。

payout IR 固定为 canonical lookup truth table：``{resolution_state: decimal-string}``。
- ``apply_payout_lookup``：给定 IR 与 resolution state，返回该 token 的兑付（Decimal）。
- ``validate_payout_ir``：核验 key 集与 ``R_c`` 完全一致、值均为有限 Decimal 且
  0≤payout≤1、无 float、无 NaN/Infinity（fail-closed）。

VOID/PARTIAL 是 resolution state；``OTHER`` 必须可判定；``UNKNOWN`` 不得作为终态。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

ALLOWED_KNOWN_STATES = frozenset(
    {"YES", "NO", "VOID", "PARTIAL", "OTHER", "INVALID", "REFUND"}
)
# 不允许出现在 truth table key 里的伪终态
FORBIDDEN_STATES = frozenset({"UNKNOWN"})


def _to_decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{path}_bool_forbidden")
    if isinstance(value, float):
        raise ValueError(f"{path}_float_forbidden")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{path}_invalid_decimal") from exc
    if not dec.is_finite():
        raise ValueError(f"{path}_not_finite")
    return dec


def validate_payout_ir(
    ir: dict[str, Any],
    *,
    resolution_states: list[str],
) -> dict[str, Decimal]:
    """校验 payout IR 且返回 {state: Decimal}。失败抛 ValueError（固定 reason 含字段名）。"""
    if not isinstance(ir, dict):
        raise ValueError("payout_ir_not_object")
    keys = set(ir.keys())
    expected = set(resolution_states)
    if FORBIDDEN_STATES & keys:
        raise ValueError(f"payout_unknown_terminal:{sorted(FORBIDDEN_STATES & keys)}")
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ValueError(f"payout_key_mismatch:missing={missing},extra={extra}")
    out: dict[str, Decimal] = {}
    for state in resolution_states:
        dec = _to_decimal(ir[state], f"payout_{state}")
        if dec < 0 or dec > 1:
            raise ValueError(f"payout_out_of_range:{state}={dec}")
        out[state] = dec
    return out


def apply_payout_lookup(ir: dict[str, Any], resolution_state: str) -> Decimal:
    """从 canonical lookup truth table 取兑付；未知 key fail-closed。"""
    if resolution_state in FORBIDDEN_STATES:
        raise ValueError(f"payout_unknown_terminal:{resolution_state}")
    if resolution_state not in ir:
        raise ValueError(f"payout_missing_state:{resolution_state}")
    return _to_decimal(ir[resolution_state], f"payout_{resolution_state}")
