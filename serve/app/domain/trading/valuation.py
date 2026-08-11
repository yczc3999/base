"""G7A 全成本价值与 deterministic depth walk（WP-03 Checkpoint B）。

- ``depth_walk``：按 exact quote checkpoint 深度做确定性、保守 walk；无 midpoint fill、
  不造 book 中不存在的数量。返回 (fill_quantity, vwap, remaining, fee)。
- ``full_cost_delta``：``ΔW(ω) = settlement - entry_cashflow - fee - adjustment
  - funding_discount - capital_charge - allocated_operating_cost``；每项只出现一次，
  ``cashflow_reconciliation_residual`` 必须为 0。
- ``robust_ev``：``min_{Q∈U_decision} E_Q[ΔW]``。
- 辅助指标：ROI、expected log-growth、worst loss、break-even payout probability、
  capital-days、edge delay erosion。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.trading.probability import expected_payout, normalize_q, push_forward_mu
from app.domain.trading.rounding import floor_quantity, round_cash, round_price, round_quantity

ZERO = Decimal("0")


def _dec(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("valuation_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("valuation_float_forbidden")
    return Decimal(str(value))


@dataclass(frozen=True)
class DepthFill:
    """确定性深度 walk 结果。"""

    fill_quantity: Decimal
    vwap: Decimal
    remaining_quantity: Decimal
    fee: Decimal
    complete: bool
    unfilled_reason: str | None = None


@dataclass(frozen=True)
class CostComponents:
    """互斥会计分量；任何分量出现即从 ΔW 扣除，禁止双计。"""

    executable_entry_cashflow: Decimal = ZERO
    explicit_fee: Decimal = ZERO
    execution_adjustment: Decimal = ZERO
    funding_or_discount_adjustment: Decimal = ZERO
    capital_charge: Decimal = ZERO
    allocated_marginal_operating_cost: Decimal = ZERO

    def total(self) -> Decimal:
        return (
            self.executable_entry_cashflow
            + self.explicit_fee
            + self.execution_adjustment
            + self.funding_or_discount_adjustment
            + self.capital_charge
            + self.allocated_marginal_operating_cost
        )


@dataclass(frozen=True)
class ValuationResult:
    """单个 action × world-state 的全成本价值。"""

    world_state_id: str
    settlement_cashflow: Decimal
    cost: CostComponents
    delta_w: Decimal
    cashflow_reconciliation_residual: Decimal = ZERO


def depth_walk(
    levels: list[tuple[Decimal | str, Decimal | str]],
    *,
    side: str,
    target_quantity: Decimal | str,
    taker_fee_bps: Decimal | str = "0",
) -> DepthFill:
    """确定性深度 walk。

    - ``side='buy'``：按 ask 从低到高消耗；``side='sell'``：按 bid 从高到低。
    - 只成交可成交数量；不足部分 remaining>0，complete=False。
    - VWAP = 总现金 / 总数量；fee = taker_fee_bps 按成交现金计提。
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"valuation_side_unknown:{side}")
    target = round_quantity(_dec(target_quantity))
    if target <= 0:
        raise ValueError("valuation_target_quantity_nonpositive")
    if not levels:
        return DepthFill(ZERO, ZERO, target, ZERO, False, "empty_book")
    ordered = []
    for raw_price, raw_size in levels:
        price = round_price(_dec(raw_price))
        size = floor_quantity(_dec(raw_size))
        if price <= 0 or price > 1:
            raise ValueError("valuation_price_out_of_range")
        if size <= 0:
            raise ValueError("valuation_level_size_nonpositive")
        ordered.append((price, size))
    ordered.sort(key=lambda item: item[0], reverse=(side == "sell"))
    remaining = target
    total_qty = ZERO
    total_cash = ZERO
    for price, size in ordered:
        if size <= 0 or remaining <= 0:
            continue
        take = min(remaining, size)
        total_qty += take
        total_cash += take * price
        remaining -= take
        if remaining <= 0:
            break
    fill_quantity = round_quantity(total_qty)
    vwap = round_price(total_cash / fill_quantity) if fill_quantity > 0 else ZERO
    fee_rate = _dec(taker_fee_bps) / Decimal("10000")
    fee = round_cash(total_cash * fee_rate)
    return DepthFill(
        fill_quantity=fill_quantity,
        vwap=vwap,
        remaining_quantity=remaining,
        fee=fee,
        complete=remaining <= 0,
        unfilled_reason=None if remaining <= 0 else "insufficient_depth",
    )


def full_cost_delta(
    *,
    settlement_cashflow: Decimal | str,
    cost: CostComponents,
) -> ValuationResult:
    """ΔW(ω) = settlement - Σcost；reconciliation residual 必须为 0。"""
    settlement = _dec(settlement_cashflow)
    delta = round_cash(settlement - cost.total())
    return ValuationResult(
        world_state_id="",
        settlement_cashflow=round_cash(settlement),
        cost=cost,
        delta_w=delta,
        cashflow_reconciliation_residual=ZERO,
    )


def world_delta_w(
    q: dict[str, Decimal],
    *,
    h_c: dict[str, str],
    payout_ir: dict[str, Decimal | str],
    cost: CostComponents,
    token_quantity: Decimal | str,
    token_vwap: Decimal | str,
    side: str = "buy",
) -> dict[str, Decimal]:
    """每个 world-state 的 ΔW：settlement(ω) - cost。

    ``settlement(ω) = quantity × payout(h_c(ω))``；entry cashflow = quantity × vwap。
    """
    quantity = _dec(token_quantity)
    vwap = _dec(token_vwap)
    entry = round_cash(quantity * vwap)
    if side not in ("buy", "sell"):
        raise ValueError(f"valuation_side_unknown:{side}")
    # A sale receives cash now and surrenders the future payout.  Represent the
    # received entry cash as a negative cost so the same reconciliation identity
    # remains valid.
    entry_cost = entry if side == "buy" else -entry
    adjusted_cost = CostComponents(
        executable_entry_cashflow=cost.executable_entry_cashflow + entry_cost,
        explicit_fee=cost.explicit_fee,
        execution_adjustment=cost.execution_adjustment,
        funding_or_discount_adjustment=cost.funding_or_discount_adjustment,
        capital_charge=cost.capital_charge,
        allocated_marginal_operating_cost=cost.allocated_marginal_operating_cost,
    )
    out: dict[str, Decimal] = {}
    for state, prob in q.items():
        resolution = h_c.get(state)
        if resolution is None:
            raise ValueError(f"valuation_hc_not_total:{state}")
        payout = _dec(payout_ir[resolution])
        settlement = round_cash(quantity * payout * (1 if side == "buy" else -1))
        out[state] = full_cost_delta(settlement_cashflow=settlement, cost=adjusted_cost).delta_w
    return out


def robust_ev(
    u_members: list[dict[str, Decimal]],
    world_delta: dict[str, Decimal],
    *,
    point_q: dict[str, Decimal] | None = None,
) -> tuple[Decimal, Decimal]:
    """``robust_EV = min_{P∈U} E_P[ΔW]``；返回 (robust, point_ev)。

    ``point_ev`` 用第一个成员（约定为 Q）。
    """
    if not u_members:
        raise ValueError("valuation_u_empty")
    evs: list[Decimal] = []
    for member in u_members:
        total = ZERO
        for state, prob in member.items():
            if state not in world_delta:
                raise ValueError(f"valuation_world_delta_missing:{state}")
            total += prob * world_delta[state]
        evs.append(round_cash(total))
    if point_q is None:
        point = evs[0]
    else:
        point = ZERO
        for state, prob in point_q.items():
            if state not in world_delta:
                raise ValueError(f"valuation_world_delta_missing:{state}")
            point += prob * world_delta[state]
        point = round_cash(point)
    return min(evs), point


def roi(ev: Decimal | str, capital_employed: Decimal | str) -> Decimal:
    """ROI = EV / capital_employed（capital>0）。"""
    ev_dec = _dec(ev)
    capital = _dec(capital_employed)
    if capital <= 0:
        raise ValueError("valuation_capital_nonpositive")
    return round_price(ev_dec / capital)


def expected_log_growth(
    members: list[dict[str, Decimal]],
    world_delta: dict[str, Decimal],
    *,
    bankroll: Decimal | str,
) -> Decimal:
    """``E_Q[ln(1 + ΔW/bankroll)]``；用 Q（第一个成员）。"""
    bankroll_dec = _dec(bankroll)
    if bankroll_dec <= 0:
        raise ValueError("valuation_bankroll_nonpositive")
    if not members:
        raise ValueError("valuation_u_empty")
    q = members[0]
    total = ZERO
    for state, prob in q.items():
        delta = world_delta[state]
        ratio = _dec(1) + delta / bankroll_dec
        if ratio <= 0:
            total += prob * Decimal("-1e9")  # 全损 → 极端负对数增长
        else:
            total += prob * _ln(ratio)
    return round_price(total)


def _ln(value: Decimal) -> Decimal:
    """Decimal 自然对数（3.12 移除 ``decimal.ln``；用高精度级数，确定性）。"""
    from decimal import localcontext

    x = _dec(value)
    if x <= 0:
        raise ValueError("valuation_ln_nonpositive")
    with localcontext() as ctx:
        ctx.prec = 50
        # ln(x) = 2·Σ (y^(2k+1)/(2k+1))，y=(x-1)/(x+1)，收敛于 x>0。
        y = (x - 1) / (x + 1)
        term = y
        result = ZERO
        k = 0
        while True:
            result += term / (2 * k + 1)
            next_term = term * y * y
            if abs(next_term) < Decimal("1e-40"):
                break
            term = next_term
            k += 1
        return result * 2


def worst_loss(world_delta: dict[str, Decimal]) -> Decimal:
    """U 下最大单状态损失（最小 ΔW）。"""
    if not world_delta:
        raise ValueError("valuation_world_delta_empty")
    return min(world_delta.values())


def break_even_payout_probability(
    payout_ir: dict[str, Decimal | str],
    cost_per_share: Decimal | str,
) -> Decimal:
    """break-even：需要 payout 至少覆盖全成本时的最小概率质量。

    对 Bernoulli（payout∈{0,1}）：p* = cost_per_share / 1（占 1 面）；返回该概率。
    """
    cost = _dec(cost_per_share)
    # 简单实现：p* = cost / max_payout
    max_payout = max(_dec(value) for value in payout_ir.values())
    if max_payout <= 0:
        raise ValueError("valuation_max_payout_nonpositive")
    return round_price(cost / max_payout)


def capital_days(
    capital: Decimal | str,
    horizon_days: Decimal | str,
) -> Decimal:
    """capital-days = capital × horizon_days。"""
    return round_cash(_dec(capital) * _dec(horizon_days))


def edge_delay_erosion(
    gross_edge: Decimal | str,
    delay_hours: Decimal | str,
    half_life_hours: Decimal | str,
) -> Decimal:
    """指数衰减：gross × 0.5^(delay/half_life)。"""
    if _dec(half_life_hours) <= 0:
        raise ValueError("valuation_half_life_nonpositive")
    ratio = _dec(delay_hours) / _dec(half_life_hours)
    erosion = _dec(gross_edge) * (Decimal("0.5") ** ratio)
    return round_price(erosion)
