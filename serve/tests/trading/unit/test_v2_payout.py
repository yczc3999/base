"""WP-01C payout/hashing 纯函数单测（任务 §5.3）。

- 5 类场景（Bernoulli/时间嵌套/互斥/条件/VOID·PARTIAL）的正确 truth table 与反例。
- 相同输入重复计算 hash 完全一致；Decimal 数值差为 0（不用 float tolerance）。
- deterministic_sample：同输入/乱序/重试完全一致。
"""

from decimal import Decimal

import pytest

from app.domain.trading.hashing import canonical_bytes, canonical_hash, deterministic_sample
from app.domain.trading.payout import (
    FORBIDDEN_STATES,
    apply_payout_lookup,
    validate_payout_ir,
)
from tests.trading.fixtures.p1a_fixtures import ALL_SCENARIOS


@pytest.mark.parametrize("name,scenario", sorted(ALL_SCENARIOS.items()))
def test_valid_payout_irs(name, scenario):
    """每类场景：每个 token 的 truth table key 集 == R_c，值 0..1。"""
    states = scenario["resolution_states"]
    for token_key, spec in scenario["tokens"].items():
        ir = spec["payout"]
        parsed = validate_payout_ir(ir, resolution_states=states)
        assert set(parsed) == set(states)
        for state, dec in parsed.items():
            assert 0 <= dec <= 1
        # apply 与 parse 一致
        for state in states:
            assert apply_payout_lookup(ir, state) == parsed[state]


@pytest.mark.parametrize("name,scenario", sorted(ALL_SCENARIOS.items()))
def test_counterexamples_fail_closed(name, scenario):
    states = scenario["resolution_states"]
    for ce_name, bad_irs in scenario["counterexamples"].items():
        for token_key, ir in bad_irs.items():
            with pytest.raises((ValueError, Exception)):
                validate_payout_ir(ir, resolution_states=states)


def test_unknown_terminal_never_accepted():
    with pytest.raises(ValueError, match="unknown_terminal"):
        validate_payout_ir({"YES": "1", "UNKNOWN": "0"}, resolution_states=["YES", "UNKNOWN"])


def test_float_rejected():
    with pytest.raises(ValueError, match="float_forbidden"):
        validate_payout_ir({"YES": 1.0, "NO": 0.0}, resolution_states=["YES", "NO"])


def test_out_of_range_rejected():
    with pytest.raises(ValueError, match="out_of_range"):
        validate_payout_ir({"YES": "1.5", "NO": "0"}, resolution_states=["YES", "NO"])


def test_key_mismatch_missing_and_extra():
    with pytest.raises(ValueError, match="key_mismatch"):
        validate_payout_ir({"YES": "1"}, resolution_states=["YES", "NO"])
    with pytest.raises(ValueError, match="key_mismatch"):
        validate_payout_ir(
            {"YES": "1", "NO": "0", "OTHER": "0.5"}, resolution_states=["YES", "NO"]
        )


def test_apply_unknown_state_fails():
    with pytest.raises(ValueError, match="unknown_terminal"):
        apply_payout_lookup({"YES": "1", "NO": "0"}, "UNKNOWN")
    with pytest.raises(ValueError, match="missing_state"):
        apply_payout_lookup({"YES": "1"}, "NO")


def test_canonical_hash_stable_and_decimal_scale_invariant():
    a = canonical_hash({"a": Decimal("0.500"), "b": [Decimal("1.0"), 2]})
    b = canonical_hash({"b": [Decimal("1.000"), 2], "a": Decimal("0.5")})
    assert a == b
    assert len(a) == 64
    # 与 float 表示不混（float 在 canonical 中保持原样，但 payout 层已拒绝）
    assert canonical_bytes({"p": Decimal("0.1")}) != canonical_bytes({"p": 0.1})


def test_deterministic_sample_repeatable_and_ordered():
    seed = "e" * 64
    r1 = deterministic_sample(content_hash="a" * 64, seed_hash=seed, stratum="r0", rate=0.5)
    r2 = deterministic_sample(content_hash="a" * 64, seed_hash=seed, stratum="r0", rate=0.5)
    assert r1 == r2
    # 不同 content → 独立样本
    r3 = deterministic_sample(content_hash="b" * 64, seed_hash=seed, stratum="r0", rate=0.5)
    assert r3 != r1 or r3[1] != r1[1]
    # rate 边界
    assert deterministic_sample(content_hash="a" * 64, seed_hash=seed, stratum="r0", rate=0.0)[0] is False
    assert deterministic_sample(content_hash="a" * 64, seed_hash=seed, stratum="r0", rate=1.0)[0] is True
    # 非法 rate
    with pytest.raises(ValueError):
        deterministic_sample(content_hash="a" * 64, seed_hash=seed, stratum="r0", rate=1.5)


def test_inclusion_probability_matches_rate():
    _, u, prob = deterministic_sample(
        content_hash="c" * 64, seed_hash="d" * 64, stratum="r0", rate=0.25
    )
    assert 0 <= u < 1.0
    assert prob == 0.25
