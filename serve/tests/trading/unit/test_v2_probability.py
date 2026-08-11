"""Deterministic Q/U coherence, push-forward, and Bernoulli p_blind（WP-02 Checkpoint A）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.trading.probability import (
    bernoulli_p_blind,
    expected_payout,
    normalize_q,
    payout_bounds,
    push_forward_mu,
    validate_u,
)


class TestNormalizeQ:
    def test_valid_total(self):
        assert normalize_q({"a": "0.3", "b": "0.7"}) == {
            "a": Decimal("0.3"), "b": Decimal("0.7")
        }

    def test_decimal_scale_insensitive(self):
        assert normalize_q({"a": "0.5", "b": "0.5"}) == {
            "a": Decimal("0.5"), "b": Decimal("0.5")
        }

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="q_empty"):
            normalize_q({})

    def test_float_rejected(self):
        with pytest.raises(ValueError, match=r"q\[a\]_float_forbidden"):
            normalize_q({"a": 0.5, "b": "0.5"})

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match=r"q\[a\]_bool_forbidden"):
            normalize_q({"a": True, "b": "0.0"})

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match=r"q_negative:a"):
            normalize_q({"a": "-0.1", "b": "1.1"})

    def test_not_total_rejected(self):
        with pytest.raises(ValueError, match="q_not_total"):
            normalize_q({"a": "0.5", "b": "0.4"})

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match=r"q\[a\]_not_finite"):
            normalize_q({"a": "NaN", "b": "1"})

    def test_near_total_tolerance(self):
        # 1e-12 容差内视为 total
        normalize_q({"a": "0.500000000000000000001", "b": "0.499999999999999999999"})


class TestValidateU:
    Q = {"a": "0.5", "b": "0.5"}
    U = [{"a": "0.5", "b": "0.5"}, {"a": "0.4", "b": "0.6"}]

    def test_valid_contains_q(self):
        q = normalize_q(self.Q)
        result = validate_u(self.U, q=q)
        assert len(result) == 2

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="u_empty"):
            validate_u([], q=normalize_q(self.Q))

    def test_q_not_in_u(self):
        q = normalize_q({"a": "0.9", "b": "0.1"})
        with pytest.raises(ValueError, match="u_must_contain_q"):
            validate_u(self.U, q=q)

    def test_duplicate_member_rejected(self):
        with pytest.raises(ValueError, match="u_duplicate_member"):
            validate_u(
                [{"a": "0.5", "b": "0.5"}, {"a": "0.5", "b": "0.5"}],
                q=normalize_q(self.Q),
            )

    def test_key_mismatch_rejected(self):
        with pytest.raises(ValueError, match="u_key_mismatch"):
            validate_u([{"a": "0.5", "c": "0.5"}], q=normalize_q(self.Q))

    def test_member_not_total_rejected(self):
        with pytest.raises(ValueError, match="q_not_total"):
            validate_u([{"a": "0.5", "b": "0.4"}], q=normalize_q(self.Q))


class TestPushForward:
    H_C = {"w0": "YES", "w1": "NO"}
    IR_YES = {"YES": "1", "NO": "0"}

    def test_bernoulli_yes(self):
        q = normalize_q({"w0": "0.6", "w1": "0.4"})
        mu = push_forward_mu(q, h_c=self.H_C, payout_ir=self.IR_YES)
        assert mu == {"1": Decimal("0.6"), "0": Decimal("0.4")}
        assert expected_payout(mu) == Decimal("0.6")

    def test_hc_not_total(self):
        q = normalize_q({"w0": "0.6", "w1": "0.4"})
        with pytest.raises(ValueError, match="h_c_not_total"):
            push_forward_mu(q, h_c={"w0": "YES"}, payout_ir=self.IR_YES)

    def test_payout_missing_state(self):
        q = normalize_q({"w0": "1", "w1": "0"})
        with pytest.raises(ValueError, match="payout_missing_state"):
            push_forward_mu(q, h_c=self.H_C, payout_ir={"YES": "1"})

    def test_bounds_order(self):
        q = normalize_q({"w0": "0.6", "w1": "0.4"})
        u = [
            normalize_q({"w0": "0.6", "w1": "0.4"}),
            normalize_q({"w0": "0.5", "w1": "0.5"}),
        ]
        lower, upper = payout_bounds(u, h_c=self.H_C, payout_ir=self.IR_YES)
        assert lower <= Decimal("0.6") <= upper
        assert lower == Decimal("0.5") and upper == Decimal("0.6")


class TestBernoulliPBlind:
    def test_yes_token(self):
        mu = {"1": Decimal("0.6"), "0": Decimal("0.4")}
        assert bernoulli_p_blind(mu) == Decimal("0.6")

    def test_no_token(self):
        mu = {"0": Decimal("0.6"), "1": Decimal("0.4")}
        assert bernoulli_p_blind(mu) == Decimal("0.4")

    def test_degenerate_all_zero(self):
        mu = {"0": Decimal("1")}
        assert bernoulli_p_blind(mu) == Decimal("0")

    def test_non_bernoulli_payouts(self):
        mu = {"0.5": Decimal("1")}
        assert bernoulli_p_blind(mu) is None

    def test_mixed_payouts(self):
        mu = {"1": Decimal("0.5"), "0.5": Decimal("0.5")}
        assert bernoulli_p_blind(mu) is None
