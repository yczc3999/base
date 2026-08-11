"""P3 evaluation / scientific-inference 纯函数（WP-04 Checkpoint A）。

拒绝审计加权（Horvitz–Thompson）、edge 分桶单调性、no-action regret、
blind→decision 延迟侵蚀、Portfolio 汇总（含 ``not_evaluable`` 语义）与 Execution 指标。

规则契约（spec 冻结）：
- Portfolio 缺 operating cost / ledger / action-set lineage 时必须 ``not_evaluable``，
  一律不 0 填充；逐项缺失的子指标返回 ``None`` 而非 0。
- 无 audit 样本时只报告 ``unknown``（调用方检查 ``has_audit`` 后再调用 ``ht_estimate``）。
- 全部 Decimal、确定性、零 DB；指数用 scoring.exp_dec 级数。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.trading.scoring import exp_dec, round_score, tail_loss

ZERO = Decimal("0")
CVAR_ALPHA = Decimal("0.95")


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


def horvitz_thompson_weight(inclusion_probability: Decimal) -> Decimal:
    """Horvitz–Thompson 权重：``1/π``（π∈(0,1]）。"""
    pi = _to_decimal(inclusion_probability, "inference_ht_pi")
    if pi <= 0 or pi > 1:
        raise ValueError("inference_ht_pi_out_of_range")
    return round_score(Decimal(1) / pi)


def has_audit(rows: list) -> bool:
    """是否存在 audit 样本；无样本时调用方应报告 ``unknown`` 而非 0。"""
    return isinstance(rows, (list, tuple)) and len(rows) > 0


def ht_estimate(weighted_losses: list[tuple[Decimal, Decimal]]) -> Decimal:
    """HT 估计：``Σ(loss·weight) / Σ weight``；空或权重非正 fail-closed。"""
    if not isinstance(weighted_losses, (list, tuple)) or not weighted_losses:
        raise ValueError("inference_ht_empty")
    num = ZERO
    den = ZERO
    for loss, weight in weighted_losses:
        l = _to_decimal(loss, "inference_ht_loss")
        w = _to_decimal(weight, "inference_ht_weight")
        if w <= 0:
            raise ValueError("inference_ht_nonpositive_weight")
        num += l * w
        den += w
    return round_score(num / den)


def edge_bucket_monotonicity(buckets: list[dict]) -> bool:
    """edge 分桶单调性：按声明 edge 升序，realized excess return 前向单调（允许平）。"""
    if not isinstance(buckets, (list, tuple)):
        raise ValueError("inference_edge_buckets_not_list")
    if len(buckets) < 2:
        return True
    ordered = sorted(
        buckets,
        key=lambda b: _to_decimal(b["edge"], "inference_edge_bucket_edge"),
    )
    prev: Decimal | None = None
    for bucket in ordered:
        realized = _to_decimal(
            bucket["realized_excess_return"], "inference_edge_bucket_realized"
        )
        if prev is not None and realized < prev:
            return False
        prev = realized
    return True


def no_action_regret(selected_pnl: Decimal, no_action_pnl: Decimal) -> Decimal:
    """``selected - no_action``（>0 表示行动优于什么都不做）。"""
    return round_score(
        _to_decimal(selected_pnl, "inference_regret_selected")
        - _to_decimal(no_action_pnl, "inference_regret_no_action")
    )


def blind_to_decision_delay_erosion(
    delay_seconds: Decimal,
    horizon_seconds: Decimal,
    edge: Decimal,
    decay_lambda: Decimal = Decimal("0.0"),
) -> Decimal:
    """edge 随 blind→decision 延迟指数侵蚀：``edge·exp(-λ·delay/horizon)``。

    - ``delay_seconds``：盲态预测到决策生成之间的延迟；
    - ``horizon_seconds``：事件/预测地平线；
    - ``decay_lambda``：侵蚀速率（0 表示不侵蚀）。
    """
    delay = _to_decimal(delay_seconds, "inference_erosion_delay")
    horizon = _to_decimal(horizon_seconds, "inference_erosion_horizon")
    lam = _to_decimal(decay_lambda, "inference_erosion_lambda")
    if horizon <= 0:
        raise ValueError("inference_erosion_horizon_nonpositive")
    if delay < 0:
        raise ValueError("inference_erosion_delay_negative")
    if lam < 0:
        raise ValueError("inference_erosion_lambda_negative")
    eroded = _to_decimal(edge, "inference_erosion_edge") * exp_dec(
        -lam * delay / horizon
    )
    return round_score(eroded)


def drawdown(equity_curve: list[Decimal]) -> Decimal:
    """最大峰值回撤（负值：从峰值到谷底的跌幅；单调上涨返回 0）。"""
    curve = [_to_decimal(e, "inference_drawdown") for e in equity_curve]
    if not curve:
        raise ValueError("inference_drawdown_empty")
    peak = curve[0]
    max_dd = ZERO
    for value in curve:
        if value > peak:
            peak = value
        dd = value - peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def cvar(losses: list[Decimal], alpha: Decimal = CVAR_ALPHA) -> Decimal:
    """CVaR / expected shortfall：最差 ``(1-alpha)`` 份额平均损失（委托 scoring.tail_loss）。"""
    return tail_loss(losses, quantile=alpha)


def _not_evaluable(reason: str) -> dict:
    return {
        "trading_pnl": None,
        "operating_cost": None,
        "system_net": None,
        "drawdown": None,
        "cvar": None,
        "capital_days": None,
        "not_evaluable": True,
        "reason": reason,
    }


def _equity_curve(rows: list[dict]) -> list[Decimal] | None:
    """优先取逐期 ``equity``；否则由起始 ``capital`` + 累计 pnl 构建；都没有返回 None。"""
    if all("equity" in row for row in rows):
        return [_to_decimal(row["equity"], "inference_portfolio_equity") for row in rows]
    if "capital" in rows[0]:
        running = _to_decimal(rows[0]["capital"], "inference_portfolio_capital")
        curve: list[Decimal] = []
        for row in rows:
            running += _to_decimal(row["pnl"], "inference_portfolio_pnl")
            curve.append(running)
        return curve
    return None


def portfolio_summary(rows: list[dict]) -> dict:
    """Portfolio 五指标汇总。

    - 缺行、缺 ``pnl`` 或 ``operating_cost`` → ``not_evaluable=True`` 且各值 None（不 0 填充）；
    - ``system_net = trading_pnl - operating_cost``；
    - ``drawdown`` 需要 equity 曲线（或 capital+pnl 推导），缺则 None；
    - ``cvar`` 对逐期净损失（``-(pnl - operating_cost)``）在 alpha=0.95 求尾均；
    - ``capital_days = Σ capital·horizon_days``，缺任一字段则 None；
    - 任一输出无法计算 → 顶层 ``not_evaluable=True``。
    """
    if not isinstance(rows, list) or not rows:
        return _not_evaluable("no_rows")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("inference_portfolio_row_not_object")
        if "pnl" not in row or "operating_cost" not in row:
            return _not_evaluable("missing_pnl_or_operating_cost")
    trading_pnl = sum(
        _to_decimal(row["pnl"], "inference_portfolio_pnl") for row in rows
    )
    operating_cost = sum(
        _to_decimal(row["operating_cost"], "inference_portfolio_cost") for row in rows
    )
    system_net = trading_pnl - operating_cost
    out: dict = {
        "trading_pnl": round_score(trading_pnl),
        "operating_cost": round_score(operating_cost),
        "system_net": round_score(system_net),
        "drawdown": None,
        "cvar": None,
        "capital_days": None,
        "not_evaluable": False,
        "reason": None,
    }
    curve = _equity_curve(rows)
    if curve is not None:
        out["drawdown"] = round_score(drawdown(curve))
    losses = [
        -(
            _to_decimal(row["pnl"], "inference_portfolio_pnl")
            - _to_decimal(row["operating_cost"], "inference_portfolio_cost")
        )
        for row in rows
    ]
    out["cvar"] = round_score(cvar(losses, alpha=CVAR_ALPHA))
    if all("capital" in row and "horizon_days" in row for row in rows):
        capital_days_value = sum(
            _to_decimal(row["capital"], "inference_portfolio_capital")
            * _to_decimal(row["horizon_days"], "inference_portfolio_horizon")
            for row in rows
        )
        out["capital_days"] = round_score(capital_days_value)
    if any(out[key] is None for key in ("drawdown", "capital_days")):
        out["not_evaluable"] = True
        out["reason"] = out["reason"] or "incomplete_portfolio_inputs"
    return out


def execution_metrics(fills: list[dict]) -> dict:
    """Execution 指标：fill/partial/reject 计数、fee 合计、slippage（VWAP vs 参考价）。

    slippage 只统计含 ``reference_price`` 与 ``fill_price`` 的成交：
    ``Σ qty·(fill_price - reference_price) / Σ qty``；无参考价样本时 ``slippage_vwap=None``。
    """
    if not isinstance(fills, list):
        raise ValueError("inference_execution_not_list")
    fill_count = 0
    partial_count = 0
    reject_count = 0
    fee_total = ZERO
    slippage_num = ZERO
    slippage_den = ZERO
    slippage_n = 0
    for fill in fills:
        status = fill.get("status")
        if status not in ("fill", "partial", "reject"):
            raise ValueError(f"inference_execution_status_unknown:{status}")
        if status == "fill":
            fill_count += 1
        elif status == "partial":
            partial_count += 1
        else:
            reject_count += 1
        fee_total += _to_decimal(fill.get("fee", "0"), "inference_execution_fee")
        if "reference_price" in fill and "fill_price" in fill:
            qty = _to_decimal(fill.get("quantity", "0"), "inference_execution_qty")
            ref = _to_decimal(fill["reference_price"], "inference_execution_ref")
            fp = _to_decimal(fill["fill_price"], "inference_execution_fill_price")
            slippage_num += qty * (fp - ref)
            slippage_den += qty
            slippage_n += 1
    out: dict = {
        "fill_count": fill_count,
        "partial_count": partial_count,
        "reject_count": reject_count,
        "fee_total": round_score(fee_total),
        "slippage_n": slippage_n,
        "slippage_vwap": None,
    }
    if slippage_den > 0:
        out["slippage_vwap"] = round_score(slippage_num / slippage_den)
    return out
