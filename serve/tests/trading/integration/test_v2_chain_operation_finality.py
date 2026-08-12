"""WP-06 Checkpoint D —— chain operation finality（真 PostgreSQL + ChainSettlementLogic）。

证明：
- five-source exact set admissible 后 prepare_redeem 创建 REDEEM operation；
- 状态机 PREPARED→SUBMITTING→UNKNOWN→RELAYER_CONFIRMED→MINED_PROVISIONAL→FINALIZED
  由 CAS 触发推进，finality evidence 不全则拒绝；
- FINALIZED 在同一 UoW 写 ledger（balanced、per-asset 归零）+ effect 恰一次，
  重复/乱序 effect=0；
- MINED_PROVISIONAL（未 finalized）时 ledger/position=0。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.settlement import ChainSettlementLogic
from app.repositories.trading.settlement import ChainOperationRepository

from tests.trading.fixtures.p6_settlement.p6_helpers import frozen_fixture

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"

CHAIN_ID = 137
CONDITION = "0x" + "44" * 32
SNAPSHOT = 91842167

ADAPTER_CONTENT_HASH = next(
    e["hash"] for e in frozen_fixture("contract_registry")["entries"]
    if e["name"] == "ctf_adapter_standard"
)


def _upgrade(db_url):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, V52)
    finally:
        conn.close()
        engine.dispose()


def _async_url(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")


def _seed_registry(db_url):
    entry = next(
        e for e in frozen_fixture("contract_registry")["entries"]
        if e["name"] == "ctf_adapter_standard"
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            return c.execute(
                text(
                    "INSERT INTO trading.contract_registry "
                    "(registry_version, kind, version_no, chain_id, address, proxy_kind, "
                    " runtime_keccak, resolved_implementation_or_beacon, resolved_code_keccak, "
                    " snapshot_block_number, snapshot_block_hash, source_url, retrieved_at, "
                    " content_hash, extra, status) "
                    "VALUES (:rv, 'ctf_adapter_standard', 1, 137, :addr, 'beacon', :rk, "
                    " :resolved, :rk2, :snap, :bh, :src, :ret, :ch, :extra, 'ACTIVE') RETURNING id"
                ),
                {
                    "rv": entry["registry_version"], "addr": entry["address"],
                    "rk": entry["runtime_keccak"],
                    "resolved": entry["resolved_implementation_or_beacon"],
                    "rk2": entry["resolved_code_keccak"],
                    "snap": entry["snapshot_block_number"],
                    "bh": entry["snapshot_block_hash"], "src": entry["source_url"],
                    "ret": entry["retrieved_at"], "ch": entry["hash"],
                    "extra": __import__("json").dumps({
                        "beacon_address": entry["beacon_address"],
                        "beacon_implementation": entry["beacon_implementation"],
                        "beacon_runtime_keccak": entry["beacon_runtime_keccak"],
                        "beacon_implementation_code_keccak": entry["beacon_implementation_code_keccak"],
                    }),
                },
            ).scalar_one()
    finally:
        engine.dispose()


def _seed_observations(db_url, *, conflict: bool = False):
    """五元组 COMPLETE 观察（Standard YES，payout 1/1，winner YES）。"""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            specs = (
                ("gamma_clob_closed", {"payload": {"closed": True, "accepting_orders": False}}),
                ("ctf_payout", {"num": "1", "den": "1", "idx": "YES"}),
                ("data_api_redeemable", {"red": True}),
                ("clob_winner_5050", {"win": "YES"}),
                ("label_audit", {"lav": "p4/v1", "payload": {"status": "final_admissible", "resolution_state": "YES"}}),
            )
            for i, (kind, extra) in enumerate(specs):
                num = extra.get("num"); den = extra.get("den")
                win = extra.get("win"); red = extra.get("red"); idx = extra.get("idx")
                if conflict and kind == "clob_winner_5050":
                    win = "NO"  # winner 与 payout 冲突
                c.execute(
                    text(
                        "INSERT INTO trading.settlement_observations "
                        "(observation_key, source_kind, condition_id, token_set, outcome_index, "
                        " numerator, denominator, winner, is_50_50_outcome, redeemable, "
                        " label_audit_version, as_of, received_at, raw_artifact_ref, "
                        " raw_artifact_hash, content_hash, payload, status) "
                        "VALUES (:k, :kind, :cond, CAST(:ts AS jsonb), :idx, :num, :den, :win, :iso, "
                        " :red, :lav, :asof, :asof, :raw, :rawh, :ch, CAST(:payload AS jsonb), 'COMPLETE')"
                    ),
                    {
                        "k": f"obs-{i}", "kind": kind, "cond": CONDITION,
                        "ts": '["1","2"]', "idx": idx, "num": num, "den": den,
                        "win": win, "iso": False if kind == "clob_winner_5050" else None,
                        "red": red, "lav": extra.get("lav"),
                        "payload": __import__("json").dumps(extra.get("payload")) if extra.get("payload") else None,
                        "asof": datetime.fromisoformat("2026-08-11T00:00:00Z"),
                        "raw": f"raw-{i}", "rawh": f"{i:064x}", "ch": f"{i:064x}",
                    },
                )
    finally:
        engine.dispose()




def _seed_fk_chain(db_url):
    """seed operation 的 FK 依赖（显式 ID）：capital/release/pm_account。"""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text(
                "INSERT INTO trading.runtime_config_versions (id, config_key, version_no, content, "
                " schema_version, content_hash, status) VALUES (100, 'cfg', 1, '{}'::jsonb, 1, "
                " repeat('a',64), 'active')"
            ))
            c.execute(text(
                "INSERT INTO trading.strategy_versions (id, strategy_key, version_no, content, "
                " schema_version, content_hash, status) VALUES (101, 'strat', 1, '{}'::jsonb, 1, "
                " repeat('b',64), 'active')"
            ))
            c.execute(text(
                "INSERT INTO trading.execution_spec_versions (id, spec_key, version_no, content, "
                " schema_version, content_hash, status) VALUES (102, 'spec', 1, '{}'::jsonb, 1, "
                " repeat('c',64), 'active')"
            ))
            c.execute(text(
                "INSERT INTO trading.capital_permission_manifests (id, name, mode, capability, limits, "
                " evaluation_capital, authorized_capital, kill_switch, content_hash, status) "
                "VALUES (900002, 'wp06-test', 'shadow', '{}'::jsonb, '{}'::jsonb, 0, 0, false, "
                " repeat('d',64), 'active')"
            ))
            c.execute(text(
                "INSERT INTO trading.release_manifests (id, release_name, config_version_id, "
                " strategy_version_id, execution_spec_version_id, capital_permission_manifest_id, "
                " git_sha, image_digest, db_revision, total_hash, status) "
                "VALUES (900001, 'wp06-test', 100, 101, 102, 900002, repeat('e',64), 'img:wp06', "
                " 'b1000052', repeat('f',64), 'active')"
            ))
            c.execute(text(
                "INSERT INTO trading.pm_accounts (id, account_key, provider, chain_id, identity_type, "
                " funder_address, maker_address, signing_identity, wallet_type, signature_type, "
                " release_manifest_id, capital_permission_manifest_id, network_mode) "
                "VALUES (1, 'wp06-account', 'polymarket', 137, 'FIXTURE_ONLY', "
                " ('0x'::text || repeat('11',20)), ('0x'::text || repeat('11',20)),  ('0x'::text || repeat('22',20)), "
                " 'deposit_wallet', '3', 900001, 900002, 'fixture')"
            ))
    finally:
        engine.dispose()

def _sessions(db_url):
    async_engine = create_async_engine(_async_url(db_url), pool_size=2, max_overflow=0)
    return async_sessionmaker(async_engine, expire_on_commit=False), async_engine


async def _walk_to_finalized(uow, op_id: int, chain_ops: ChainOperationRepository,
                             *, fence: int = 1) -> None:
    seq = 0

    def ev(from_, to):
        nonlocal seq
        event = {
            "operation_id": op_id, "sequence_no": seq, "transition_from": from_,
            "transition_to": to, "event_type": to, "event_payload": {},
            "event_hash": f"{seq + 1:064d}", "fence_token": fence,
        }
        seq += 1
        return event

    await chain_ops.append_state_event(uow.session, ev("PREPARED", "SUBMITTING"))
    await chain_ops.update_evidence(uow.session, op_id, {
        "relayer_nonce": "42", "deadline": datetime(2026, 8, 11, 1, 0, tzinfo=timezone.utc),
    })
    await chain_ops.append_state_event(uow.session, ev("SUBMITTING", "RELAYER_NEW"))
    await chain_ops.append_state_event(uow.session, ev("RELAYER_NEW", "EXECUTED"))
    await chain_ops.update_evidence(uow.session, op_id, {
        "transaction_id": "tx-final-1", "transaction_hash": "0x" + "12" * 32,
    })
    await chain_ops.append_state_event(uow.session, ev("EXECUTED", "MINED"))
    await chain_ops.append_state_event(uow.session, ev("MINED", "RELAYER_CONFIRMED"))
    await chain_ops.update_evidence(uow.session, op_id, {
        "receipt_block_number": SNAPSHOT - 32, "receipt_block_hash": "0x" + "ef" * 32,
    })
    await chain_ops.append_state_event(uow.session, ev("RELAYER_CONFIRMED", "MINED_PROVISIONAL"))
    await chain_ops.update_evidence(uow.session, op_id, {
        "finalized_block_number": SNAPSHOT + 64,
        "pre_balance": {"pusd": "1000000", "token": "500000"},
        "post_balance": {"pusd": "1500000", "token": "0"},
    })
    await chain_ops.append_state_event(uow.session, ev("MINED_PROVISIONAL", "FINALIZED"))


def _query(db_url, sql, params=None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).fetchall()
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_finality_ledger_effect_exactly_once_and_balanced(temp_pg_db):
    url = temp_pg_db.url
    _upgrade(url)
    _seed_fk_chain(url)
    _seed_registry(url)
    _seed_observations(url)
    sessions, async_engine = _sessions(url)

    logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(),
                                 chain_id=CHAIN_ID)
    chain_ops = ChainOperationRepository()
    try:
        async with UnitOfWork(sessions) as uow:
            prepared = await logic.prepare_redeem(
                uow, operation_key="op-final-1", idempotency_key="idem-final-1",
                account_id=1, wallet_address="0x" + "11" * 20, condition_id=CONDITION,
                market_id=None, neg_risk=False,
                registry_content_hash=ADAPTER_CONTENT_HASH, permission_ref="perm/v1",
                release_manifest_id=900001, capital_permission_manifest_id=900002,
                fencing_token=1,
            )
            op_id = prepared.operation_id
            await _walk_to_finalized(uow, op_id, chain_ops)
            result = await logic.apply_finality(uow, op_id, winning_token_id=None)
        applied = result["applied"]
    finally:
        await async_engine.dispose()
    assert applied is True

    rows = _query(url,
        "SELECT asset_type, asset_key, amount FROM trading.ledger_postings "
        "WHERE transaction_id = (SELECT id FROM trading.ledger_transactions "
        " WHERE kind='SETTLEMENT' AND chain_operation_id=:oid) ORDER BY posting_no",
        {"oid": op_id})
    assert len(rows) == 4
    by_asset: dict = {}
    for asset_type, asset_key, amount in rows:
        by_asset[(asset_type, asset_key)] = by_asset.get((asset_type, asset_key), 0) + int(amount)
    for key, total in by_asset.items():
        assert total == 0, f"asset {key} not balanced: {total}"
    op = _query(url, "SELECT economic_effect_applied FROM trading.chain_operations WHERE id=:oid",
                {"oid": op_id})
    assert op == [(True,)]

    # 重复 apply → 无二次 effect
    sessions2, async_engine2 = _sessions(url)
    try:
        async with UnitOfWork(sessions2) as uow2:
            result2 = await logic.apply_finality(uow2, op_id)
            assert result2["applied"] is False
    finally:
        await async_engine2.dispose()
    count = _query(url, "SELECT count(*) FROM trading.ledger_transactions WHERE kind='SETTLEMENT' AND chain_operation_id=:oid",
                   {"oid": op_id})
    assert count == [(1,)]


@pytest.mark.anyio
async def test_mining_provisional_has_no_ledger_effect(temp_pg_db):
    """RELAYER_CONFIRMED + receipt 已 mined 但未 finalized → ledger/position=0。"""
    url = temp_pg_db.url
    _upgrade(url)
    _seed_fk_chain(url)
    _seed_registry(url)
    _seed_observations(url)
    sessions, async_engine = _sessions(url)
    logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(),
                                 chain_id=CHAIN_ID)
    chain_ops = ChainOperationRepository()
    applied_early = False
    try:
        async with UnitOfWork(sessions) as uow:
            prepared = await logic.prepare_redeem(
                uow, operation_key="op-provisional", idempotency_key="idem-prov",
                account_id=1, wallet_address="0x" + "11" * 20, condition_id=CONDITION,
                market_id=None, neg_risk=False,
                registry_content_hash=ADAPTER_CONTENT_HASH, permission_ref="perm/v1",
                release_manifest_id=900001, capital_permission_manifest_id=900002,
                fencing_token=1,
            )
            op_id = prepared.operation_id
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 0, "transition_from": "PREPARED",
                "transition_to": "SUBMITTING", "event_type": "SUBMITTING",
                "event_payload": {}, "event_hash": "0" * 64, "fence_token": 1,
            })
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 1, "transition_from": "SUBMITTING",
                "transition_to": "RELAYER_NEW", "event_type": "RELAYER_NEW",
                "event_payload": {}, "event_hash": "1" * 64, "fence_token": 1,
            })
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 2, "transition_from": "RELAYER_NEW",
                "transition_to": "EXECUTED", "event_type": "EXECUTED",
                "event_payload": {}, "event_hash": "2" * 64, "fence_token": 1,
            })
            await chain_ops.update_evidence(uow.session, op_id, {
                "transaction_id": "tx-prov", "transaction_hash": "0x" + "22" * 32,
            })
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 3, "transition_from": "EXECUTED",
                "transition_to": "MINED", "event_type": "MINED",
                "event_payload": {}, "event_hash": "3" * 64, "fence_token": 1,
            })
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 4, "transition_from": "MINED",
                "transition_to": "RELAYER_CONFIRMED", "event_type": "RELAYER_CONFIRMED",
                "event_payload": {}, "event_hash": "4" * 64, "fence_token": 1,
            })
            await chain_ops.update_evidence(uow.session, op_id, {
                "receipt_block_number": SNAPSHOT - 32, "receipt_block_hash": "0x" + "ef" * 32,
            })
            await chain_ops.append_state_event(uow.session, {
                "operation_id": op_id, "sequence_no": 5, "transition_from": "RELAYER_CONFIRMED",
                "transition_to": "MINED_PROVISIONAL", "event_type": "MINED_PROVISIONAL",
                "event_payload": {}, "event_hash": "5" * 64, "fence_token": 1,
            })
            try:
                await logic.apply_finality(uow, op_id)
                applied_early = True
            except RuntimeError as exc:
                assert "not_finalized" in str(exc)
    finally:
        await async_engine.dispose()
    assert applied_early is False
    count = _query(url, "SELECT count(*) FROM trading.ledger_transactions WHERE kind='SETTLEMENT'")
    assert count == [(0,)]


@pytest.mark.anyio
async def test_settlement_conflict_blocks_redeem(temp_pg_db):
    """winner/payout 冲突 → SETTLEMENT_CONFLICT，prepare_redeem 拒绝，无任何 operation。"""
    url = temp_pg_db.url
    _upgrade(url)
    _seed_fk_chain(url)
    _seed_registry(url)
    _seed_observations(url, conflict=True)
    sessions, async_engine = _sessions(url)
    logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(),
                                 chain_id=CHAIN_ID)
    try:
        async with UnitOfWork(sessions) as uow:
            assessment = await logic.assess_settlement(uow, CONDITION)
            assert assessment.admissible is False
            assert assessment.conflict_reason == "payout_winner_conflict"
            with pytest.raises(RuntimeError, match="not_admissible"):
                await logic.prepare_redeem(
                    uow, operation_key="op-conflict", idempotency_key="idem-conflict",
                    account_id=1, wallet_address="0x" + "11" * 20, condition_id=CONDITION,
                    market_id=None, neg_risk=False,
                    registry_content_hash=ADAPTER_CONTENT_HASH, permission_ref="perm/v1",
                    release_manifest_id=900001, capital_permission_manifest_id=900002,
                    fencing_token=1,
                )
    finally:
        await async_engine.dispose()
    count = _query(url, "SELECT count(*) FROM trading.chain_operations")
    assert count == [(0,)]
