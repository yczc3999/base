"""WP-01C 状态机单测（架构 §1.1 / 任务 §7）。

- 合法/非法 Gate 顺序（越 Gate 拒绝）。
- episode key 确定性 + sorted spec-set 稳定。
- 硬约束：hard fail 后不得越 Gate。
"""

from datetime import datetime, timezone
from dataclasses import replace

import pytest

from app.orchestrator.trading_state_machine import (
    EpisodeKeyMaterial,
    IllegalTransitionError,
    TradingStateMachine,
    episode_key,
)
from app.domain.trading.gates import assert_frozen_gate_binding


def test_legal_forward_transitions():
    sm = TradingStateMachine(None)
    sm.assert_order("G0", "R0")
    sm.assert_order("R0", "G1")
    sm.assert_order("G1", "G2")
    sm.assert_order("G2", "R1")


@pytest.mark.parametrize("frm,to", [
    ("G0", "G1"), ("G0", "G2"), ("R0", "G2"), ("G1", "R1"), ("G2", "G0"),
])
def test_illegal_transitions_rejected(frm, to):
    sm = TradingStateMachine(None)
    with pytest.raises(IllegalTransitionError):
        sm.assert_order(frm, to)


def test_unknown_gate_rejected():
    sm = TradingStateMachine(None)
    with pytest.raises(IllegalTransitionError, match="unknown_gate"):
        sm.assert_order("G0", "G9")


def test_gate_binding_must_come_from_frozen_cohort():
    lineage = {
        "cohort_release_manifest_id": 7,
        "cohort_policy_hashes": {"r1": "a" * 64, "r0": "b" * 64},
    }
    assert_frozen_gate_binding(
        lineage, policy_type="r1", policy_hash="a" * 64,
        version_manifest_id=7
    )
    with pytest.raises(ValueError, match="release_binding"):
        assert_frozen_gate_binding(
            lineage, policy_type="r1", policy_hash="a" * 64,
            version_manifest_id=8
        )
    with pytest.raises(ValueError, match="policy_binding"):
        assert_frozen_gate_binding(
            lineage, policy_type="r1", policy_hash="b" * 64,
            version_manifest_id=7
        )
    with pytest.raises(ValueError, match="policy_binding"):
        assert_frozen_gate_binding(
            lineage, policy_type="taxonomy", policy_hash="a" * 64,
            version_manifest_id=7
        )


def test_episode_key_deterministic_and_spec_order_stable():
    now = datetime.now(timezone.utc)
    e1 = EpisodeKeyMaterial(
        opportunity_key="opp-a", component_version_hash="comp-a",
        strategy_hash="strat-a", objective_hash="obj-a", trigger="frame",
        cutoff_at=now, horizon="res", experiment_variant="control",
        spec_hashes=["h1", "h2", "h3"],
    )
    e2 = replace(e1, spec_hashes=["h3", "h1", "h2"])
    assert episode_key(e1) == episode_key(e2)
    assert len(episode_key(e1)) == 64


def test_episode_key_differs_on_any_input():
    now = datetime.now(timezone.utc)
    base = EpisodeKeyMaterial(
        opportunity_key="opp-a", component_version_hash="comp-a",
        strategy_hash="strat-a", objective_hash="obj-a", trigger="frame",
        cutoff_at=now, horizon="res", experiment_variant="control",
        spec_hashes=["h1", "h2"],
    )
    variants = [
        replace(base, opportunity_key="opp-B"),
        replace(base, component_version_hash="comp-B"),
        replace(base, strategy_hash="strat-B"),
        replace(base, trigger="ws"),
        replace(base, horizon="deep"),
        replace(base, spec_hashes=["h1", "h3"]),
    ]
    keys = {episode_key(base)} | {episode_key(v) for v in variants}
    assert len(keys) == 7  # 全部不同
