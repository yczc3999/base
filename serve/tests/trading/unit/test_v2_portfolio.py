"""Portfolio domain 单测（WP-03 Checkpoint C）。

覆盖 net_risk_capital / cap_check / marginal_log_growth_delta / worst_loss_cvar。
全部确定性 Decimal。
"""

from decimal import Decimal

import pytest

from app.domain.trading import (
    LegExposure,
    cap_check,
    marginal_log_growth_delta,
    net_risk_capital,
    worst_loss_cvar,
)


# ---------------- net_risk_capital ----------------

def test_net_risk_capital_leg_exposure_times_price():
    legs = [LegExposure(market_id=1, component_id=1, contract_spec_id=1, token_id=1,
                        quantity=Decimal("100"))]
    result = net_risk_capital(legs, bankroll=Decimal("1000"), price=Decimal("0.5"))
    assert result["capital"] == Decimal("50")
    assert result["fraction"] == Decimal("0.05")


def test_net_risk_capital_sell_negative_uses_abs():
    legs = [LegExposure(market_id=1, component_id=1, contract_spec_id=1, token_id=1,
                        quantity=Decimal("-100"))]
    result = net_risk_capital(legs, bankroll=Decimal("1000"), price=Decimal("0.5"))
    assert result["capital"] == Decimal("50")
    assert result["fraction"] == Decimal("0.05")


def test_net_risk_capital_nonpositive_bankroll_rejected():
    with pytest.raises(ValueError, match="portfolio_bankroll_nonpositive"):
        net_risk_capital([], bankroll=Decimal("0"), price=Decimal("0.5"))
    with pytest.raises(ValueError, match="portfolio_bankroll_nonpositive"):
        net_risk_capital([], bankroll=Decimal("-1"), price=Decimal("0.5"))


# ---------------- cap_check ----------------

def test_cap_check_at_default_limits_passes():
    check = cap_check(
        market_exposure=Decimal("40"), component_exposure=Decimal("60"),
        global_exposure=Decimal("300"), bankroll=Decimal("1000"),
    )
    assert check.ok is True
    assert check.per_market_fraction == Decimal("0.04")
    assert check.per_component_fraction == Decimal("0.06")
    assert check.global_fraction == Decimal("0.30")
    assert check.reason is None


def test_cap_check_per_market_breach():
    check = cap_check(
        market_exposure=Decimal("41"), component_exposure=Decimal("50"),
        global_exposure=Decimal("200"), bankroll=Decimal("1000"),
    )
    assert check.ok is False
    assert check.reason == "per_market_cap_exceeded"


def test_cap_check_per_component_breach():
    check = cap_check(
        market_exposure=Decimal("30"), component_exposure=Decimal("61"),
        global_exposure=Decimal("200"), bankroll=Decimal("1000"),
    )
    assert check.ok is False
    assert check.reason == "per_component_cap_exceeded"


def test_cap_check_global_breach():
    check = cap_check(
        market_exposure=Decimal("30"), component_exposure=Decimal("50"),
        global_exposure=Decimal("301"), bankroll=Decimal("1000"),
    )
    assert check.ok is False
    assert check.reason == "global_cap_exceeded"


def test_cap_check_lower_permission_cap_is_tighter():
    check = cap_check(
        market_exposure=Decimal("30"), component_exposure=Decimal("30"),
        global_exposure=Decimal("100"), bankroll=Decimal("1000"),
        per_market_cap=Decimal("0.02"),
    )
    assert check.ok is False
    assert check.reason == "per_market_cap_exceeded"


def test_cap_check_nonpositive_bankroll_rejected():
    with pytest.raises(ValueError, match="portfolio_bankroll_nonpositive"):
        cap_check(
            market_exposure=Decimal("0"), component_exposure=Decimal("0"),
            global_exposure=Decimal("0"), bankroll=Decimal("0"),
        )


# ---------------- marginal_log_growth_delta ----------------

def test_marginal_log_growth_delta_vs_no_action():
    delta = marginal_log_growth_delta(
        [{"w0": Decimal("0.6"), "w1": Decimal("0.4")}],
        {"w0": Decimal("100"), "w1": Decimal("-50")},
        bankroll=Decimal("100"),
        base_world_delta={"w0": Decimal("0"), "w1": Decimal("0")},
    )
    # candidate = 0.2*ln2 ≈ 0.138629；base = 0
    assert delta == Decimal("0.138629")


# ---------------- worst_loss_cvar ----------------

def test_worst_loss_cvar_min_expected_over_members():
    cvar = worst_loss_cvar(
        [
            {"w0": Decimal("0.6"), "w1": Decimal("0.4")},
            {"w0": Decimal("0.5"), "w1": Decimal("0.5")},
        ],
        {"w0": Decimal("100"), "w1": Decimal("-50")},
    )
    assert cvar == Decimal("25")  # min(40, 25)


def test_worst_loss_cvar_alpha_out_of_range_rejected():
    with pytest.raises(ValueError, match="portfolio_alpha_out_of_range"):
        worst_loss_cvar([{"w0": Decimal("1")}], {"w0": Decimal("0")}, alpha=Decimal("0"))
    with pytest.raises(ValueError, match="portfolio_alpha_out_of_range"):
        worst_loss_cvar([{"w0": Decimal("1")}], {"w0": Decimal("0")}, alpha=Decimal("1.1"))


def test_worst_loss_cvar_empty_u_rejected():
    with pytest.raises(ValueError, match="portfolio_u_empty"):
        worst_loss_cvar([], {"w0": Decimal("0")})
