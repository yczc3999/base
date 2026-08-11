"""P3 scoring 纯函数单测（WP-04 Checkpoint A）。

覆盖 Bernoulli/multiclass/mean-only 的 golden 数值、冻结 epsilon 截断、calibration、
sharpness、tail loss、cluster bootstrap 确定性 + n_eff、properness guard、spec 自洽
（frozen_at 早于今天、content_hash 自洽、spec_policy_hashes 与冻结 manifest 快照一致）。
全部确定性 Decimal、快速（<10s）、不连 DB。
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.trading import (
    BERNOULLI_EPSILON,
    bernoulli_brier,
    bernoulli_log_loss,
    calibration_intercept_slope,
    cluster_bootstrap,
    delta_loss,
    expit,
    logit,
    mean_squared_payout_loss,
    multiclass_brier,
    multiclass_log_loss,
    n_eff,
    proper_loss_guard,
    sharpness,
    tail_loss,
)
from tests.trading.fixtures.p3_learning.p3_helpers import (
    frozen_scenario,
    frozen_spec,
    load_p3_spec,
    load_scenario,
    p3_spec_sha256,
    scenario_sha256,
    spec_policy_hashes,
)

D = Decimal

# 冻结 P_EVALUATION_SPEC_MANIFEST 快照：与 spec_policy_hashes() 必须逐位一致。
# 任何 spec policy 修改都会让这里失败（fixture 冻结语义）。
EXPECTED_POLICY_HASHES = {
    "label_policy_hash": "4ff6f251915007150ff8fb4a1558baeb885d1ed62d2c5a5fde8e31e614567197",
    "target_policy_hash": "bdcd2cf91793c8d41eee58df932bc7404dbdd1c5270d278a27fafdfd093c906a",
    "baseline_policy_hash": "670246d7e408e32f4442650e1eca928de484f242c67f5359dde212ecaff5bd9b",
    "split_policy_hash": "ddd31b216926db79d838e9f3129cf3ac4a3ef5c08038009f7973f443ef60b178",
    "bootstrap_policy_hash": "ba19e6fa54d73cb8f38f12fa750f4ea1871bb9ee8842678692d4fe2a409332fb",
    "metric_policy_hash": "9e4653f4a55ee719634d0398fc0007640451a9950ae48abf484d816d181db7a6",
    "promotion_policy_hash": "1acfc1d21b1b15c49677deede45477ba42dcb66211c38a2e8db6fb80aa0eb0e7",
}


def approx(value: Decimal, expected: Decimal, tol: Decimal = D("1e-12")) -> bool:
    return abs(value - expected) <= tol


# ---------------- Bernoulli ----------------

def test_bernoulli_brier_golden():
    assert bernoulli_brier(D("0.7"), 1) == D("0.09")
    assert bernoulli_brier(D("0.7"), 0) == D("0.49")
    assert bernoulli_brier(D("0.3"), 1) == D("0.49")


def test_bernoulli_log_loss_golden():
    # -ln(0.7) = 0.3566749439387324... → 12 位舍入
    assert bernoulli_log_loss(D("0.7"), 1) == D("0.356674943939")
    # -ln(0.3) = 1.2039728043259360... → 12 位舍入
    assert bernoulli_log_loss(D("0.7"), 0) == D("1.203972804326")


def test_bernoulli_log_loss_frozen_epsilon_clamps_extremes():
    # p=0 → clamp 到 epsilon=0.001；-ln(0.001) = 6.907755278982137...
    assert bernoulli_log_loss(D("0"), 1) == D("6.907755278982")
    # p=1, y=0 → -ln(1-p) = -ln(0.001)
    assert bernoulli_log_loss(D("1"), 0) == D("6.907755278982")
    # p=1, y=1 → -ln(0.999) = 0.0010005003335835... → 12 位
    assert bernoulli_log_loss(D("1"), 1) == D("0.001000500334")
    assert BERNOULLI_EPSILON == D("0.001")


def test_bernoulli_epsilon_matches_spec():
    spec = load_p3_spec()
    assert BERNOULLI_EPSILON == D(spec["metric_policy"]["bernoulli_epsilon"])


# ---------------- multiclass ----------------

def test_multiclass_brier_golden():
    assert multiclass_brier([D("0.6"), D("0.3"), D("0.1")], [1, 0, 0]) == D("0.26")


def test_multiclass_log_loss_golden():
    assert multiclass_log_loss([D("0.6"), D("0.3"), D("0.1")], [1, 0, 0]) == D(
        "0.510825623766"
    )


def test_multiclass_validation_fail_closed():
    with pytest.raises(ValueError, match="scoring_mc_length_mismatch"):
        multiclass_brier([D("0.5"), D("0.5")], [1, 0, 0])
    with pytest.raises(ValueError, match="scoring_mc_onehot_not_exactly_one"):
        multiclass_brier([D("0.5"), D("0.5")], [1, 1])
    with pytest.raises(ValueError, match="scoring_mc_probs_not_total"):
        multiclass_brier([D("0.6"), D("0.2"), D("0.1")], [1, 0, 0])


# ---------------- mean-only / delta ----------------

def test_mean_squared_payout_loss_golden():
    assert mean_squared_payout_loss(D("0.72"), D("0.60")) == D("0.0144")


def test_delta_loss_lower_is_better():
    assert delta_loss(D("0.30"), D("0.20")) == D("0.10")
    assert delta_loss(D("0.15"), D("0.25")) == D("-0.10")


# ---------------- calibration / sharpness / logit ----------------

def test_calibration_intercept_slope_golden():
    intercept, slope = calibration_intercept_slope([(D("0.5"), 0), (D("0.75"), 1)])
    # 过 (0,0) 与 (ln3,1) 的直线：slope = 1/ln(3) ≈ 0.910239226627，intercept = 0
    assert intercept == D("0")
    assert slope == D("0.910239226627")


def test_calibration_insufficient_or_zero_variance_returns_none():
    assert calibration_intercept_slope([(D("0.5"), 0)]) == (None, None)
    assert calibration_intercept_slope([]) == (None, None)
    assert calibration_intercept_slope([(D("0.5"), 0), (D("0.5"), 1)]) == (None, None)


def test_sharpness_golden():
    assert sharpness([D("0.7"), D("0.3")]) == D("0.21")
    assert sharpness([D("0.5"), D("0.5")]) == D("0.25")


def test_logit_expit_roundtrip():
    assert logit(D("0.5")) == D("0")
    assert approx(logit(D("0.7")), D("0.847297860387"), D("1e-12"))
    assert approx(expit(logit(D("0.7"))), D("0.7"), D("1e-12"))


# ---------------- tail loss / bootstrap / n_eff ----------------

def test_tail_loss_golden():
    losses = [D("0.1"), D("0.2"), D("0.3"), D("0.4"), D("0.5")]
    assert tail_loss(losses, D("0.6")) == D("0.45")   # 最差 2 个：mean(0.4,0.5)
    assert tail_loss(losses, D("0.8")) == D("0.5")    # 最差 1 个：max
    assert tail_loss(losses) == D("0.5")              # 默认 0.95 → 最差 1 个


def test_n_eff_kish_formula():
    assert n_eff([5, 5, 5], 15) == D("3")          # 等大 cluster → 集群数
    assert n_eff([1, 1, 1], 3) == D("3")
    assert n_eff([10], 10) == D("1")
    assert n_eff([9, 1], 10) == D("1.219512195122")  # 100/82
    with pytest.raises(ValueError, match="scoring_n_eff_total_mismatch"):
        n_eff([5, 5], 9)


def test_cluster_bootstrap_deterministic_and_n_eff():
    cluster_losses = [[D("0.1"), D("0.2")], [D("0.3"), D("0.4")], [D("0.5"), D("0.6")]]
    first = cluster_bootstrap(cluster_losses, seed=42)
    second = cluster_bootstrap(cluster_losses, seed=42)
    assert first == second  # 固定 seed 完全可复现
    assert first["n_clusters"] == 3
    assert first["n_eff"] == D("3")
    # cluster 均值 [0.15, 0.35, 0.55] → point = 0.35
    assert first["point"] == D("0.35")
    assert first["ci_low"] <= first["point"] <= first["ci_high"]
    assert first["time_blocks"] == 1
    other_seed = cluster_bootstrap(cluster_losses, seed=7)
    assert other_seed["point"] == first["point"]  # 点估计不依赖 seed
    assert first["ci_low"] >= D("0") and first["ci_high"] <= D("1")


# ---------------- properness / spec ----------------

def test_proper_loss_guard_only_final_admissible():
    assert proper_loss_guard("final_admissible") is True
    for state in ("pending", "provisional", "disputed", "final_excluded"):
        assert proper_loss_guard(state) is False


def test_spec_frozen_at_before_today_and_hash_self_consistent():
    spec = frozen_spec()
    frozen_at = datetime.fromisoformat(spec["frozen_at"].replace("Z", "+00:00"))
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert frozen_at < now
    assert spec["schema_version"] == "p3/evaluation-spec/v1"
    assert len(p3_spec_sha256()) == 64
    assert len(spec["content_hash"]) == 64


def test_spec_policy_hashes_match_frozen_manifest():
    actual = spec_policy_hashes()
    assert set(actual) == set(EXPECTED_POLICY_HASHES)
    for key, value in EXPECTED_POLICY_HASHES.items():
        assert actual[key] == value, f"{key} drifted from frozen manifest"


def test_scenarios_load_and_self_consistent():
    for name in ("bernoulli", "multiclass", "mean_only", "label_conflict",
                 "reject_audit", "holdout_tamper"):
        scenario = frozen_scenario(name)
        assert scenario["name"] == name
        assert len(scenario_sha256(name)) == 64
        assert len(scenario["content_hash"]) == 64


def test_scenario_golden_matches_functions():
    bernoulli = frozen_scenario("bernoulli")
    assert bernoulli_brier(
        D(bernoulli["blind_submission"]["bernoulli_p_yes"]), 1
    ) == D(bernoulli["golden"]["brier"])
    assert bernoulli_log_loss(
        D(bernoulli["blind_submission"]["bernoulli_p_yes"]), 1
    ) == D(bernoulli["golden"]["log_loss"])

    multiclass = frozen_scenario("multiclass")
    probs = [D(v) for v in multiclass["blind_submission"]["probs"]]
    one_hot = [int(v) for v in multiclass["one_hot"]]
    assert multiclass_brier(probs, one_hot) == D(multiclass["golden"]["multiclass_brier"])
    assert multiclass_log_loss(probs, one_hot) == D(
        multiclass["golden"]["multiclass_log_loss"]
    )

    mean_only = frozen_scenario("mean_only")
    assert mean_squared_payout_loss(
        D(mean_only["predicted_mean"]), D(mean_only["actual_mean"])
    ) == D(mean_only["golden"]["mean_squared_payout_loss"])

    reject_audit = frozen_scenario("reject_audit")
    assert load_scenario("reject_audit")["no_audit_reported_as"] == "unknown"


# ---------------- float fail-closed ----------------

def test_float_input_rejected():
    with pytest.raises(ValueError, match="scoring_brier_prob_float_forbidden"):
        bernoulli_brier(0.7, 1)
    with pytest.raises(ValueError, match="scoring_delta_candidate_float_forbidden"):
        delta_loss(0.1, D("0.2"))
    with pytest.raises(ValueError, match="scoring_mse_actual_float_forbidden"):
        mean_squared_payout_loss(D("0.7"), 0.6)
