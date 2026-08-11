"""Valuation domain 单测（WP-03 Checkpoint B）。

覆盖 depth_walk / full_cost_delta / world_delta_w / robust_ev / expected_log_growth
及辅助指标（roi、worst_loss、break_even、capital_days、edge_delay_erosion）。
全部确定性 Decimal；float 输入 fail-closed。
"""

from decimal import Decimal

import pytest

from app.domain.trading import (
    CostComponents,
    break_even_payout_probability,
    capital_days,
    depth_walk,
    edge_delay_erosion,
    expected_log_growth,
    full_cost_delta,
    robust_ev,
    roi,
    world_delta_w,
    worst_loss,
)


# ---------------- depth_walk ----------------

def test_depth_walk_buy_ascending_fills_target():
    fill = depth_walk(
        [(Decimal("0.52"), Decimal("100")), (Decimal("0.53"), Decimal("200"))],
        side="buy", target_quantity=Decimal("250"),
    )
    assert fill.fill_quantity == Decimal("250")
    # VWAP = (0.52*100 + 0.53*150) / 250 = 131.5/250 = 0.526，round_price 保留 6 位
    assert fill.vwap == Decimal("0.526000")
    assert fill.remaining_quantity == Decimal("0")
    assert fill.fee == Decimal("0")
    assert fill.complete is True
    assert fill.unfilled_reason is None


def test_depth_walk_buy_insufficient_depth():
    fill = depth_walk(
        [(Decimal("0.52"), Decimal("100")), (Decimal("0.53"), Decimal("200"))],
        side="buy", target_quantity=Decimal("500"),
    )
    assert fill.fill_quantity == Decimal("300")
    assert fill.vwap == Decimal("0.526667")
    assert fill.remaining_quantity == Decimal("200")
    assert fill.complete is False
    assert fill.unfilled_reason == "insufficient_depth"


def test_depth_walk_empty_book():
    fill = depth_walk([], side="buy", target_quantity=Decimal("500"))
    assert fill.fill_quantity == Decimal("0")
    assert fill.vwap == Decimal("0")
    assert fill.remaining_quantity == Decimal("500")
    assert fill.complete is False
    assert fill.unfilled_reason == "empty_book"


def test_depth_walk_unknown_side_rejected():
    with pytest.raises(ValueError, match="valuation_side_unknown"):
        depth_walk([(Decimal("0.5"), Decimal("1"))], side="bid", target_quantity=Decimal("1"))


def test_depth_walk_nonpositive_target_rejected():
    with pytest.raises(ValueError, match="valuation_target_quantity_nonpositive"):
        depth_walk([(Decimal("0.5"), Decimal("1"))], side="buy", target_quantity=Decimal("0"))
    with pytest.raises(ValueError, match="valuation_target_quantity_nonpositive"):
        depth_walk([(Decimal("0.5"), Decimal("1"))], side="buy", target_quantity=Decimal("-5"))


# ---------------- full_cost_delta / CostComponents ----------------

def test_full_cost_delta_settlement_minus_cost_total():
    result = full_cost_delta(
        settlement_cashflow="110",
        cost=CostComponents(executable_entry_cashflow=Decimal("50"), explicit_fee=Decimal("10")),
    )
    assert result.delta_w == Decimal("50")
    assert result.settlement_cashflow == Decimal("110")
    assert result.cashflow_reconciliation_residual == Decimal("0")
    assert result.world_state_id == ""


def test_cost_components_total_is_mutually_exclusive_sum():
    cost = CostComponents(
        Decimal("1"), Decimal("2"), Decimal("3"),
        Decimal("4"), Decimal("5"), Decimal("6"),
    )
    assert cost.total() == Decimal("21")


# ---------------- world_delta_w ----------------

def test_world_delta_w_quantity_times_payout_minus_entry_fee():
    wd = world_delta_w(
        {"w0": "0.6", "w1": "0.4"},
        h_c={"w0": "res0", "w1": "res1"},
        payout_ir={"res0": "1", "res1": "0"},
        cost=CostComponents(explicit_fee=Decimal("5")),
        token_quantity="100", token_vwap="0.5",
    )
    # entry = 100*0.5 = 50；cost.total = 50 + 5 = 55
    assert wd["w0"] == Decimal("45")    # 100*1 - 55
    assert wd["w1"] == Decimal("-55")   # 100*0 - 55


def test_world_delta_w_hc_not_total_rejected():
    with pytest.raises(ValueError, match="valuation_hc_not_total:w1"):
        world_delta_w(
            {"w0": "0.6", "w1": "0.4"},
            h_c={"w0": "res0"},  # w1 无 h_c 映射
            payout_ir={"res0": "1"},
            cost=CostComponents(),
            token_quantity="100", token_vwap="0.5",
        )


# ---------------- robust_ev ----------------

def test_robust_ev_takes_min_over_u_members():
    robust, point = robust_ev(
        [
            {"w0": Decimal("0.6"), "w1": Decimal("0.4")},
            {"w0": Decimal("0.5"), "w1": Decimal("0.5")},
        ],
        {"w0": Decimal("100"), "w1": Decimal("-50")},
    )
    assert robust == Decimal("25")   # min(40, 25)
    assert point == Decimal("40")    # 第一个成员（约定 Q）


def test_robust_ev_empty_u_rejected():
    with pytest.raises(ValueError, match="valuation_u_empty"):
        robust_ev([], {"w0": Decimal("1")})


# ---------------- expected_log_growth ----------------

def test_expected_log_growth_uses_internal_ln_series():
    value = expected_log_growth(
        [{"w0": Decimal("0.6"), "w1": Decimal("0.4")}],
        {"w0": Decimal("100"), "w1": Decimal("-50")},
        bankroll=Decimal("100"),
    )
    # ratio: 2 与 0.5 → E = 0.6*ln2 + 0.4*(-ln2) = 0.2*ln2 ≈ 0.138629
    assert value == Decimal("0.138629")


def test_expected_log_growth_ratio_nonpositive_caps_negative():
    value = expected_log_growth(
        [{"w0": Decimal("1.0")}],
        {"w0": Decimal("-200")},  # ratio = 1 + (-200/100) = -1 ≤ 0
        bankroll=Decimal("100"),
    )
    assert value == Decimal("-1000000000")


def test_expected_log_growth_empty_u_rejected():
    with pytest.raises(ValueError, match="valuation_u_empty"):
        expected_log_growth([], {}, bankroll=Decimal("100"))


# ---------------- 辅助指标边界 ----------------

def test_roi_ratio_rounded_to_price_places():
    assert roi(Decimal("100"), Decimal("200")) == Decimal("0.5")
    with pytest.raises(ValueError, match="valuation_capital_nonpositive"):
        roi(Decimal("10"), Decimal("0"))


def test_worst_loss_is_min_world_delta():
    assert worst_loss({"w0": Decimal("5"), "w1": Decimal("-3")}) == Decimal("-3")
    with pytest.raises(ValueError, match="valuation_world_delta_empty"):
        worst_loss({})


def test_break_even_payout_probability():
    assert break_even_payout_probability({"res0": "1", "res1": "0"}, Decimal("0.4")) == Decimal("0.4")
    with pytest.raises(ValueError, match="valuation_max_payout_nonpositive"):
        break_even_payout_probability({"res0": "0", "res1": "0"}, Decimal("0.4"))


def test_capital_days():
    assert capital_days(Decimal("100"), Decimal("3")) == Decimal("300")


def test_edge_delay_erosion_halving():
    assert edge_delay_erosion(Decimal("100"), Decimal("24"), Decimal("24")) == Decimal("50")
    assert edge_delay_erosion(Decimal("9.5"), Decimal("0"), Decimal("1")) == Decimal("9.5")
    with pytest.raises(ValueError, match="valuation_half_life_nonpositive"):
        edge_delay_erosion(Decimal("100"), Decimal("1"), Decimal("0"))


# ---------------- float fail-closed ----------------

def test_float_input_rejected_everywhere():
    with pytest.raises(ValueError, match="valuation_float_forbidden"):
        roi(Decimal("1.0"), 2.0)
    with pytest.raises(ValueError, match="valuation_float_forbidden"):
        capital_days(1.5, Decimal("3"))
    with pytest.raises(ValueError, match="valuation_float_forbidden"):
        depth_walk([(Decimal("0.5"), Decimal("1"))], side="buy", target_quantity=2.5)
    with pytest.raises(ValueError, match="valuation_float_forbidden"):
        depth_walk([(0.5, 100)], side="buy", target_quantity=Decimal("100"))
    with pytest.raises(ValueError, match="valuation_float_forbidden"):
        expected_log_growth(
            [{"w0": Decimal("1")}], {"w0": Decimal("0")}, bankroll=100.0
        )
