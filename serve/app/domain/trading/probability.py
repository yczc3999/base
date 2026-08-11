"""Deterministic probability coherence, push-forward, and payout projection（WP-02 Checkpoint A）。

架构 §1.0 / §4.2 / §4.3 的纯函数实现。无 DB、无网络、无隐式 clock；全部用 Decimal。

- ``normalize_q``：world-state 联合分布非负、和为 1（fail-closed）。
- ``validate_u``：U 非空、去重、每个成员与 Q 同 key 集且各自 total、且 ``Q ∈ U``。
- ``push_forward_mu``：``μ_{c,t} = Q ∘ (g_{c,t} ∘ h_c)^{-1}``（有限状态=按 payout 求和）。
- ``expected_payout`` / ``payout_bounds``：``V = ∫ y dμ(y)`` 与 U 下界/上界。
- ``bernoulli_p_blind``：仅当 μ 为 Bernoulli（兑付只含 0/1）时派生 nullable ``p_blind``。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

_DECIMAL_EPSILON = Decimal("1e-12")


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


def normalize_q(values: dict[str, Any]) -> dict[str, Decimal]:
    """校验并返回 ``{world_state_id: Decimal}``：非负、和为 1、无 float/NaN。

    不要求 key 集预先匹配 schema（由调用方提供 world_state_ids 时另行判定）；
    这里只保证每个值合法且 total=1。
    """
    if not isinstance(values, dict) or not values:
        raise ValueError("q_empty")
    out: dict[str, Decimal] = {}
    total = Decimal(0)
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError("q_state_key_invalid")
        dec = _to_decimal(value, f"q[{key}]")
        if dec < 0:
            raise ValueError(f"q_negative:{key}")
        out[key] = dec
        total += dec
    if abs(total - Decimal(1)) > _DECIMAL_EPSILON:
        raise ValueError(f"q_not_total:{total.normalize()}")
    return out


def _q_key_set(q: dict[str, Decimal]) -> frozenset[str]:
    return frozenset(q.keys())


def validate_u(
    u: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    q: dict[str, Decimal],
) -> list[dict[str, Decimal]]:
    """U 非空、去重、每个成员合法且与 Q 同 key 集、且 ``Q ∈ U``。

    成员去重按 canonical sorted repr（值相等即去重，顺序无关）。任何一条失败 fail-closed。
    """
    if not isinstance(u, (list, tuple)) or not u:
        raise ValueError("u_empty")
    q_keys = _q_key_set(q)
    seen: set[str] = set()
    out: list[dict[str, Decimal]] = []
    q_serialized = _serialize_distribution(q)
    for index, member in enumerate(u):
        if not isinstance(member, dict):
            raise ValueError(f"u_member_not_object:{index}")
        parsed = normalize_q(member)
        if _q_key_set(parsed) != q_keys:
            raise ValueError(f"u_key_mismatch:{index}")
        serialized = _serialize_distribution(parsed)
        if serialized in seen:
            raise ValueError(f"u_duplicate_member:{index}")
        seen.add(serialized)
        out.append(parsed)
    if q_serialized not in seen:
        raise ValueError("u_must_contain_q")
    return out


def _serialize_distribution(dist: dict[str, Decimal]) -> str:
    return repr(sorted((key, format(value.normalize(), "f")) for key, value in dist.items()))


def push_forward_mu(
    q: dict[str, Decimal],
    *,
    h_c: dict[str, str],
    payout_ir: dict[str, Any],
) -> dict[str, Decimal]:
    """``μ = Q ∘ (g ∘ h)^{-1}``：每个 world state 概率按 (h_c, payout) 归并。

    - ``h_c``: ``{world_state_id: resolution_state}``，必须 total（对每个 q key 有定义）。
    - ``payout_ir``: ``{resolution_state: payout-decimal}``，必须覆盖所有 h_c 输出。
    返回 ``{payout_decimal_string: probability}``。
    """
    payout_map: dict[str, Decimal] = {}
    for state, prob in q.items():
        resolution = h_c.get(state)
        if resolution is None:
            raise ValueError(f"h_c_not_total:{state}")
        if resolution not in payout_ir:
            raise ValueError(f"payout_missing_state:{resolution}")
        payout = _to_decimal(payout_ir[resolution], f"payout[{resolution}]")
        if payout < 0 or payout > 1:
            raise ValueError(f"payout_out_of_range:{resolution}={payout}")
        key = format(payout.normalize(), "f")
        payout_map[key] = payout_map.get(key, Decimal(0)) + prob
    return payout_map


def expected_payout(mu: dict[str, Decimal]) -> Decimal:
    """``V = ∫ y dμ(y) = Σ_payout payout × probability``。"""
    total = Decimal(0)
    for payout_text, prob in mu.items():
        payout = _to_decimal(payout_text, "payout_value")
        total += payout * prob
    return total


def payout_bounds(
    members: list[dict[str, Decimal]],
    *,
    h_c: dict[str, str],
    payout_ir: dict[str, Any],
) -> tuple[Decimal, Decimal]:
    """U 下每个成员 push-forward 的期望兑付最小/最大（U 下界/上界）。"""
    if not members:
        raise ValueError("u_empty")
    lows: list[Decimal] = []
    highs: list[Decimal] = []
    for member in members:
        mu = push_forward_mu(member, h_c=h_c, payout_ir=payout_ir)
        expected = expected_payout(mu)
        lows.append(expected)
        highs.append(expected)
    return min(lows), max(highs)


def bernoulli_p_blind(mu: dict[str, Decimal]) -> Decimal | None:
    """仅当 μ 为 Bernoulli（payout 集为 {0,1}）时返回期望=1 的概率；否则 None。

    Bernoulli 判定：所有 payout 值必须等于 0 或 1，且 0/1 都存在（或只剩单边但概率
    total=1 的退化仍视为 Bernoulli，如 p=1 或 p=0）。
    """
    keys = set(mu.keys())
    allowed = {"0", "0.0", "1", "1.0"}
    if not keys.issubset(allowed):
        return None
    return expected_payout(mu)
