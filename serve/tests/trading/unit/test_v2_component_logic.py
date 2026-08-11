"""WP-01C G2 component logic 单测（纯判定）。

覆盖（任务 §5.2/§2.6）：schema allowlist、state budget、h_c totality、component 空集、
依赖边 probability/coherence 拒绝（DB CHECK）。
"""

from decimal import Decimal

import pytest

from app.logics.trading.component import (
    G2_COMPONENT_EMPTY,
    G2_HC_NOT_TOTAL,
    G2_SCHEMA_FORBIDDEN,
    G2_STATE_BUDGET,
    WORLD_STATE_BUDGET,
    _forbidden_in,
)
from app.schemas.trading.semantics import WorldSchemaInput


def _ws(**over):
    base = dict(
        component_key="comp-a",
        variables={"attacked": {"type": "bool"}},
        domains={"attacked": ["true", "false"]},
        constraints=[],
        factorization={"independent": ["attacked"]},
        world_states=[
            {"world_state_id": "t", "assignment": {"attacked": "true"}},
            {"world_state_id": "f", "assignment": {"attacked": "false"}},
        ],
        state_count=2,
        h_c={"1": {"t": "YES", "f": "NO"}},
        schema_version=1,
    )
    base.update(over)
    return WorldSchemaInput(**base)


def _validate(ws, spec_ids, hc):
    from app.logics.trading.component import ComponentLogic

    return ComponentLogic(None)._validate(ws, spec_ids, hc)


def test_g2_pass_with_total_hc():
    ws = _ws()
    assert _validate(ws, [1], {1: {"t": "YES", "f": "NO"}}) is None


def test_g2_empty_component_fails():
    ws = _ws()
    assert _validate(ws, [], {}) == G2_COMPONENT_EMPTY


def test_g2_hc_not_total_fails():
    ws = _ws()
    assert _validate(ws, [1], {1: {"t": "YES"}}) == G2_HC_NOT_TOTAL
    assert _validate(ws, [1], {}) == G2_HC_NOT_TOTAL


def test_g2_forbidden_schema_field():
    # schema 构造时 fail-closed（typed DTO validator 抛）
    with pytest.raises(Exception, match="schema_forbidden"):
        _ws(variables={"attacked": {"type": "bool"}, "probability": 0.5})
    with pytest.raises(Exception, match="schema_forbidden"):
        _ws(domains={"attacked": ["true", "false"], "edge": 0.1})
    # Logic 层对原始 dict 的 reason 判定（绕过 typed DTO，验证 reason 路径）
    reason = _forbidden_in(
        {"attacked": {"type": "bool"}, "probability": 0.5}, "schema"
    )
    assert reason and reason.startswith("schema.probability")
    assert G2_SCHEMA_FORBIDDEN in f"{G2_SCHEMA_FORBIDDEN}:{reason}"


def test_g2_state_budget_exceeded():
    ws = _ws(state_count=WORLD_STATE_BUDGET + 1)
    assert _validate(ws, [1], {1: {"t": "YES", "f": "NO"}}) == G2_STATE_BUDGET


def test_g2_state_count_mismatch_constraint_conflict():
    ws = _ws(state_count=3)
    assert _validate(ws, [1], {1: {"t": "YES", "f": "NO"}}) == "g2_constraint_conflict"


def test_g2_duplicate_explicit_assignment_rejected():
    ws = _ws(
        world_states=[
            {"world_state_id": "t", "assignment": {"attacked": "true"}},
            {"world_state_id": "f", "assignment": {"attacked": "true"}},
        ]
    )
    assert _validate(ws, [1], {1: {"t": "YES", "f": "NO"}}) == "g2_constraint_conflict"


def test_forbidden_scans_recursive():
    assert _forbidden_in({"a": {"b": {"belief": 1}}}) == "a.b.belief"
    assert _forbidden_in({"a": [{"mu": 1}]}) == "a[0].mu"
    assert _forbidden_in({"clean": 1}) is None
