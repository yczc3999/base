"""G7B 最小组合与保险丝（WP-03 Checkpoint C）。

- ``net_risk_capital``：按 position/lot 净敞口（BUY/ADD 正 exposure，REDUCE/CLOSE 负）。
- ``cap_check``：同 market ≤4%、同 component ≤6%、全局 ≤30%（可更低不可更高）；
  并发候选合计不得突破（调用方以 SELECT FOR UPDATE / advisory lock 原子计算）。
- ``marginal_log_growth_delta``：候选相对 NO_ACTION 的边际期望对数增长。
- ``worst_loss_cvar``：U 下 worst loss / CVaR（简单实现：最差 α 分位平均）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.trading.rounding import round_price, round_quantity
from app.domain.trading.valuation import expected_log_growth

ZERO = Decimal("0")
_DECIMAL_ONE = Decimal("1")


def _dec(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("portfolio_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("portfolio_float_forbidden")
    return Decimal(str(value))


@dataclass(frozen=True)
class LegExposure:
    """一条 leg 的净敞口贡献（base-unit quantity，正=增加 exposure）。"""

    market_id: int
    component_id: int
    contract_spec_id: int
    token_id: int
    quantity: Decimal


def net_risk_capital(
    legs: list[LegExposure],
    *,
    bankroll: Decimal | str,
    price: Decimal | str,
) -> dict[str, Decimal]:
    """按 leg 净敞口 × 价格估算占用资本（buy 用当前价，sell 用 -quantity）。"""
    price_dec = _dec(price)
    bankroll_dec = _dec(bankroll)
    if bankroll_dec <= 0:
        raise ValueError("portfolio_bankroll_nonpositive")
    total = ZERO
    for leg in legs:
        total += leg.quantity * price_dec
    capital = abs(total)
    return {"capital": capital, "fraction": capital / bankroll_dec}


@dataclass(frozen=True)
class CapCheck:
    ok: bool
    per_market_fraction: Decimal
    per_component_fraction: Decimal
    global_fraction: Decimal
    reason: str | None = None


def cap_check(
    *,
    market_exposure: Decimal,
    component_exposure: Decimal,
    global_exposure: Decimal,
    bankroll: Decimal | str,
    per_market_cap: Decimal | str = "0.04",
    per_component_cap: Decimal | str = "0.06",
    global_cap: Decimal | str = "0.30",
) -> CapCheck:
    """4%/6%/30% 或更低 permission cap；任何一条越限 → fail-closed。"""
    bankroll_dec = _dec(bankroll)
    if bankroll_dec <= 0:
        raise ValueError("portfolio_bankroll_nonpositive")
    per_market = market_exposure / bankroll_dec
    per_component = component_exposure / bankroll_dec
    global_fraction = global_exposure / bankroll_dec
    if per_market > _dec(per_market_cap):
        return CapCheck(False, per_market, per_component, global_fraction, "per_market_cap_exceeded")
    if per_component > _dec(per_component_cap):
        return CapCheck(False, per_market, per_component, global_fraction, "per_component_cap_exceeded")
    if global_fraction > _dec(global_cap):
        return CapCheck(False, per_market, per_component, global_fraction, "global_cap_exceeded")
    return CapCheck(True, per_market, per_component, global_fraction)


def marginal_log_growth_delta(
    members: list[dict[str, Decimal]],
    world_delta: dict[str, Decimal],
    *,
    bankroll: Decimal | str,
    base_world_delta: dict[str, Decimal],
) -> Decimal:
    """候选相对 NO_ACTION 的边际期望对数增长（= E[ln(1+ΔW/B)] - base）。"""
    candidate = expected_log_growth(members, world_delta, bankroll=bankroll)
    base = expected_log_growth(members, base_world_delta, bankroll=bankroll)
    return round_price(candidate - base)


def worst_loss_cvar(
    members: list[dict[str, Decimal]],
    world_delta: dict[str, Decimal],
    *,
    alpha: Decimal | str = "0.05",
) -> Decimal:
    """U 下最差 α 分位平均损失（CVaR 简化实现）。

    对每个 member 计算期望（ΔW 越负越差）；取所有 member 期望中的最小值。
    若调用方需要更细的尾部，可改为按排序后的最差状态平均。
    """
    alpha_dec = _dec(alpha)
    if not (ZERO < alpha_dec <= _DECIMAL_ONE):
        raise ValueError("portfolio_alpha_out_of_range")
    evs: list[Decimal] = []
    for member in members:
        total = ZERO
        for state, prob in member.items():
            if state not in world_delta:
                raise ValueError(f"portfolio_world_delta_missing:{state}")
            total += prob * world_delta[state]
        evs.append(total)
    if not evs:
        raise ValueError("portfolio_u_empty")
    # 保守：取最差成员期望（下限），不依赖排序假设。
    return min(evs)
