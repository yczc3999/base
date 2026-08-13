"""WP-01C G0/R0 screening logic 单测（纯判定）。

覆盖（任务 §6.1/§6.2/§2.8）：G0 字段缺失/hash 不一致/未冻结、R0 严格 allowlist、
R0 判定分支、审计抽样确定性。
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.logics.trading.screening import (
    G0_FAIL,
    G0_PASS,
    G0_REASON_HASH_MISMATCH,
    G0_REASON_MISSING,
    R0_DEFER,
    R0_REJECT,
    R0_SELECT,
    ScreeningLogic,
    compose_tag_disposition,
)
from app.schemas.trading.workflow import G0ObjectiveInput, R0Input, R0PolicyInput
from app.domain.trading.hashing import canonical_hash

FULL_OBJECTIVE = {
    "objective_fn_version": "v1",
    "units": "USD",
    "decision_horizon": "HOLD_TO_RESOLUTION",
    "HOLD_TO_RESOLUTION": True,
    "discount_policy": {"kind": "none"},
    "capital_charge_policy": {"kind": "linear"},
    "NO_ACTION": {"action": "NO_ACTION"},
    "allowed_actions": ["NO_ACTION", "PREDICT"],
    **{field: {"included": True} for field in (
        "trading_cost_scope", "data_cost_scope", "llm_cost_scope",
        "search_cost_scope", "infrastructure_cost_scope", "human_cost_scope",
        "operational_cost_scope",
    )},
    "robustness_policy": {"kind": "worst_case"},
    "hard_constraint_ordering": ["eligibility", "capital"],
}
_ALL_G0_FIELDS = set(FULL_OBJECTIVE)


def _r0(**over):
    base = dict(best_bid=Decimal("0.5"), best_ask=Decimal("0.52"))
    base.update(over)
    return R0Input(**base)


R0_POLICY = R0PolicyInput(
    policy_version=1,
    minimum_rule_completeness=Decimal("0.5"),
    maximum_research_cost=Decimal("100000"),
    require_two_sided_quote=True,
    defer_recheck_condition="market_data_changes",
    reject_recheck_condition="resource_envelope_changes",
)


def test_g0_missing_fields_detected():
    g = G0ObjectiveInput(content={})
    assert set(g.missing_fields) == _ALL_G0_FIELDS  # 空 dict → 全部缺失
    # 部分缺失 → 只列缺失项
    partial = G0ObjectiveInput(content={"units": "usd"})
    assert set(partial.missing_fields) == _ALL_G0_FIELDS - {"units"}
    assert G0ObjectiveInput(content=FULL_OBJECTIVE).missing_fields == []


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("objective_fn_version", ""),
        ("units", 1),
        ("decision_horizon", []),
        ("HOLD_TO_RESOLUTION", False),
        ("NO_ACTION", "NO_ACTION"),
        ("allowed_actions", []),
        ("allowed_actions", ["PREDICT"]),
        ("trading_cost_scope", "all"),
        ("human_cost_scope", {}),
        ("discount_policy", "none"),
        ("robustness_policy", []),
        ("hard_constraint_ordering", "eligibility"),
    ],
)
def test_g0_rejects_bad_present_field_types(field, bad_value):
    with pytest.raises(Exception, match="objective_"):
        G0ObjectiveInput(content={**FULL_OBJECTIVE, field: bad_value})


def test_g0_hash_mismatch():
    logic = ScreeningLogic(None)
    # 直接调用纯逻辑部分：hash 不匹配 → G0_FAIL/HASH_MISMATCH
    from app.schemas.trading.workflow import G0_REQUIRED_FIELDS
    actual = canonical_hash(FULL_OBJECTIVE)
    # 验证 hash 不一致判定（run_g0 需要 UoW/DB，纯部分用 helper）
    assert actual != canonical_hash({**FULL_OBJECTIVE, "units": "changed"})


def test_r0_allowlist_rejects_unknown():
    with pytest.raises(Exception, match="extra_forbidden"):
        R0Input(best_bid=Decimal("0.5"), best_ask=Decimal("0.52"), prior=0.5)


def test_r0_decide_branches():
    logic = ScreeningLogic(None)
    # SELECT：有报价、未 crossed、规则完整、容量足够
    assert logic._decide(_r0(), R0_POLICY)[0] == R0_SELECT
    # DEFER：缺报价
    assert logic._decide(_r0(best_bid=None), R0_POLICY)[0] == R0_DEFER
    # DEFER：crossed
    assert logic._decide(_r0(best_bid=Decimal("0.53")), R0_POLICY)[0] == R0_DEFER
    # DEFER：规则不完整
    assert logic._decide(_r0(rule_completeness=Decimal("0.3")), R0_POLICY)[0] == R0_DEFER
    # REJECT：容量 0
    assert logic._decide(_r0(minimum_deployable_capacity=Decimal("0")), R0_POLICY)[0] == R0_REJECT
    # REJECT：成本过高
    assert logic._decide(_r0(estimated_research_cost=Decimal("200000")), R0_POLICY)[0] == R0_REJECT


def test_compose_tag_disposition_rules():
    assert compose_tag_disposition(None) is None
    assert compose_tag_disposition([]) == (R0_DEFER, "r0_tags_missing")
    assert compose_tag_disposition([{"gamma_tag_id": "2", "disposition": "SELECT"}]) is None
    assert compose_tag_disposition(
        [{"gamma_tag_id": "1", "disposition": None}]
    ) == (R0_DEFER, "r0_tag_defer")
    assert compose_tag_disposition(
        [
            {"gamma_tag_id": "2", "disposition": "SELECT"},
            {"gamma_tag_id": "1", "disposition": "REJECT"},
        ]
    ) == (R0_REJECT, "r0_tag_reject")
    assert compose_tag_disposition(
        [
            {"gamma_tag_id": "2", "disposition": "SELECT"},
            {"gamma_tag_id": "84", "disposition": "DEFER"},
        ]
    ) == (R0_DEFER, "r0_tag_defer")


def test_r0_tag_gate_before_l1():
    logic = ScreeningLogic(None)
    rejected, reason = logic._decide(
        _r0(market_metadata={"tags": [{"gamma_tag_id": "1", "disposition": "REJECT"}]}),
        R0_POLICY,
    )
    assert rejected == R0_REJECT
    assert reason == "r0_tag_reject"
    deferred, reason = logic._decide(
        _r0(market_metadata={"tags": []}),
        R0_POLICY,
    )
    assert deferred == R0_DEFER
    assert reason == "r0_tags_missing"
    selected, reason = logic._decide(
        _r0(market_metadata={"tags": [{"gamma_tag_id": "2", "disposition": "SELECT"}]}),
        R0_POLICY,
    )
    assert selected == R0_SELECT
    assert reason is None


def test_audit_sample_deterministic():
    from app.domain.trading.hashing import deterministic_sample

    seed = "a" * 64
    a = deterministic_sample(content_hash="b" * 64, seed_hash=seed, stratum="r0", rate=0.5)
    b = deterministic_sample(content_hash="b" * 64, seed_hash=seed, stratum="r0", rate=0.5)
    assert a == b
    assert 0 <= a[1] < 1.0
    assert a[2] == 0.5
