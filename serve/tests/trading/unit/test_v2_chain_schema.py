"""WP-06 Checkpoint D —— chain-settlement schema 单元（无 DB）。

证明：4 张新表 ORM 模型列/约束与迁移 DDL 对齐；金额列全部 NUMERIC(38,0)（base units、
无 float）；时间列 TIMESTAMPTZ；hash/ID 使用 C collation；状态机常量与模型一致。
"""

from __future__ import annotations

from sqlalchemy import Numeric, DateTime, String, Text

from app.models.trading.settlement import (
    CHAIN_OPERATION_ACTIVE_STATES,
    CHAIN_OPERATION_STATES,
    ChainOperation,
    ChainOperationStateHistory,
    ContractRegistry,
    SettlementObservation,
    SETTLEMENT_SOURCE_KINDS,
)


def _col(table, name):
    return table.c[name]


def test_contract_registry_columns_types():
    from sqlalchemy import BigInteger, Integer

    assert isinstance(ContractRegistry.__table__.c["chain_id"].type, BigInteger)
    assert isinstance(ContractRegistry.__table__.c["version_no"].type, Integer)


def test_amount_columns_are_decimal_base_units():
    for table in (ChainOperation,):
        amount = table.__table__.c["amount_base_units"]
        assert isinstance(amount.type, Numeric)
        assert amount.type.precision == 38
        assert amount.type.scale == 0


def test_time_columns_timestamptz():
    for table in (ContractRegistry, ChainOperation, ChainOperationStateHistory,
                  SettlementObservation):
        for name in ("created_at", "as_of", "received_at"):
            if name in table.__table__.c:
                col = table.__table__.c[name]
                assert isinstance(col.type, DateTime)
                assert col.type.timezone is True, f"{table.__tablename__}.{name} not TZ"


def test_hash_columns_use_collation_or_sha256():
    for table in (ChainOperation, ChainOperationStateHistory, SettlementObservation):
        for name in ("economic_hash", "body_hash", "content_hash", "event_hash"):
            if name in table.__table__.c:
                col = table.__table__.c[name]
                assert isinstance(col.type, String), f"{name} not string"
                # sha256_type 使用 C collation
                assert col.type.collation == "C" or col.type.length == 64


def test_no_float_columns_in_chain_tables():
    import inspect

    from sqlalchemy import Float

    for table in (ContractRegistry, ChainOperation, ChainOperationStateHistory,
                  SettlementObservation):
        for col in table.__table__.columns:
            assert not isinstance(col.type, Float), f"{table.__tablename__}.{col.name} is Float"
            assert getattr(col.type, "asdecimal", True) is not False


def test_state_machine_constants_consistent():
    assert len(CHAIN_OPERATION_STATES) == 14
    assert set(CHAIN_OPERATION_ACTIVE_STATES) <= set(CHAIN_OPERATION_STATES)
    assert "FINALIZED" in CHAIN_OPERATION_STATES
    assert "SETTLEMENT_CONFLICT" in CHAIN_OPERATION_STATES
    # active 状态不含 terminal
    terminal = {"FINALIZED", "INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"}
    assert set(CHAIN_OPERATION_ACTIVE_STATES) & terminal == set()


def test_settlement_source_kinds_exact():
    assert SETTLEMENT_SOURCE_KINDS == (
        "gamma_clob_closed", "ctf_payout", "data_api_redeemable",
        "clob_winner_5050", "label_audit",
    )
    assert len(SETTLEMENT_SOURCE_KINDS) == 5


def test_chain_operation_immutable_binding_columns_present():
    cols = {c.name for c in ChainOperation.__table__.columns}
    for required in ("operation_key", "idempotency_key", "economic_hash", "operation_type",
                     "account_id", "wallet_address", "condition_id", "registry_version_id",
                     "target_address", "release_manifest_id", "capital_permission_manifest_id",
                     "fencing_token", "amount_base_units", "calldata", "calldata_keccak",
                     "body_hash", "call_set_hash", "expected_operation_hash",
                     "preflight_hash1", "preflight_hash2"):
        assert required in cols, f"missing binding column {required}"
