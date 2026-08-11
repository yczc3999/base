"""P3 evaluation / scientific-inference 纯函数单测（WP-04 Checkpoint A）。

覆盖 Horvitz–Thompson 加权（1/π）、ht_estimate、has_audit、edge 分桶单调性、
no-action regret、blind→decision 延迟侵蚀、portfolio 汇总（not_evaluable 不 0 填充）、
execution 指标、drawdown、CVaR。全部确定性 Decimal、快速、不连 DB。
"""

from decimal import Decimal

import pytest

from app.domain.trading import (
    blind_to_decision_delay_erosion,
    cvar,
    drawdown,
    edge_bucket_monotonicity,
    execution_metrics,
    has_audit,
    horvitz_thompson_weight,
    ht_estimate,
    no_action_regret,
    portfolio_summary,
)

D = Decimal


# ---------------- Horvitz–Thompson / reject-audit ----------------

def test_horvitz_thompson_weight_is_inverse_pi():
    assert horvitz_thompson_weight(D("0.25")) == D("4")
    assert horvitz_thompson_weight(D("0.50")) == D("2")
    assert horvitz_thompson_weight(D("1")) == D("1")
    with pytest.raises(ValueError, match="inference_ht_pi_out_of_range"):
        horvitz_thompson_weight(D("0"))
    with pytest.raises(ValueError, match="inference_ht_pi_out_of_range"):
        horvitz_thompson_weight(D("1.5"))


def test_ht_estimate_weighted_sum_over_weight_sum():
    # (0.12*4 + 0.08*2) / (4 + 2) = 0.64/6 = 0.1066666... → 12 位
    assert ht_estimate([(D("0.12"), D("4")), (D("0.08"), D("2"))]) == D(
        "0.106666666667"
    )
    with pytest.raises(ValueError, match="inference_ht_empty"):
        ht_estimate([])


def test_has_audit_unknown_semantics():
    assert has_audit([]) is False
    assert has_audit([(D("0.12"), D("4"))]) is True


def test_reject_audit_scenario_weights_recomputable():
    from tests.trading.fixtures.p3_learning.p3_helpers import frozen_scenario

    scenario = frozen_scenario("reject_audit")
    weights = {
        sample["market_id"]: horvitz_thompson_weight(
            D(sample["inclusion_probability"])
        )
        for sample in scenario["audit_samples"]
    }
    assert weights == {k: D(v) for k, v in scenario["golden"]["weights"].items()}
    assert ht_estimate(
        [(D(s["loss"]), weights[s["market_id"]]) for s in scenario["audit_samples"]]
    ) == D(scenario["golden"]["ht_estimate"])


# ---------------- edge / regret / erosion ----------------

def test_edge_bucket_monotonicity():
    monotonic = [
        {"edge": "0.02", "realized_excess_return": "0.01"},
        {"edge": "0.05", "realized_excess_return": "0.02"},
        {"edge": "0.08", "realized_excess_return": "0.02"},  # 允许平
    ]
    assert edge_bucket_monotonicity(monotonic) is True
    assert edge_bucket_monotonicity(monotonic[:1]) is True
    broken = [
        {"edge": "0.02", "realized_excess_return": "0.03"},
        {"edge": "0.05", "realized_excess_return": "0.01"},
    ]
    assert edge_bucket_monotonicity(broken) is False


def test_no_action_regret_sign():
    assert no_action_regret(D("10"), D("4")) == D("6")
    assert no_action_regret(D("2"), D("5")) == D("-3")


def test_blind_to_decision_delay_erosion():
    # λ=0 → 不侵蚀
    assert blind_to_decision_delay_erosion(D("3600"), D("3600"), D("0.10")) == D("0.10")
    # λ=1, delay=horizon → edge·exp(-1) = 0.10·0.3678794411714423 = 0.036787944117...
    assert blind_to_decision_delay_erosion(
        D("3600"), D("3600"), D("0.10"), D("1")
    ) == D("0.036787944117")
    with pytest.raises(ValueError, match="inference_erosion_horizon_nonpositive"):
        blind_to_decision_delay_erosion(D("1"), D("0"), D("0.1"))
    with pytest.raises(ValueError, match="inference_erosion_lambda_negative"):
        blind_to_decision_delay_erosion(D("1"), D("10"), D("0.1"), D("-1"))


# ---------------- portfolio ----------------

def test_drawdown_peak_to_trough():
    assert drawdown([D("100"), D("120"), D("90"), D("110")]) == D("-30")
    assert drawdown([D("1"), D("2"), D("3")]) == D("0")
    with pytest.raises(ValueError, match="inference_drawdown_empty"):
        drawdown([])


def test_cvar_matches_tail_loss():
    losses = [D("0.1"), D("0.2"), D("0.3"), D("0.4"), D("0.5")]
    assert cvar(losses, D("0.8")) == D("0.5")
    assert cvar(losses, D("0.6")) == D("0.45")
    assert cvar(losses) == D("0.5")


def test_portfolio_summary_full():
    rows = [
        {"pnl": "100", "operating_cost": "10", "equity": "110",
         "capital": "1000", "horizon_days": "1"},
        {"pnl": "50", "operating_cost": "10", "equity": "150",
         "capital": "1000", "horizon_days": "2"},
        {"pnl": "-80", "operating_cost": "10", "equity": "60",
         "capital": "1000", "horizon_days": "1"},
    ]
    out = portfolio_summary(rows)
    assert out["not_evaluable"] is False
    assert out["trading_pnl"] == D("70")     # 100+50-80
    assert out["operating_cost"] == D("30")  # 10*3
    assert out["system_net"] == D("40")      # 70-30
    assert out["drawdown"] == D("-90")       # equity [110,150,60] → 峰值 150 → -90
    assert out["cvar"] == D("90")            # 净损失 [−90,−40,90] 的最差尾部
    assert out["capital_days"] == D("4000")  # 1000*(1+2+1)


def test_portfolio_summary_not_evaluable_no_zero_fill():
    # 缺 operating_cost → not_evaluable，system_net 必须 None 而非 0
    out = portfolio_summary([{"pnl": "100"}])
    assert out["not_evaluable"] is True
    assert out["system_net"] is None
    assert out["trading_pnl"] is None
    assert out["reason"] == "missing_pnl_or_operating_cost"

    # 空行集 → not_evaluable
    assert portfolio_summary([])["not_evaluable"] is True

    # 有 pnl+cost 但缺 equity/capital/horizon → 逐项 None，整体 not_evaluable
    partial = portfolio_summary([{"pnl": "10", "operating_cost": "2"}])
    assert partial["not_evaluable"] is True
    assert partial["drawdown"] is None
    assert partial["capital_days"] is None
    assert partial["system_net"] == D("8")


def test_portfolio_summary_drawdown_from_capital_derived_curve():
    rows = [
        {"pnl": "10", "operating_cost": "0", "capital": "100", "horizon_days": "1"},
        {"pnl": "-30", "operating_cost": "0", "capital": "100", "horizon_days": "1"},
    ]
    out = portfolio_summary(rows)
    # equity = [110, 80] → 峰值 110 → 回撤 -30
    assert out["drawdown"] == D("-30")
    assert out["not_evaluable"] is False


# ---------------- execution ----------------

def test_execution_metrics_counts_fees_slippage():
    fills = [
        {"status": "fill", "quantity": "100", "fill_price": "0.52",
         "reference_price": "0.50", "fee": "0.01"},
        {"status": "partial", "quantity": "50", "fill_price": "0.53",
         "reference_price": "0.52", "fee": "0.005"},
        {"status": "reject", "quantity": "0", "fee": "0"},
    ]
    out = execution_metrics(fills)
    assert out["fill_count"] == 1
    assert out["partial_count"] == 1
    assert out["reject_count"] == 1
    assert out["fee_total"] == D("0.015")
    # VWAP slippage = (0.02*100 + 0.01*50) / 150 = 2.5/150 = 0.0166666...
    assert out["slippage_vwap"] == D("0.016666666667")
    assert out["slippage_n"] == 2


def test_execution_metrics_no_reference_slippage_none():
    out = execution_metrics([{"status": "fill", "quantity": "10", "fee": "0"}])
    assert out["fill_count"] == 1
    assert out["slippage_vwap"] is None
    assert out["slippage_n"] == 0


def test_execution_metrics_unknown_status_rejected():
    with pytest.raises(ValueError, match="inference_execution_status_unknown"):
        execution_metrics([{"status": "cancel", "fee": "0"}])


# ---------------- float fail-closed ----------------

def test_float_input_rejected():
    with pytest.raises(ValueError, match="inference_ht_pi_float_forbidden"):
        horvitz_thompson_weight(0.25)
    with pytest.raises(ValueError, match="inference_regret_selected_float_forbidden"):
        no_action_regret(10.0, D("4"))
    with pytest.raises(ValueError, match="inference_erosion_edge_float_forbidden"):
        blind_to_decision_delay_erosion(D("1"), D("10"), 0.1)
