"""P3 deterministic scoring 纯函数（WP-04 Checkpoint A）。

评价只读投影之前先打分：Bernoulli/multiclass/mean-only 的 proper loss、paired ΔLoss、
calibration、sharpness、tail loss、cluster bootstrap 与 n_eff。全部 Decimal、确定性、零 DB；
``decimal.ln`` 在 3.12 已不可依赖，对数/指数均用高精度级数自实现（参考 valuation._ln）。

规则契约（spec 冻结）：
- log loss 概率 0/1 一律用冻结 epsilon（``BERNOULLI_EPSILON``，来自 spec 的
  ``metric_policy.bernoulli_epsilon``）截断，不临场选择。
- bootstrap 以 resolution cluster 为抽样单元（不按裸 market），固定 seed，纯整数 LCG 索引。
- 数值输出统一 ``round_score``（12 位小数，ROUND_HALF_UP），保证跨存储字节稳定。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

BERNOULLI_EPSILON = Decimal("0.001")

_SCORE_PLACES = 12
_LN_LIMIT = Decimal("1e-40")
_LN_PREC = 50


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


def _clamp_prob(p: Decimal, epsilon: Decimal) -> Decimal:
    if p < epsilon:
        return epsilon
    if p > Decimal(1) - epsilon:
        return Decimal(1) - epsilon
    return p


def _ln(value: Decimal) -> Decimal:
    """Decimal 自然对数（3.12 移除 decimal.ln；atanh 级数，确定性）。

    ``ln(x) = 2·Σ y^(2k+1)/(2k+1)``，``y=(x-1)/(x+1)``，对 x>0 收敛。
    """
    x = _to_decimal(value, "scoring_ln")
    if x <= 0:
        raise ValueError("scoring_ln_nonpositive")
    with localcontext() as ctx:
        ctx.prec = _LN_PREC
        y = (x - 1) / (x + 1)
        term = y
        result = Decimal(0)
        k = 0
        while True:
            result += term / (2 * k + 1)
            next_term = term * y * y
            if abs(next_term) < _LN_LIMIT:
                break
            term = next_term
            k += 1
        return result * 2


def exp_dec(value: Decimal | str) -> Decimal:
    """Decimal 指数级数（确定性；不依赖 ``decimal.exp``）。"""
    x = _to_decimal(value, "scoring_exp")
    with localcontext() as ctx:
        ctx.prec = _LN_PREC
        result = Decimal(1)
        term = Decimal(1)
        k = 1
        while True:
            term = term * x / Decimal(k)
            result += term
            if abs(term) < _LN_LIMIT:
                break
            k += 1
        return result


def round_score(value: Decimal | str, places: int = _SCORE_PLACES) -> Decimal:
    """数值输出统一舍入（ROUND_HALF_UP，固定小数位），保证确定性字节。"""
    dec = _to_decimal(value, "scoring_round")
    with localcontext() as ctx:
        ctx.prec = 50
        return dec.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def bernoulli_brier(prob: Decimal, outcome: int) -> Decimal:
    """Bernoulli Brier：``(p - y)^2``，y∈{0,1}。"""
    p = _to_decimal(prob, "scoring_brier_prob")
    y = _to_decimal(outcome, "scoring_brier_outcome")
    if y not in (Decimal(0), Decimal(1)):
        raise ValueError("scoring_brier_outcome_not_binary")
    diff = p - y
    return round_score(diff * diff)


def bernoulli_log_loss(
    prob: Decimal,
    outcome: int,
    epsilon: Decimal = BERNOULLI_EPSILON,
) -> Decimal:
    """Bernoulli log loss：``-(y·ln(p) + (1-y)·ln(1-p))``。

    概率先 ``clamp(epsilon, 1-epsilon)``；epsilon 来自冻结 spec。
    """
    p = _to_decimal(prob, "scoring_logloss_prob")
    y = _to_decimal(outcome, "scoring_logloss_outcome")
    if y not in (Decimal(0), Decimal(1)):
        raise ValueError("scoring_logloss_outcome_not_binary")
    eps = _to_decimal(epsilon, "scoring_logloss_epsilon")
    if eps <= 0 or eps >= Decimal("0.5"):
        raise ValueError("scoring_logloss_epsilon_out_of_range")
    p = _clamp_prob(p, eps)
    loss = -(y * _ln(p) + (Decimal(1) - y) * _ln(Decimal(1) - p))
    return round_score(loss)


def _validate_multiclass(probs: list[Decimal], one_hot: list[Decimal]) -> None:
    if not isinstance(probs, (list, tuple)) or not probs:
        raise ValueError("scoring_mc_empty")
    if len(probs) != len(one_hot):
        raise ValueError("scoring_mc_length_mismatch")
    if not all(o in (Decimal(0), Decimal(1)) for o in one_hot):
        raise ValueError("scoring_mc_onehot_not_binary")
    if sum(one_hot) != Decimal(1):
        raise ValueError("scoring_mc_onehot_not_exactly_one")
    if any(p < 0 or p > 1 for p in probs):
        raise ValueError("scoring_mc_prob_out_of_range")
    if abs(sum(probs) - Decimal(1)) > Decimal("1e-6"):
        raise ValueError("scoring_mc_probs_not_total")


def multiclass_brier(probs: list[Decimal], one_hot: list[int]) -> Decimal:
    """Multiclass Brier：``Σ_k (p_k - y_k)^2``。"""
    probs_dec = [_to_decimal(p, "scoring_mc_prob") for p in probs]
    one_hot_dec = [_to_decimal(o, "scoring_mc_onehot") for o in one_hot]
    _validate_multiclass(probs_dec, one_hot_dec)
    loss = sum((p - o) * (p - o) for p, o in zip(probs_dec, one_hot_dec))
    return round_score(loss)


def multiclass_log_loss(
    probs: list[Decimal],
    one_hot: list[int],
    epsilon: Decimal = BERNOULLI_EPSILON,
) -> Decimal:
    """Multiclass log loss：``-Σ_k y_k·ln(p_k)``，p_k 先 clamp epsilon。"""
    probs_dec = [_to_decimal(p, "scoring_mc_logloss_prob") for p in probs]
    one_hot_dec = [_to_decimal(o, "scoring_mc_logloss_onehot") for o in one_hot]
    _validate_multiclass(probs_dec, one_hot_dec)
    eps = _to_decimal(epsilon, "scoring_mc_logloss_epsilon")
    if eps <= 0 or eps >= Decimal("0.5"):
        raise ValueError("scoring_mc_logloss_epsilon_out_of_range")
    total = Decimal(0)
    for p, o in zip(probs_dec, one_hot_dec):
        if o == Decimal(1):
            total -= _ln(_clamp_prob(p, eps))
    return round_score(total)


def mean_squared_payout_loss(predicted: Decimal, actual: Decimal) -> Decimal:
    """mean-only payout 固定平方误差：``(predicted - actual)^2``。"""
    diff = _to_decimal(predicted, "scoring_mse_predicted") - _to_decimal(
        actual, "scoring_mse_actual"
    )
    return round_score(diff * diff)


def delta_loss(candidate: Decimal, baseline: Decimal) -> Decimal:
    """``ΔLoss = candidate - baseline``，越小越好（可负）。"""
    return round_score(
        _to_decimal(candidate, "scoring_delta_candidate")
        - _to_decimal(baseline, "scoring_delta_baseline")
    )


def logit(p: Decimal, epsilon: Decimal = BERNOULLI_EPSILON) -> Decimal:
    """logit：``ln(p/(1-p))``，p 先 clamp epsilon。中间量不额外舍入。"""
    p = _to_decimal(p, "scoring_logit_p")
    eps = _to_decimal(epsilon, "scoring_logit_epsilon")
    if eps <= 0 or eps >= Decimal("0.5"):
        raise ValueError("scoring_logit_epsilon_out_of_range")
    p = _clamp_prob(p, eps)
    return _ln(p / (Decimal(1) - p))


def expit(x: Decimal) -> Decimal:
    """logistic 反变换：``1/(1+exp(-x))``。"""
    x = _to_decimal(x, "scoring_expit_x")
    return Decimal(1) / (Decimal(1) + exp_dec(-x))


def calibration_intercept_slope(
    pairs: list[tuple[Decimal, int]],
) -> tuple[Decimal | None, Decimal | None]:
    """logit-link 线性回归：``y ∈ {0,1}`` 对 ``x = logit(p)`` 的最小二乘。

    返回 ``(intercept, slope)``；样本 <2 或 x 零方差时返回 ``(None, None)``。
    测试用充足样本；本函数不做假设检验。
    """
    if not isinstance(pairs, (list, tuple)) or len(pairs) < 2:
        return (None, None)
    xs: list[Decimal] = []
    ys: list[Decimal] = []
    for prob, outcome in pairs:
        p = _to_decimal(prob, "scoring_calib_prob")
        y = _to_decimal(outcome, "scoring_calib_outcome")
        if y not in (Decimal(0), Decimal(1)):
            raise ValueError("scoring_calib_outcome_not_binary")
        xs.append(logit(p))
        ys.append(y)
    with localcontext() as ctx:
        ctx.prec = _LN_PREC
        n = Decimal(len(xs))
        x_bar = sum(xs) / n
        y_bar = sum(ys) / n
        sxx = sum((x - x_bar) * (x - x_bar) for x in xs)
        if sxx == 0:
            return (None, None)
        sxy = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
        slope = sxy / sxx
        intercept = y_bar - slope * x_bar
    return round_score(intercept), round_score(slope)


def sharpness(probs: list[Decimal]) -> Decimal:
    """sharpness：``mean(p·(1-p))``（越小越自信）。"""
    values = [_to_decimal(p, "scoring_sharpness_prob") for p in probs]
    if not values:
        raise ValueError("scoring_sharpness_empty")
    if any(p < 0 or p > 1 for p in values):
        raise ValueError("scoring_sharpness_prob_out_of_range")
    mean = sum(p * (Decimal(1) - p) for p in values) / Decimal(len(values))
    return round_score(mean)


def _worst_fraction_mean(values: list[Decimal], q: Decimal) -> Decimal:
    """最差 ``(1-q)`` 份额的平均（至少 1 个样本）。"""
    n = len(values)
    k = n - int(q * Decimal(n))
    if k < 1:
        k = 1
    if k > n:
        k = n
    ordered = sorted(values)
    tail = ordered[n - k:]
    return sum(tail) / Decimal(k)


def tail_loss(losses: list[Decimal], quantile: Decimal = Decimal("0.95")) -> Decimal:
    """tail loss / expected shortfall：超过分位数的尾部平均损失。

    实现为最差 ``(1-quantile)`` 份额的平均（至少 1 个样本），即 level=quantile 的 CVaR。
    """
    values = [_to_decimal(l, "scoring_tail_loss") for l in losses]
    if not values:
        raise ValueError("scoring_tail_loss_empty")
    q = _to_decimal(quantile, "scoring_tail_loss_quantile")
    if q <= 0 or q >= 1:
        raise ValueError("scoring_tail_loss_quantile_out_of_range")
    return round_score(_worst_fraction_mean(values, q))


def n_eff(cluster_sizes: list[int], total: int) -> Decimal:
    """有效样本量：``n_eff = total^2 / Σ_c n_c^2``（Kish 有效样本量，完美组内相关）。

    - 全部 cluster 等大时 ``n_eff = 集群数``；
    - 每 cluster 仅 1 个样本时 ``n_eff = total``；
    - 恒在 ``[1, n_clusters]``。``total`` 必须等于 ``Σ n_c``。
    """
    if not isinstance(cluster_sizes, (list, tuple)) or not cluster_sizes:
        raise ValueError("scoring_n_eff_empty")
    sizes = [int(s) for s in cluster_sizes]
    if any(s < 0 for s in sizes):
        raise ValueError("scoring_n_eff_negative")
    total_dec = _to_decimal(total, "scoring_n_eff_total")
    if sum(sizes) != int(total_dec):
        raise ValueError("scoring_n_eff_total_mismatch")
    denom = sum(Decimal(s) * Decimal(s) for s in sizes)
    if denom == 0:
        raise ValueError("scoring_n_eff_zero_clusters")
    return round_score((total_dec * total_dec) / denom)


def _lcg_next(state: int) -> int:
    # Numerical Recipes 纯整数 LCG（32 位），只用于索引序列，不产生浮点概率。
    return (1664525 * state + 1013904223) & 0xFFFFFFFF


def _quantile_indexed(sorted_values: list[Decimal], quantile: Decimal) -> Decimal:
    n = len(sorted_values)
    if n == 0:
        raise ValueError("scoring_quantile_empty")
    idx = int(quantile * Decimal(n - 1))
    idx = max(0, min(idx, n - 1))
    return sorted_values[idx]


def cluster_bootstrap(
    cluster_losses: list[list[Decimal]],
    *,
    seed: int,
    n_resamples: int = 1000,
    time_blocks: int = 1,
) -> dict:
    """resolution-cluster bootstrap：以 cluster 为抽样单元（不按裸 market），固定 seed。

    - 每个 cluster 贡献 ``mean(cluster losses)``（cluster 内等权）；
    - 每个 resample 对 ``n_clusters`` 个 cluster 有放回抽样，统计量 = 抽样 cluster 均值再平均；
    - ``time_blocks`` 是记录的分层维度（等块下块内重抽样即 cluster iid 抽样，统计量等价），
      输出原样记录；
    - CI 取 2.5%–97.5% 分位数（``sorted[int(quantile·(n-1))]`` 索引法，确定性）。

    返回 ``{point, ci_low, ci_high, n_eff, n_clusters, time_blocks}``。
    """
    if not isinstance(cluster_losses, (list, tuple)) or not cluster_losses:
        raise ValueError("scoring_bootstrap_empty")
    if not isinstance(n_resamples, int) or n_resamples <= 0:
        raise ValueError("scoring_bootstrap_n_resamples_nonpositive")
    if not isinstance(time_blocks, int) or time_blocks <= 0:
        raise ValueError("scoring_bootstrap_time_blocks_nonpositive")
    cluster_means: list[Decimal] = []
    cluster_sizes: list[int] = []
    for index, cluster in enumerate(cluster_losses):
        values = [_to_decimal(v, f"scoring_bootstrap_loss[{index}]") for v in cluster]
        if not values:
            raise ValueError(f"scoring_bootstrap_empty_cluster:{index}")
        cluster_means.append(sum(values) / Decimal(len(values)))
        cluster_sizes.append(len(values))
    n_clusters = len(cluster_means)
    point = round_score(sum(cluster_means) / Decimal(n_clusters))

    resample_means: list[Decimal] = []
    state = int(seed) & 0xFFFFFFFF
    for _ in range(n_resamples):
        stat = Decimal(0)
        for _ in range(n_clusters):
            state = _lcg_next(state)
            stat += cluster_means[state % n_clusters]
        resample_means.append(stat / Decimal(n_clusters))
    resample_means.sort()
    ci_low = round_score(_quantile_indexed(resample_means, Decimal("0.025")))
    ci_high = round_score(_quantile_indexed(resample_means, Decimal("0.975")))

    total = sum(cluster_sizes)
    return {
        "point": point,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_eff": n_eff(cluster_sizes, total),
        "n_clusters": n_clusters,
        "time_blocks": time_blocks,
    }


def proper_loss_guard(label_state: str) -> bool:
    """proper-loss 准入：只有 ``final_admissible`` 进入 prediction proper loss。"""
    return label_state == "final_admissible"
