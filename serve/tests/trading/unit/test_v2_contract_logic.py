"""WP-01C G1 contract logic 单测（纯判定 + typed DTO）。

覆盖（任务 §5.1/§2.2）：typed candidate 校验、G1 fail-closed 分支、5 类场景 PASS、
歧义/非 total/错误 token/非法 UNKNOWN 反例、hash 确定性、schema allowlist 认知字段拒绝。
"""

from decimal import Decimal

import pytest

from app.logics.trading.contract import (
    G1_AMBIGUOUS_RESOLUTION,
    G1_CLARIFICATION_MISSING,
    G1_MISSING_RULES,
    G1_PAYOUT_INCOMPLETE,
    _g1_validate,
    spec_canonical_content,
)
from app.schemas.trading.semantics import (
    SCHEMA_FORBIDDEN_KEYS,
    ContractSpecInput,
    PayoutIRInput,
    WorldSchemaInput,
)
from app.domain.trading.hashing import canonical_hash
from tests.trading.fixtures.p1a_fixtures import ALL_SCENARIOS


def _candidate(scenario, *, rules="rules", resolution_source="gamma", clarification="clar", **over):
    payouts = []
    idx = 0
    for token_key, spec in scenario["tokens"].items():
        token_version_id = spec["outcome_index"] + 101
        payouts.append(
            PayoutIRInput(
                token_key=token_key,
                pm_token_id=idx + 1,
                token_version_id=token_version_id,
                outcome_index=spec["outcome_index"],
                function_ir=spec["payout"],
            )
        )
        idx += 1
    base = dict(
        contract_key=f"c-{scenario['name']}",
        market_version_id=1,
        yes_token_version_id=101,
        no_token_version_id=102,
        artifact_object_id=1,
        resolution_states=scenario["resolution_states"],
        compiler_version="lookup/v1",
        schema_version=1,
        rules=rules,
        resolution_source=resolution_source,
        clarification=clarification,
        payouts=payouts,
    )
    base.update(over)
    return ContractSpecInput(**base)


@pytest.mark.parametrize("name,scenario", sorted(ALL_SCENARIOS.items()))
def test_g1_pass_all_scenarios(name, scenario):
    cand = _candidate(scenario)
    assert _g1_validate(cand) is None


def test_g1_fail_missing_rules():
    cand = _candidate(ALL_SCENARIOS["bernoulli"], rules=None)
    assert _g1_validate(cand) == G1_MISSING_RULES


def test_g1_fail_missing_resolution_source():
    cand = _candidate(ALL_SCENARIOS["bernoulli"], resolution_source=None)
    assert _g1_validate(cand) == G1_AMBIGUOUS_RESOLUTION


def test_g1_simple_rules_do_not_require_clarification():
    cand = _candidate(ALL_SCENARIOS["bernoulli"], clarification=None)
    assert _g1_validate(cand) is None


def test_g1_fail_missing_critical_clarification():
    cand = _candidate(
        ALL_SCENARIOS["bernoulli"],
        clarification=None,
        clarification_required=True,
    )
    assert _g1_validate(cand) == G1_CLARIFICATION_MISSING


def test_g1_fail_non_total_payout():
    """非 total：payout key 集 ≠ R_c → G1 fail（任务 §5.1）。"""
    # 用反例构造缺 key 的 payout
    bernoulli = ALL_SCENARIOS["bernoulli"]
    payouts = [
        PayoutIRInput(token_key="yes", pm_token_id=1, token_version_id=101, outcome_index=0,
                      function_ir={"YES": "1"}),  # 缺 NO
        PayoutIRInput(token_key="no", pm_token_id=2, token_version_id=102, outcome_index=1,
                      function_ir={"YES": "0", "NO": "1"}),
    ]
    cand = ContractSpecInput(
        contract_key="c-bad", market_version_id=1, yes_token_version_id=101, no_token_version_id=102,
        artifact_object_id=1, resolution_states=["YES", "NO"], compiler_version="v1",
        schema_version=1, rules="r", resolution_source="g", clarification="c", payouts=payouts,
    )
    assert _g1_validate(cand) == G1_PAYOUT_INCOMPLETE


def test_g1_fail_unknown_terminal_in_payout():
    """非法 UNKNOWN 终态 → typed schema 拒绝。"""
    with pytest.raises(Exception, match="unknown_terminal"):
        _candidate(
            ALL_SCENARIOS["bernoulli"],
            payouts=[
                PayoutIRInput(token_key="yes", pm_token_id=1, token_version_id=1, outcome_index=0,
                              function_ir={"YES": "1", "NO": "0", "UNKNOWN": "0"}),
                PayoutIRInput(token_key="no", pm_token_id=2, token_version_id=1, outcome_index=1,
                              function_ir={"YES": "0", "NO": "1"}),
            ],
        )


def test_schema_allowlist_rejects_cognition_fields():
    for field in SCHEMA_FORBIDDEN_KEYS:
        with pytest.raises(Exception, match="schema_forbidden"):
            WorldSchemaInput(
                component_key="c", variables={field: 0.5}, domains={},
                constraints=[], factorization={},
                world_states=[{"world_state_id": "s0", "assignment": {}}], state_count=1,
                h_c={}, schema_version=1,
            )


def test_schema_accepts_finite_variables():
    ws = WorldSchemaInput(
        component_key="c",
        variables={"attacked": {"type": "bool"}},
        domains={"attacked": ["true", "false"]},
        constraints=[],
        factorization={"independent": ["attacked"]},
        world_states=[
            {"world_state_id": "attacked-true", "assignment": {"attacked": "true"}},
            {"world_state_id": "attacked-false", "assignment": {"attacked": "false"}},
        ],
        state_count=2,
        h_c={"1": {"attacked-true": "YES", "attacked-false": "NO"}},
        schema_version=1,
    )
    assert ws.state_count == 2


def test_spec_canonical_content_stable():
    a = spec_canonical_content(
        contract_key="c1", resolution_states=["NO", "YES"], token_ids={0: "1", 1: "2"},
        payout_irs={"1": {"YES": "1", "NO": "0"}},
    )
    b = spec_canonical_content(
        contract_key="c1", resolution_states=["YES", "NO"], token_ids={1: "2", 0: "1"},
        payout_irs={"1": {"NO": "0", "YES": "1"}},
    )
    assert canonical_hash(a) == canonical_hash(b)
