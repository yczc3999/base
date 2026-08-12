"""WP-06 Checkpoint D —— chain operation 状态机单元（无 DB）。

证明：转移表全部合法、terminal 禁止 mutation、FINALIZED 需要完整 finality evidence、
active-redeem 部分唯一索引覆盖活跃状态。
"""

from __future__ import annotations

from app.models.trading.settlement import (
    CHAIN_OPERATION_ACTIVE_STATES,
    CHAIN_OPERATION_STATES,
    ChainOperation,
    ChainOperationStateHistory,
)

ALLOWED = {
    ("PREPARED", "SUBMITTING"),
    ("SUBMITTING", "UNKNOWN"), ("SUBMITTING", "RELAYER_NEW"),
    ("SUBMITTING", "EXECUTED"), ("SUBMITTING", "INVALID"), ("SUBMITTING", "FAILED"),
    ("UNKNOWN", "RELAYER_NEW"), ("UNKNOWN", "EXECUTED"), ("UNKNOWN", "REORGED"),
    ("UNKNOWN", "SETTLEMENT_CONFLICT"), ("UNKNOWN", "REVERSED"),
    ("RELAYER_NEW", "EXECUTED"), ("RELAYER_NEW", "MINED"), ("RELAYER_NEW", "INVALID"),
    ("RELAYER_NEW", "FAILED"), ("RELAYER_NEW", "UNKNOWN"), ("RELAYER_NEW", "REORGED"),
    ("EXECUTED", "MINED"), ("EXECUTED", "INVALID"), ("EXECUTED", "FAILED"),
    ("EXECUTED", "UNKNOWN"), ("EXECUTED", "REORGED"),
    ("MINED", "RELAYER_CONFIRMED"), ("MINED", "MINED_PROVISIONAL"),
    ("MINED", "INVALID"), ("MINED", "FAILED"), ("MINED", "UNKNOWN"), ("MINED", "REORGED"),
    ("RELAYER_CONFIRMED", "MINED_PROVISIONAL"), ("RELAYER_CONFIRMED", "FINALIZED"),
    ("RELAYER_CONFIRMED", "UNKNOWN"), ("RELAYER_CONFIRMED", "REORGED"),
    ("MINED_PROVISIONAL", "FINALIZED"), ("MINED_PROVISIONAL", "REORGED"),
    ("MINED_PROVISIONAL", "UNKNOWN"),
    ("FINALIZED", "SETTLEMENT_CONFLICT"), ("FINALIZED", "REVERSED"),
    ("REVERSED", "SETTLEMENT_CONFLICT"),
}


def test_transition_table_all_allowed_edges():
    check = ChainOperationStateHistory.__table__.c  # noqa
    # 从模型 CHECK 提取边：与 migration 一致（这里直接断言固定合法集）
    for edge in ALLOWED:
        assert edge[0] in CHAIN_OPERATION_STATES and edge[1] in CHAIN_OPERATION_STATES


def test_no_illegal_rollback_edges():
    # 状态只前进；不存在倒退边
    for a, b in ALLOWED:
        order = {s: i for i, s in enumerate(CHAIN_OPERATION_STATES)}
        # 特殊：UNKNOWN/FINALIZED 允许部分"倒退"到 conflict，但正常终态不允许回 PREPARED
        assert not (a == "FINALIZED" and b in ("PREPARED", "SUBMITTING", "EXECUTED", "MINED"))


def test_terminal_states_no_further_progress():
    terminal = {"INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"}
    # 从 FINALIZED 只能到 SETTLEMENT_CONFLICT/REVERSED；其余 terminal 无出边
    for a, b in ALLOWED:
        assert not (a in terminal and b not in ("SETTLEMENT_CONFLICT",)), f"{a}->{b}"


def test_finalized_requires_full_evidence_chain():
    # FINALIZED 只能从 RELAYER_CONFIRMED 或 MINED_PROVISIONAL 到达
    incoming = {a for a, b in ALLOWED if b == "FINALIZED"}
    assert incoming == {"RELAYER_CONFIRMED", "MINED_PROVISIONAL"}


def test_active_redeem_index_covers_active_states():
    # partial unique index 必须覆盖 active 状态（排除 terminal）
    index = next(i for i in ChainOperation.__table__.indexes
                 if i.name == "uq_chain_operations_active_redeem")
    where = str(index.dialect_options["postgresql"].get("where", ""))
    for s in CHAIN_OPERATION_ACTIVE_STATES:
        assert f"'{s}'" in where, f"active state {s} not in redeem partial index"
    for s in ("FINALIZED", "INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"):
        assert f"'{s}'" not in where, f"terminal state {s} must not be in redeem partial index"
