"""Payout truth-table 纯函数（WP-01C Checkpoint A）。

payout IR 固定为 canonical lookup truth table：``{resolution_state: decimal-string}``。
- ``apply_payout_lookup``：给定 IR 与 resolution state，返回该 token 的兑付（Decimal）。
- ``validate_payout_ir``：核验 key 集与 ``R_c`` 完全一致、值均为有限 Decimal 且
  0≤payout≤1、无 float、无 NaN/Infinity（fail-closed）。

VOID/PARTIAL 是 resolution state；``OTHER`` 必须可判定；``UNKNOWN`` 不得作为终态。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
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


# ======================================================================
# WP-06 Checkpoint C —— CTF split/merge/redeem calldata 构建 + payout 一致性核验
# calldata 为确定性 hex（Solidity ABI），与 p6 relayer golden 全等；caller 不可覆盖。
# ======================================================================

from eth_abi import encode as _abi_encode
from eth_utils import keccak as _keccak


def function_selector(signature: str) -> str:
    """Solidity 函数选择器：keccak256(signature)[:4]。"""
    return "0x" + _keccak(signature.encode()).hex()[:8]


_SELECTOR_RE = re.compile(r"^0x[0-9a-fA-F]{8}$")


def _official_selector(signature: str, override: str | None) -> str:
    expected = function_selector(signature)
    if override is None:
        return expected
    if not isinstance(override, str) or not _SELECTOR_RE.fullmatch(override):
        raise ValueError("payout_selector_override_invalid")
    if override.lower() != expected:
        raise ValueError("payout_selector_override_forbidden")
    return expected


def _addr_arg(address: str) -> str:
    """32-byte 左填充地址参数。"""
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"payout_address_invalid:{address!r}")
    try:
        bytes.fromhex(address[2:])
    except ValueError as exc:
        raise ValueError(f"payout_address_invalid:{address!r}") from exc
    return address[2:].rjust(64, "0")


def _uint_arg(value: int | str) -> str:
    """32-byte uint 参数（非负十进制/十六进制）。"""
    if isinstance(value, bool):
        raise ValueError("payout_uint_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("payout_uint_float_forbidden")
    if not isinstance(value, (int, str)):
        raise ValueError("payout_uint_invalid")
    if isinstance(value, str):
        try:
            value = int(value, 16) if value.startswith("0x") else int(value)
        except ValueError as exc:
            raise ValueError("payout_uint_invalid") from exc
    if value < 0:
        raise ValueError("payout_uint_negative")
    if value >= 1 << 256:
        raise ValueError("payout_uint_overflow")
    return format(int(value), "064x")


def _bytes32_arg(value: str, *, path: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise ValueError(f"payout_{path}_invalid")
    try:
        return bytes.fromhex(value[2:])
    except ValueError as exc:
        raise ValueError(f"payout_{path}_invalid") from exc


def build_split_calldata(
    *,
    collateral_address: str,
    condition_id: str,
    parent_collection_id: str,
    partition: list[str],
    amount_base_units: int,
    selector_override: str | None = None,
) -> str:
    """``splitPosition(address,bytes32,bytes32,uint256[],uint256)``。"""
    selector = _official_selector(
        "splitPosition(address,bytes32,bytes32,uint256[],uint256)", selector_override
    )
    condition = _bytes32_arg(condition_id, path="condition")
    parent = _bytes32_arg(parent_collection_id, path="parent")
    _addr_arg(collateral_address)
    if not partition:
        raise ValueError("payout_partition_empty")
    values = [_uint_value(item) for item in partition]
    encoded = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
        [
            collateral_address,
            parent,
            condition,
            values,
            _uint_value(amount_base_units),
        ],
    )
    return selector + encoded.hex()


def build_merge_calldata(
    *,
    collateral_address: str,
    condition_id: str,
    parent_collection_id: str,
    partition: list[str],
    amount_base_units: int,
    selector_override: str | None = None,
) -> str:
    """``mergePositions(address,bytes32,bytes32,uint256[],uint256)``。"""
    selector = _official_selector(
        "mergePositions(address,bytes32,bytes32,uint256[],uint256)", selector_override
    )
    condition = _bytes32_arg(condition_id, path="condition")
    parent = _bytes32_arg(parent_collection_id, path="parent")
    _addr_arg(collateral_address)
    if not partition:
        raise ValueError("payout_partition_empty")
    values = [_uint_value(item) for item in partition]
    encoded = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
        [
            collateral_address,
            parent,
            condition,
            values,
            _uint_value(amount_base_units),
        ],
    )
    return selector + encoded.hex()


def build_redeem_calldata(
    *,
    collateral_address: str,
    condition_id: str,
    parent_collection_id: str,
    partition: list[str],
    selector_override: str | None = None,
) -> str:
    """``redeemPositions(address,bytes32,bytes32,uint256[])``（redeem 无 amount）。"""
    selector = _official_selector(
        "redeemPositions(address,bytes32,bytes32,uint256[])", selector_override
    )
    condition = _bytes32_arg(condition_id, path="condition")
    parent = _bytes32_arg(parent_collection_id, path="parent")
    _addr_arg(collateral_address)
    if not partition:
        raise ValueError("payout_partition_empty")
    values = [_uint_value(item) for item in partition]
    encoded = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            collateral_address,
            parent,
            condition,
            values,
        ],
    )
    return selector + encoded.hex()


def _uint_value(value: int | str) -> int:
    """Return a validated uint256 value used by the canonical ABI encoder."""
    encoded = _uint_arg(value)
    return int(encoded, 16)


def verify_payout_consistency(
    *,
    ctf_payout_outcome: str,
    ctf_numerator: str,
    ctf_denominator: str,
    clob_winner: str | None,
    clob_is_50_50: bool | None,
) -> bool:
    """CTF payout 与 CLOB winner/50-50 一致性核验。

    - 50-50：payout 1/2 + 1/2 且 clob is_50_50=true（winner 可为空）。
    - 二元：payout 1/0 且 clob winner 与 outcome_index 一致。
    - 任一缺失/冲突 → False（进入 SETTLEMENT_CONFLICT）。
    """
    if clob_is_50_50 is True:
        return ctf_numerator == "1" and ctf_denominator == "2" and (
            clob_winner is None or clob_winner in ("YES", "NO")
        )
    if clob_is_50_50 is False:
        if clob_winner is None:
            return False
        if clob_winner == ctf_payout_outcome:
            return ctf_numerator == "1" and ctf_denominator == "1"
        # 非 winner 面 payout 0/1
        return ctf_numerator == "0" and ctf_denominator == "1"
    # 缺 50-50 信号 → fail closed
    return False
